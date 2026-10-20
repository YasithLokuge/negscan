"""Tests for the auto-exposure loop, against a modelled sensor instead of the scanner."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scanner_app import capture, ov5620  # noqa: E402
from scanner_app.usb_scanner import EXPOSURE_MAX, EXPOSURE_MIN  # noqa: E402


class FakeScanner:
    """A sensor whose measured level is `slope * exposure + offset`, clamped to 0..255.

    The offset matters: on the real sensor the response does not pass through the origin, so near
    the bottom of the register one step is worth far more than the tolerance. That is what makes
    the loop oscillate, and it is the case worth testing.
    """

    def __init__(self, slope=18.0, offset=-258.0):
        self.slope, self.offset = slope, offset
        self.exposure = capture.DEFAULT_EXPOSURE
        self.reads = 0
        self.history = []

    def set_exposure(self, value):
        self.exposure = max(EXPOSURE_MIN, min(EXPOSURE_MAX, int(value)))
        self.history.append(self.exposure)
        return self.exposure

    def level(self, exposure=None):
        e = self.exposure if exposure is None else exposure
        return float(np.clip(round(self.slope * e + self.offset), 0, 255))

    def read_frame(self, timeout=6.0, should_stop=None):
        self.reads += 1
        return np.full(ov5620.FRAME_BYTES, int(self.level()), dtype=np.uint8).tobytes()


class AutoExposureTests(unittest.TestCase):
    RESPONSES = ((18.0, -258.0), (8.6, 0.0), (4.0, 10.0), (30.0, -400.0), (1.5, 0.0))

    def test_finds_the_largest_exposure_at_or_under_target(self):
        """The contract, checked against the model's own answer for every response shape."""
        for slope, offset in self.RESPONSES:
            with self.subTest(slope=slope, offset=offset):
                scanner = FakeScanner(slope, offset)
                chosen = capture.auto_expose(scanner)
                best = max((e for e in range(EXPOSURE_MIN, EXPOSURE_MAX + 1)
                            if scanner.level(e) <= capture.AE_TARGET), default=EXPOSURE_MIN)
                self.assertEqual(chosen, best,
                                 f"chose {chosen} (level {scanner.level(chosen)}), "
                                 f"best is {best} (level {scanner.level(best)})")

    def test_never_returns_a_clipping_exposure(self):
        for slope, offset in self.RESPONSES:
            with self.subTest(slope=slope, offset=offset):
                scanner = FakeScanner(slope, offset)
                chosen = capture.auto_expose(scanner)
                self.assertLess(scanner.level(chosen), capture.AE_CLIPPED,
                                f"chose {chosen}, level {scanner.level(chosen)}")

    def test_coarse_sensor_lands_on_the_boundary(self):
        """The case a proportional loop cannot settle: one register step jumps past the target."""
        scanner = FakeScanner(slope=18.0, offset=-258.0)
        chosen = capture.auto_expose(scanner)
        self.assertLessEqual(scanner.level(chosen), capture.AE_TARGET)
        self.assertGreater(scanner.level(chosen + 1), capture.AE_TARGET,
                           "one step up should exceed the target, or this was not the largest")

    def test_stops_instead_of_cycling(self):
        scanner = FakeScanner(slope=18.0, offset=-258.0)
        capture.auto_expose(scanner)
        distinct = set(scanner.history)
        self.assertLessEqual(len(distinct), capture.AE_MAX_STEPS + 1)
        self.assertLessEqual(scanner.reads, 2 * capture.AE_MAX_STEPS + 2)

    def test_leaves_the_scanner_on_the_exposure_it_returned(self):
        for slope, offset in ((18.0, -258.0), (8.6, 0.0)):
            with self.subTest(slope=slope):
                scanner = FakeScanner(slope, offset)
                chosen = capture.auto_expose(scanner)
                self.assertEqual(scanner.exposure, chosen)

    def test_everything_clipping_falls_back_to_the_darkest_tried(self):
        scanner = FakeScanner(slope=100.0, offset=0.0)   # clips even at the minimum
        chosen = capture.auto_expose(scanner)
        self.assertEqual(chosen, EXPOSURE_MIN)
        self.assertEqual(scanner.exposure, EXPOSURE_MIN)

    def test_a_dead_stream_does_not_hang_or_crash(self):
        class Dead(FakeScanner):
            def read_frame(self, timeout=6.0, should_stop=None):
                return None

        scanner = Dead()
        chosen = capture.auto_expose(scanner)
        self.assertEqual(chosen, capture.DEFAULT_EXPOSURE)

    def test_starting_from_the_answer_costs_one_probe(self):
        """A repeat capture should confirm the current exposure, not search again."""
        scanner = FakeScanner(slope=18.0, offset=-258.0)
        first = capture.auto_expose(scanner)
        probes_for_first = len(set(scanner.history))

        again = FakeScanner(slope=18.0, offset=-258.0)
        second = capture.auto_expose(again, start=first)
        self.assertEqual(second, first)
        self.assertLess(len(set(again.history)), probes_for_first)
        # Two probes: confirm the current exposure, then confirm one step up is too bright.
        # Each probe reads two frames, and one more is read after the final setting.
        self.assertLessEqual(again.reads, 5)

    def test_start_outside_the_range_is_clamped(self):
        scanner = FakeScanner(slope=8.6, offset=0.0)
        chosen = capture.auto_expose(scanner, start=10_000)
        self.assertLessEqual(scanner.level(chosen), capture.AE_TARGET)
        self.assertTrue(all(EXPOSURE_MIN <= e <= EXPOSURE_MAX for e in scanner.history))

    def test_measure_level_reads_the_bright_end(self):
        mosaic = np.full((ov5620.HEIGHT, ov5620.WIDTH), 40, dtype=np.uint8)
        mosaic[:, ::7] = 200
        self.assertGreater(capture.measure_level(mosaic), 150)


if __name__ == "__main__":
    unittest.main()
