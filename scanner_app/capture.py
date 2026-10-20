"""Scanner control on a worker thread, plus the auto-exposure loop.

The GUI thread must never block on USB: a frame takes about 270 ms to arrive and opening the device
takes longer. `CaptureWorker` owns the `Scanner`, streams previews continuously, and does a capture
when asked, handing results back through Qt signals.
"""
import time

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from . import processing, raw
from .usb_scanner import (EXPOSURE_MAX, EXPOSURE_MIN, Scanner, ScannerError)

# Aim the brightest part of the film just below clipping. The film base is the brightest thing in
# the frame and carries no picture, so a little headroom is all that is needed.
AE_TARGET = 236
AE_PERCENTILE = 99.8
AE_CLIPPED = 250           # at or above this the frame is clipping and the level is meaningless
AE_MAX_STEPS = 8
DEFAULT_EXPOSURE = 50


def measure_level(mosaic, percentile=AE_PERCENTILE):
    """The brightness the auto-exposure loop steers, read off a decimated raw mosaic."""
    return float(np.percentile(mosaic[::4, ::4], percentile))


def auto_expose(scanner, target=AE_TARGET, start=None, log=None):
    """Set the largest exposure whose brightest 0.2% still sits at or below `target`, and return it.

    That is the whole contract, and it is what a scanner wants: as much signal as possible without
    clipping the film base. The sensor's own AEC is not used -- it meters for an average scene, and
    a backlit negative is nearly all highlight, so it drives the base straight into clipping.

    `start` is where to begin the search. Passing the exposure the last scan settled on makes a
    repeat capture cost one or two frames instead of six, because the lamp and the film base do not
    change between frames of the same roll.

    Brightness rises with the exposure register, so the search brackets the answer and bisects.
    Scaling by target/measured alone is not enough: the response does not pass through the origin,
    so near the bottom of the register one step is worth far more than the tolerance, and a plain
    feedback loop just oscillates between two neighbouring values. Proportional steps are used
    only until the answer is bracketed, and bisection finishes the job in a few more frames.
    """
    measured = {}

    def probe(exposure):
        scanner.set_exposure(exposure)
        scanner.read_frame()                   # the frame in flight still has the old exposure
        frame = scanner.read_frame()
        if frame is None:
            return None
        measured[exposure] = measure_level(raw.as_mosaic(frame))
        if log:
            log(f"auto exposure: {exposure} -> level {measured[exposure]:.0f}")
        return measured[exposure]

    def clamp(value):
        return max(EXPOSURE_MIN, min(EXPOSURE_MAX, int(value)))

    low = high = None       # low: largest exposure at or under target; high: smallest over it
    exposure = clamp(DEFAULT_EXPOSURE if start is None else start)
    for _ in range(AE_MAX_STEPS):
        level = probe(exposure)
        if level is None:
            break
        if level <= target:
            low = exposure if low is None else max(low, exposure)
        else:
            high = exposure if high is None else min(high, exposure)

        if low is not None and high is not None:
            if high - low <= 1:
                break                          # the boundary is pinned down
            nxt = (low + high) // 2
        elif level >= AE_CLIPPED:
            nxt = round(exposure * 0.6)        # clipped: the level says nothing about how far over
        else:
            nxt = round(exposure * target / max(level, 1.0))
        nxt = clamp(nxt)
        if low is not None:
            nxt = max(nxt, low + 1)
        if high is not None:
            nxt = min(nxt, high - 1)
        nxt = clamp(nxt)
        if nxt == exposure or nxt in measured:
            break
        exposure = nxt

    if low is None:
        # No frame below target. Either it is too bright even at the shortest exposure, or no
        # frame arrived at all -- and in that second case we have learned nothing, so the default
        # is a better guess than the darkest setting.
        low = EXPOSURE_MIN if measured else DEFAULT_EXPOSURE
    if log:
        shown = measured.get(low)
        log(f"auto exposure: {low}" + (f" (level {shown:.0f})" if shown is not None else ""))
    scanner.set_exposure(low)
    scanner.read_frame()                       # let the change take effect before anyone captures
    return low


class CaptureWorker(QObject):
    """Lives on its own QThread. Call the slots; listen to the signals."""

    preview_ready = Signal(object)        # BGR uint8, half resolution
    capture_ready = Signal(object)        # BGR uint8, full resolution
    status = Signal(str)
    failed = Signal(str)
    connected = Signal(bool)

    def __init__(self):
        super().__init__()
        self._scanner = None
        self._previewing = False
        self._stop = False
        self._timer = None
        self._exposure = None

    # -------------------------------------------------------------- lifetime

    @Slot()
    def connect_scanner(self):
        if self._scanner is not None:
            return
        try:
            self.status.emit("Opening the scanner...")
            scanner = Scanner()
            scanner.init_sensor(log=self.status.emit)
            scanner.set_auto_exposure(False)
            scanner.set_gain(0)
            scanner.set_exposure(DEFAULT_EXPOSURE)
            scanner.start_stream()
            scanner.read_frame()               # the first frame after a start is always black
            self.status.emit("Metering...")
            exposure = auto_expose(scanner, log=self.status.emit)
            self._exposure = exposure
            self._scanner = scanner
            self.connected.emit(True)
            self.status.emit(f"Ready (exposure {exposure})")
        except (ScannerError, OSError) as e:
            self._close()
            self.failed.emit(str(e))

    @Slot()
    def disconnect_scanner(self):
        self._previewing = False
        self._close()
        self.connected.emit(False)
        self.status.emit("Disconnected")

    def _close(self):
        self._exposure = None
        if self._timer is not None:
            self._timer.stop()
        if self._scanner is not None:
            try:
                self._scanner.close()
            except Exception:
                pass
            self._scanner = None

    @Slot()
    def shutdown(self):
        self._stop = True
        self._previewing = False
        self._close()

    # --------------------------------------------------------------- preview

    @Slot(bool)
    def set_previewing(self, on):
        """Start or stop the preview.

        One frame is grabbed per timer tick rather than in a loop, so this thread keeps returning
        to its event loop between frames and queued calls -- capture, stop, disconnect -- are
        delivered promptly instead of waiting out a 270 ms frame.
        """
        self._previewing = bool(on)
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self._grab_preview)
        if self._previewing:
            self._timer.start(0)
        else:
            self._timer.stop()

    @Slot()
    def _grab_preview(self):
        if not self._previewing or self._stop or self._scanner is None:
            return
        try:
            frame = self._scanner.read_frame(timeout=3.0,
                                             should_stop=lambda: not self._previewing)
        except (ScannerError, OSError) as e:
            self._previewing = False
            self.failed.emit(str(e))
            self._close()
            self.connected.emit(False)
            return
        if frame is not None:
            self.preview_ready.emit(raw.debayer_half(frame))
        if self._previewing:
            self._timer.start(0)

    # --------------------------------------------------------------- capture

    @Slot(int)
    def capture(self, frame_count):
        if self._scanner is None:
            self.failed.emit("The scanner is not connected.")
            return
        frame_count = max(1, int(frame_count))
        try:
            self.status.emit("Metering...")
            self._exposure = auto_expose(self._scanner, start=self._exposure)
            self.status.emit(f"Capturing {frame_count} frame(s)...")
            mosaics = []
            deadline = time.time() + 10 + 2 * frame_count
            while len(mosaics) < frame_count and time.time() < deadline:
                frame = self._scanner.read_frame(timeout=5.0)
                if frame is not None:
                    mosaics.append(raw.as_mosaic(frame))
            if not mosaics:
                self.failed.emit("No complete frame arrived from the scanner.")
                return
            # Average the raw mosaics, before demosaicing: averaging Bayer samples of the same
            # colour is what actually cancels the sensor noise.
            stacked = (processing.average_frames(mosaics) if len(mosaics) > 1 else mosaics[0])
            self.capture_ready.emit(raw.debayer(stacked.tobytes()))
            self.status.emit(f"Captured ({len(mosaics)} frame(s) averaged)")
        except (ScannerError, OSError) as e:
            self.failed.emit(str(e))

    @Slot(int)
    def set_exposure(self, value):
        if self._scanner is not None:
            try:
                self._scanner.set_exposure(value)
            except (ScannerError, OSError) as e:
                self.failed.emit(str(e))
