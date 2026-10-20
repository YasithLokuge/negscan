"""Image processing for film scans. Pure numpy/OpenCV, no UI, so it is easy to test.

Every stage except saturation is a *per-channel pointwise* function of an 8-bit input, so each one
is built as a 256x3 curve and the whole chain is composed into a single lookup table before it
touches the image. On a 4.4 MP frame that is the difference between about 930 ms of float maths and
about 10 ms of `cv2.LUT`, and because the input really is 8-bit the result is identical, not an
approximation: the curve is the old formula evaluated at the only 256 values it can ever see.

The statistics each stage needs (percentiles, medians) come from a decimated sample, exactly as
before, so building the chain costs a few milliseconds regardless of image size.
"""
from dataclasses import dataclass

import cv2
import numpy as np

FILM_COLOR_NEGATIVE = "Color negative"
FILM_BW_NEGATIVE = "B&W negative"
FILM_POSITIVE = "Positive slide"
FILM_TYPES = (FILM_COLOR_NEGATIVE, FILM_BW_NEGATIVE, FILM_POSITIVE)

# Quarter turns clockwise. Anything else would need interpolation and would soften the scan.
ROTATIONS = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}

LEVELS = 256
_RAMP = np.arange(LEVELS, dtype=np.float32)[:, None]        # (256, 1), broadcasts over channels


@dataclass(frozen=True)
class Settings:
    film: str = FILM_COLOR_NEGATIVE
    auto_levels: bool = True
    neutralise: bool = True    # match the three channels through the midtones
    exposure_ev: float = 0.0   # -2.0 .. +2.0 stops
    contrast: float = 0.0      # -1.0 .. +1.0
    gamma: float = 1.0         # 0.4 .. 2.5
    saturation: float = 1.0    # 0.0 grey .. 1.0 unchanged .. 2.0 double
    rotation: int = 0          # quarter turns clockwise: 0, 90, 180 or 270 degrees


# ---------------------------------------------------------------- curves

def apply_curve(img, curve):
    """Map a BGR uint8 image through a (256, 3) curve, one table per channel."""
    return cv2.LUT(img, np.ascontiguousarray(curve).reshape(1, LEVELS, 3))


def map_through(curve, values):
    """Apply a (256, 3) curve to any (..., 3) array of channel values. Also composes two curves."""
    return np.take_along_axis(curve, values.astype(np.intp), axis=0)


def compose(curves):
    """Collapse curves applied in order into one. Exact, because each stage is 8-bit in and out."""
    out = np.tile(np.arange(LEVELS, dtype=np.uint8)[:, None], (1, 3))
    for curve in curves:
        out = map_through(curve, out)
    return out


def _as_curve(values, round_half_up=False):
    """Clip a (256, 3) or (256, 1) float curve into uint8, broadcasting to three channels."""
    if round_half_up:
        values = values + 0.5
    curve = np.clip(values, 0, 255).astype(np.uint8)
    return np.broadcast_to(curve, (LEVELS, 3)) if curve.shape[1] == 1 else curve


def invert_curve():
    return _as_curve(255.0 - _RAMP)


def levels_curve(pixels, low_pct=0.5, high_pct=99.5):
    """Stretch each channel so its darkest and brightest 0.5% map to black and white."""
    lo = np.percentile(pixels, low_pct, axis=0)
    hi = np.percentile(pixels, high_pct, axis=0)
    span = np.maximum(hi - lo, 1.0)
    return _as_curve((_RAMP - lo) * (255.0 / span))


def negative_curve(pixels, base_pct=0.3, shoulder_pct=0.2):
    """Invert a colour negative in density space, which is where film actually behaves linearly.

    A negative's orange mask is a dye layer in front of everything else, so in transmittance it is
    a per-channel *multiplier* and inverting linearly leaves a cast that no amount of channel
    stretching removes cleanly. Taking -log10 turns it into a per-channel offset, and subtracting
    each channel's own film-base density removes the mask exactly.

    `base_pct` is the fraction of pixels allowed to be lighter than the film base, and
    `shoulder_pct` the fraction allowed to clip at white.
    """
    def density(x):
        return -np.log10(np.maximum(x, 1.0) * (1.0 / 255.0))

    sample = density(pixels.astype(np.float32))
    base = np.percentile(sample, base_pct, axis=0)                  # clear base: least dense
    shoulder = np.percentile(sample, 100.0 - shoulder_pct, axis=0)
    scaled = (density(_RAMP) - base) / np.maximum(shoulder - base, 1e-6)
    return _as_curve(scaled * 255.0)


def neutralise_curve(pixels, strength=1.0):
    """Give each channel its own gamma so all three share one median.

    The red, green and blue emulsion layers have slightly different contrast, so matching only the
    black and white points still leaves a cast through the middle of the range. One exponent per
    channel corrects it without moving either end.
    """
    median = np.clip(np.median(pixels, axis=0) * (1.0 / 255.0), 0.02, 0.98)
    target = float(np.exp(np.log(median).mean()))                   # geometric mean: no lightening
    exponent = 1.0 + strength * (np.log(target) / np.log(median) - 1.0)
    x = np.clip(_RAMP * (1.0 / 255.0), 1e-5, 1.0)
    return _as_curve((x ** exponent) * 255.0, round_half_up=True)


def tone_curve(exposure_ev, contrast, gamma):
    """Exposure, contrast and gamma, as one curve shared by all three channels."""
    x = _RAMP * (1.0 / 255.0)
    x = x * (2.0 ** exposure_ev)
    if contrast:
        k = 1.0 + 4.0 * contrast if contrast > 0 else 1.0 + contrast
        x = 0.5 + (x - 0.5) * k
    return _as_curve(np.power(np.clip(x, 0.0, 1.0), 1.0 / gamma) * 255.0, round_half_up=True)


def tone_lut(exposure_ev, contrast, gamma):
    """The tone curve as a flat 256-entry table."""
    return tone_curve(exposure_ev, contrast, gamma)[:, 0].copy()


# ------------------------------------------------- stages, as whole images

def _sample(img, step=4):
    """A decimated view for percentiles. On a 4.4 MP frame this is ~16x cheaper and as accurate."""
    return img[::step, ::step].reshape(-1, img.shape[2])


def invert(img):
    return 255 - img


def to_mono_bgr(img):
    """Collapse to grey and keep three channels so the rest of the pipeline is unchanged."""
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)


def auto_levels(img, low_pct=0.5, high_pct=99.5):
    return apply_curve(img, levels_curve(_sample(img), low_pct, high_pct))


def negative_to_positive(img, base_pct=0.3, shoulder_pct=0.2):
    return apply_curve(img, negative_curve(_sample(img), base_pct, shoulder_pct))


def neutralise_midtones(img, strength=1.0):
    return apply_curve(img, neutralise_curve(_sample(img), strength))


def apply_tone(img, settings):
    return apply_curve(img, tone_curve(settings.exposure_ev, settings.contrast, settings.gamma))


def adjust_saturation(img, amount):
    """Scale how far each pixel sits from grey. 0 is greyscale, 1 unchanged, above 1 more vivid.

    A blend against luma rather than an HSV round trip: HSV would need two more colour conversions
    per frame and would requantise hue and value every time. `cvtColor` to grey uses the same
    Rec. 601 weights, and both calls stay in 8-bit, so this is a few milliseconds on a full frame.
    """
    if amount == 1.0:
        return img
    grey = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    return cv2.addWeighted(img, amount, grey, 1.0 - amount, 0.0)


# --------------------------------------------------------------- pipeline

def pipeline_curve(img, settings):
    """Build the single curve that carries out everything but saturation.

    Each stage's statistics come from the running sample, so a stage sees exactly the values it
    would have seen if the stages had been applied to the whole image one at a time.
    """
    curves = []
    pixels = _sample(img)

    def stage(curve):
        nonlocal pixels
        curves.append(curve)
        pixels = map_through(curve, pixels)

    if settings.film == FILM_COLOR_NEGATIVE:
        # Density inversion already places the black and white points, so auto_levels would only
        # re-stretch what it just set; neutralising the midtones is the useful follow-up.
        if settings.auto_levels:
            stage(negative_curve(pixels))
            if settings.neutralise:
                stage(neutralise_curve(pixels))
        else:
            stage(invert_curve())
    elif settings.film == FILM_BW_NEGATIVE:
        stage(invert_curve())
        if settings.auto_levels:
            stage(levels_curve(pixels))
    elif settings.auto_levels:
        stage(levels_curve(pixels))

    # Tone goes last of the pointwise stages, then saturation on top of the finished tones: gamma
    # and contrast both move colours towards or away from grey, so judging vividness before them
    # would fight the other sliders.
    stage(tone_curve(settings.exposure_ev, settings.contrast, settings.gamma))
    return compose(curves)


def rotate(img, degrees):
    """Turn an image by a multiple of 90 degrees, clockwise. Lossless, and free at 0."""
    degrees = int(degrees) % 360
    if degrees == 0:
        return img
    if degrees not in ROTATIONS:
        raise ValueError(f"rotation must be a multiple of 90 degrees, got {degrees}")
    return cv2.rotate(img, ROTATIONS[degrees])


def process(img, settings):
    """Full pipeline: film conversion, levels, tone, saturation, rotation.

    Input and output are BGR uint8. Rotation comes last because it is the only geometric step:
    turning the image first would change which pixels the decimated sample picks, and so shift the
    levels very slightly for no reason.
    """
    out = to_mono_bgr(img) if settings.film == FILM_BW_NEGATIVE else img
    out = apply_curve(out, pipeline_curve(out, settings))
    return rotate(adjust_saturation(out, settings.saturation), settings.rotation)


def average_frames(frames):
    """Average same-size frames to reduce sensor noise. Works on raw mosaics or BGR images.

    Accumulated as integers: a float32 stack of several 4.4 MP frames is tens of megabytes and the
    allocation costs more than the arithmetic.
    """
    frames = [np.asarray(f) for f in frames]
    if len(frames) == 1:
        return frames[0].astype(np.uint8, copy=True)
    total = np.zeros(frames[0].shape, dtype=np.uint32)
    for frame in frames:
        total += frame
    return ((total + len(frames) // 2) // len(frames)).astype(np.uint8)


def fit_to(img, max_w, max_h):
    """Downscale for display, keeping aspect ratio. Returns the input unchanged if it already fits."""
    h, w = img.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    if scale == 1.0:
        return img
    return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
