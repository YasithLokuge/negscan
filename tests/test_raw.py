"""Tests for the Bayer decoding and the register tables. No hardware needed."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scanner_app import ov5620, raw  # noqa: E402


# Where red and blue sit in the top-left 2x2, for each way of naming a Bayer pattern.
RED_BLUE_POSITIONS = {"RGGB": ((0, 0), (1, 1)), "BGGR": ((1, 1), (0, 0)),
                      "GRBG": ((0, 1), (1, 0)), "GBRG": ((1, 0), (0, 1))}


def synthetic_mosaic(pattern=ov5620.BAYER):
    """A mosaic whose left half is lit only in red photosites and right half only in blue.

    `raw.debayer` rotates by 180 degrees, so a correct decode puts blue on the left of the output
    and red on the right. Building the mosaic from the pattern name means this checks the decoders
    against each other and against OpenCV's naming, whichever pattern is configured.
    """
    (ry, rx), (by, bx) = RED_BLUE_POSITIONS[pattern]
    m = np.zeros((ov5620.HEIGHT, ov5620.WIDTH), dtype=np.uint8)
    half = ov5620.WIDTH // 2
    m[ry::2, rx::2] = 200
    m[by::2, bx::2] = 180
    m[:, half:] = np.where(m[:, half:] == 200, 0, m[:, half:])   # no red in the right half
    m[:, :half] = np.where(m[:, :half] == 180, 0, m[:, :half])   # no blue in the left half
    green = np.zeros_like(m, dtype=bool)
    for gy, gx in ({(0, 0), (0, 1), (1, 0), (1, 1)} - {(ry, rx), (by, bx)}):
        green[gy::2, gx::2] = True
    m[green] = 60
    return m


class TableTests(unittest.TestCase):
    def test_geometry_matches_the_window_registers(self):
        window = {reg: value for reg, value in ov5620.SENSOR_INIT}
        self.assertEqual((window[0x18] - window[0x17]) * 16, ov5620.WIDTH)
        self.assertEqual((window[0x1A] - window[0x19]) * 8, ov5620.HEIGHT)

    def test_bridge_frame_size_matches_the_sensor_window(self):
        bridge = {}
        for reg, value in ov5620.BRIDGE_INIT:
            bridge.setdefault(reg, value)       # 0x1D repeats; the size registers do not
        self.assertEqual(bridge[0x88] | (bridge[0x89] << 8), ov5620.WIDTH)
        self.assertEqual(bridge[0x8A] | (bridge[0x8B] << 8), ov5620.HEIGHT)

    def test_frame_bytes(self):
        self.assertEqual(ov5620.FRAME_BYTES, 2592 * 1680)

    def test_bayer_pattern_is_the_one_measured_on_the_hardware(self):
        """Locked down deliberately: this was established by scanning a negative and looking.

        The two diagonal phases both produce a plausible-looking picture, and the wrong one simply
        swaps red and blue -- green foliage becomes magenta, blue sky becomes green. Nothing in the
        driver files states it, so if this ever needs to change, change it by rendering a scan of
        known colours, not by reasoning about the register tables.
        """
        self.assertEqual(ov5620.BAYER, "BGGR")


class DebayerTests(unittest.TestCase):
    def test_full_and_half_agree_on_size(self):
        frame = synthetic_mosaic().tobytes()
        self.assertEqual(raw.debayer(frame).shape, (ov5620.HEIGHT, ov5620.WIDTH, 3))
        self.assertEqual(raw.debayer_half(frame).shape,
                         (ov5620.HEIGHT // 2, ov5620.WIDTH // 2, 3))

    def test_half_resolution_puts_each_colour_in_the_right_plane(self):
        out = raw.debayer_half(synthetic_mosaic().tobytes())   # BGR, rotated 180
        left = out[:, :out.shape[1] // 2 - 4].reshape(-1, 3).mean(axis=0)
        right = out[:, out.shape[1] // 2 + 4:].reshape(-1, 3).mean(axis=0)
        self.assertEqual(int(np.argmax(left)), 0, f"left should be blue, got BGR {left}")
        self.assertEqual(int(np.argmax(right)), 2, f"right should be red, got BGR {right}")

    def test_full_resolution_agrees_with_half_on_colour_order(self):
        out = raw.debayer(synthetic_mosaic().tobytes())
        left = out[:, :out.shape[1] // 2 - 8].reshape(-1, 3).mean(axis=0)
        right = out[:, out.shape[1] // 2 + 8:].reshape(-1, 3).mean(axis=0)
        self.assertEqual(int(np.argmax(left)), 0)
        self.assertEqual(int(np.argmax(right)), 2)

    def test_wrong_size_frame_is_rejected(self):
        with self.assertRaises(ValueError):
            raw.as_mosaic(b"\x00" * 1024)


if __name__ == "__main__":
    unittest.main()
