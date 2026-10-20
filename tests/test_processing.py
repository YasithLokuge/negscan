"""Tests for the pure image pipeline. Run: ./.venv/bin/python -m unittest discover -s tests -v"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scanner_app import processing as proc  # noqa: E402


def gradient(h=64, w=96):
    ramp = np.linspace(0, 255, w, dtype=np.uint8)
    return np.dstack([np.tile(ramp, (h, 1))] * 3)


def masked_negative(h=64, w=96):
    """A synthetic colour negative: a scene inverted, then dimmed per channel by an orange mask.

    Blue is attenuated hardest and red least, which is how a real mask behaves.
    """
    scene = np.dstack([np.tile(np.linspace(10, 245, w, dtype=np.float32), (h, 1))] * 3)
    negative = 255.0 - scene
    mask = np.array([0.35, 0.6, 1.0], dtype=np.float32)   # BGR: blue dimmest, red brightest
    return np.clip(negative * mask, 0, 255).astype(np.uint8)


class ToneTests(unittest.TestCase):
    def test_invert_is_involution(self):
        img = gradient()
        np.testing.assert_array_equal(proc.invert(proc.invert(img)), img)

    def test_default_tone_is_identity(self):
        lut = proc.tone_lut(0.0, 0.0, 1.0)
        np.testing.assert_array_equal(lut, np.arange(256, dtype=np.uint8))

    def test_positive_slide_without_levels_is_unchanged(self):
        img = gradient()
        s = proc.Settings(film=proc.FILM_POSITIVE, auto_levels=False)
        np.testing.assert_array_equal(proc.process(img, s), img)

    def test_auto_levels_uses_the_full_range(self):
        img = (gradient().astype(np.float32) * 0.4 + 40).astype(np.uint8)
        out = proc.auto_levels(img)
        self.assertLess(out.min(), 8)
        self.assertGreater(out.max(), 247)


class NegativeTests(unittest.TestCase):
    def test_density_inversion_removes_the_orange_mask(self):
        """The three channels should line up again once the mask is divided out in density space."""
        out = proc.negative_to_positive(masked_negative()).astype(np.float32)
        spread = out.reshape(-1, 3).mean(axis=0)
        self.assertLess(spread.max() - spread.min(), 12.0,
                        f"channels still differ after inversion: {spread}")

    def test_density_inversion_reverses_the_negative(self):
        """The negative ramps bright-to-dark, so the positive must ramp dark-to-bright."""
        neg = masked_negative()
        self.assertGreater(neg[:, :8].mean(), neg[:, -8:].mean())
        out = proc.negative_to_positive(neg)
        left, right = out[:, :8].mean(), out[:, -8:].mean()
        self.assertGreater(right, left)
        self.assertGreater(right - left, 100)

    def test_density_inversion_spans_the_range(self):
        out = proc.negative_to_positive(masked_negative())
        self.assertLess(out.min(), 8)
        self.assertGreater(out.max(), 247)

    def test_neutralise_matches_channel_medians(self):
        img = masked_negative()
        shifted = proc.negative_to_positive(img)
        out = proc.neutralise_midtones(shifted)
        medians = np.median(out.reshape(-1, 3), axis=0)
        self.assertLess(medians.max() - medians.min(), 4.0, f"medians: {medians}")

    def test_neutralise_leaves_a_neutral_image_alone(self):
        img = gradient()
        np.testing.assert_allclose(proc.neutralise_midtones(img), img, atol=2)

    def test_colour_negative_pipeline_runs_end_to_end(self):
        out = proc.process(masked_negative(), proc.Settings())
        self.assertEqual(out.shape, (64, 96, 3))
        self.assertEqual(out.dtype, np.uint8)


def colourful(h=64, w=96):
    """A ramp with the three channels offset from each other, so it has real saturation."""
    ramp = np.linspace(40, 215, w, dtype=np.float32)
    img = np.dstack([np.tile(np.roll(ramp, shift), (h, 1)) for shift in (0, 12, 24)])
    return img.astype(np.uint8)


class SaturationTests(unittest.TestCase):
    def test_unchanged_at_one(self):
        img = colourful()
        np.testing.assert_array_equal(proc.adjust_saturation(img, 1.0), img)

    def test_zero_is_greyscale(self):
        out = proc.adjust_saturation(colourful(), 0.0)
        np.testing.assert_allclose(out[..., 0], out[..., 1], atol=1)
        np.testing.assert_allclose(out[..., 1], out[..., 2], atol=1)

    def test_zero_preserves_luma(self):
        img = colourful()
        luma = (img.astype(np.float32) * proc._LUMA_BGR).sum(axis=2)
        out = proc.adjust_saturation(img, 0.0).astype(np.float32).mean(axis=2)
        np.testing.assert_allclose(out, luma, atol=1.5)

    def test_above_one_widens_the_channel_spread(self):
        img = colourful()
        spread = lambda x: float((x.astype(np.float32).max(axis=2)
                                  - x.astype(np.float32).min(axis=2)).mean())
        self.assertGreater(spread(proc.adjust_saturation(img, 1.6)), spread(img) * 1.4)

    def test_grey_input_is_unaffected_at_any_amount(self):
        grey = gradient()
        for amount in (0.0, 0.5, 2.0):
            np.testing.assert_allclose(proc.adjust_saturation(grey, amount), grey, atol=1)

    def test_extremes_stay_in_range(self):
        out = proc.adjust_saturation(colourful(), 2.0)
        self.assertEqual(out.dtype, np.uint8)
        self.assertGreaterEqual(out.min(), 0)
        self.assertLessEqual(out.max(), 255)

    def test_pipeline_applies_the_setting(self):
        img = masked_negative()
        plain = proc.process(img, proc.Settings())
        grey = proc.process(img, proc.Settings(saturation=0.0))
        self.assertFalse(np.array_equal(plain, grey))
        np.testing.assert_allclose(grey[..., 0], grey[..., 2], atol=1)


class AverageTests(unittest.TestCase):
    def test_average_cancels_symmetric_noise(self):
        # Kept clear of 0 and 255 so clipping cannot bias the mean at either end.
        base = (gradient().astype(np.float32) * 0.8 + 25).astype(np.uint8)
        frames = [np.clip(base.astype(np.int16) + d, 0, 255).astype(np.uint8) for d in (-10, 10)]
        np.testing.assert_allclose(proc.average_frames(frames), base, atol=1)

    def test_average_works_on_single_channel_mosaics(self):
        mosaic = np.full((8, 8), 100, dtype=np.uint8)
        out = proc.average_frames([mosaic, mosaic + 20])
        self.assertEqual(out.shape, (8, 8))
        self.assertTrue((out == 110).all())


class FitTests(unittest.TestCase):
    def test_fit_keeps_small_images(self):
        img = gradient(10, 10)
        self.assertIs(proc.fit_to(img, 100, 100), img)

    def test_fit_preserves_aspect_ratio(self):
        img = gradient(200, 400)
        out = proc.fit_to(img, 100, 100)
        self.assertEqual(out.shape[:2], (50, 100))


if __name__ == "__main__":
    unittest.main()
