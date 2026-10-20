"""Image processing for film scans. Pure numpy/OpenCV, no UI, so it is easy to test."""
from dataclasses import dataclass

import cv2
import numpy as np

FILM_COLOR_NEGATIVE = "Color negative"
FILM_BW_NEGATIVE = "B&W negative"
FILM_POSITIVE = "Positive slide"
FILM_TYPES = (FILM_COLOR_NEGATIVE, FILM_BW_NEGATIVE, FILM_POSITIVE)


@dataclass(frozen=True)
class Settings:
    film: str = FILM_COLOR_NEGATIVE
    auto_levels: bool = True
    neutralise: bool = True    # match the three channels through the midtones
    exposure_ev: float = 0.0   # -2.0 .. +2.0 stops
    contrast: float = 0.0      # -1.0 .. +1.0
    gamma: float = 1.0         # 0.4 .. 2.5
    saturation: float = 1.0    # 0.0 grey .. 1.0 unchanged .. 2.0 double


def invert(img):
    return 255 - img


def to_mono_bgr(img):
    """Collapse to grey and keep three channels so the rest of the pipeline is unchanged."""
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)


def _sample(img, step=4):
    """A decimated view for percentiles. On a 4.4 MP frame this is ~16x cheaper and as accurate."""
    return img[::step, ::step].reshape(-1, img.shape[2])


def auto_levels(img, low_pct=0.5, high_pct=99.5):
    """Stretch each channel so its darkest and brightest 0.5% map to black and white."""
    lo = np.percentile(_sample(img), low_pct, axis=0)
    hi = np.percentile(_sample(img), high_pct, axis=0)
    span = np.maximum(hi - lo, 1.0)
    out = (img.astype(np.float32) - lo) * (255.0 / span)
    return np.clip(out, 0, 255).astype(np.uint8)


def negative_to_positive(img, base_pct=0.3, shoulder_pct=0.2):
    """Invert a colour negative in density space, which is where film actually behaves linearly.

    A negative's orange mask is a dye layer in front of everything else, so in transmittance it is
    a per-channel *multiplier* and inverting linearly leaves a cast that no amount of channel
    stretching removes cleanly. Taking -log10 turns it into a per-channel offset, and subtracting
    each channel's own film-base density removes the mask exactly.

    `base_pct` is the fraction of pixels allowed to be lighter than the film base, and
    `shoulder_pct` the fraction allowed to clip at white.
    """
    linear = np.maximum(img.astype(np.float32), 1.0) * (1.0 / 255.0)
    density = -np.log10(linear)
    sample = _sample(density)
    base = np.percentile(sample, base_pct, axis=0)             # clear base: least dense
    shoulder = np.percentile(sample, 100.0 - shoulder_pct, axis=0)
    scaled = (density - base) / np.maximum(shoulder - base, 1e-6)
    return np.clip(scaled * 255.0, 0, 255).astype(np.uint8)


def neutralise_midtones(img, strength=1.0):
    """Give each channel its own gamma so all three share one median.

    The red, green and blue emulsion layers have slightly different contrast, so matching only the
    black and white points still leaves a cast through the middle of the range. One exponent per
    channel corrects it without moving either end.
    """
    median = np.median(_sample(img), axis=0) * (1.0 / 255.0)
    median = np.clip(median, 0.02, 0.98)
    target = float(np.exp(np.log(median).mean()))              # geometric mean: no net lightening
    x = np.clip(img.astype(np.float32) * (1.0 / 255.0), 1e-5, 1.0)
    out = np.empty_like(x)
    for c in range(3):
        exponent = np.log(target) / np.log(median[c])
        out[..., c] = x[..., c] ** (1.0 + strength * (exponent - 1.0))
    return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


def tone_lut(exposure_ev, contrast, gamma):
    """Build a 256-entry lookup table for exposure, contrast and gamma."""
    x = np.linspace(0.0, 1.0, 256, dtype=np.float32)
    x = x * (2.0 ** exposure_ev)
    if contrast:
        k = 1.0 + 4.0 * contrast if contrast > 0 else 1.0 + contrast
        x = 0.5 + (x - 0.5) * k
    x = np.clip(x, 0.0, 1.0)
    y = np.power(x, 1.0 / gamma)
    return np.clip(y * 255.0 + 0.5, 0, 255).astype(np.uint8)


def apply_tone(img, settings):
    lut = tone_lut(settings.exposure_ev, settings.contrast, settings.gamma)
    return lut[img]


# Rec. 601 luma weights, in OpenCV's BGR order.
_LUMA_BGR = np.array([0.114, 0.587, 0.299], dtype=np.float32)


def adjust_saturation(img, amount):
    """Scale how far each pixel sits from grey. 0 is greyscale, 1 unchanged, above 1 more vivid.

    Done as a blend against luma rather than in HSV: HSV would need two more colour conversions per
    frame, and converting back through uint8 quantises hue and value on every preview refresh.
    """
    if amount == 1.0:
        return img
    f = img.astype(np.float32)
    luma = (f * _LUMA_BGR).sum(axis=2, keepdims=True)
    return np.clip(luma + (f - luma) * amount + 0.5, 0, 255).astype(np.uint8)


def process(img, settings):
    """Full pipeline: film conversion, levels, tone, saturation. Input and output are BGR uint8."""
    if settings.film == FILM_COLOR_NEGATIVE:
        # Density inversion already places the black and white points, so auto_levels would only
        # re-stretch what it just set; neutralising the midtones is the useful follow-up.
        out = negative_to_positive(img) if settings.auto_levels else invert(img)
        if settings.auto_levels and settings.neutralise:
            out = neutralise_midtones(out)
    elif settings.film == FILM_BW_NEGATIVE:
        out = invert(to_mono_bgr(img))
        if settings.auto_levels:
            out = auto_levels(out)
    else:
        out = auto_levels(img) if settings.auto_levels else img
    # Saturation goes last, on the finished tones: gamma and contrast both move colours towards or
    # away from grey, so judging vividness before them would fight the other sliders.
    return adjust_saturation(apply_tone(out, settings), settings.saturation)


def average_frames(frames):
    """Average same-size frames to reduce sensor noise. Works on raw mosaics or BGR images."""
    stack = np.stack([np.asarray(f, dtype=np.float32) for f in frames], axis=0)
    return np.clip(stack.mean(axis=0) + 0.5, 0, 255).astype(np.uint8)


def fit_to(img, max_w, max_h):
    """Downscale for display, keeping aspect ratio. Returns the input unchanged if it already fits."""
    h, w = img.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    if scale == 1.0:
        return img
    return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
