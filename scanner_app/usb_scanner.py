"""Driver for the Kaiser Baas / OVT film scanner: an OV550 USB bridge with an OV5620 sensor.

The scanner has no UVC video interface -- its only interface is vendor-specific class 0xFF -- so
macOS never lists it as a camera and AVFoundation cannot see it. Everything here talks to it
directly over libusb instead.

Protocol, from the Windows driver (ov550ivx.sys / ov550ivx.inf and OvtCam/MQT5620RAW.set) and from
testing against the device:

- Bridge registers: vendor control transfers on interface 0, bRequest 1, wValue 0, wIndex = the
  register. Writes use bmRequestType 0x40 and reads 0xC0, one byte either way.
- The sensor hangs off the bridge's SCCB (I2C-like) bus at address 0x60, reached through bridge
  registers 0xF1-0xF6.
- Alternate setting 0 is idle. Alternate setting 1 adds the 5116-byte isochronous IN endpoint 0x81
  that carries image data; writing 0x00 to bridge register 0xE0 starts the stream.
- Image data is 8-bit raw Bayer, 2592x1680, in UVC-style payloads: a 12-byte header per packet
  whose bit 0 is the frame ID, which toggles between frames.
"""
import time
from contextlib import contextmanager

import usb.core
import usb.util

from . import ov5620
from .libusb_iso import IsoStream

VID, PID = 0x05A9, 0x1550
INTERFACE = 0
ALT_IDLE, ALT_STREAM = 0, 1
EP_BUTTON, EP_IMAGE = 0x83, 0x81
ISO_PACKET = 5116

# Bridge registers (ov550ivx.inf, section OVT.USBDCam2800.BasicSetting).
REG_USB_CTRL = 0xE0        # 0x00 runs the stream, 0x08 blocks it
REG_POWER = 0xE7           # 0x3A powers the sensor up, 0x3B down
REG_LED_A, REG_LED_B = 0x21, 0x23
SCCB_ADDR, SCCB_SUB = 0xF1, 0xF2
SCCB_WDATA, SCCB_RDATA = 0xF3, 0xF4
SCCB_OP, SCCB_STATUS = 0xF5, 0xF6
SCCB_WRITE, SCCB_READ_ADDR, SCCB_READ_DATA = 0x37, 0x33, 0xF9
SCCB_DONE, SCCB_BUSY, SCCB_NAK = 0x00, 0x03, 0x04

SENSOR_ADDR = 0x60
# Sensor registers used after the init table. COM8 bit 0 is AEC, bit 1 AWB, bit 2 AGC.
SREG_GAIN, SREG_BLUE_GAIN, SREG_RED_GAIN = 0x00, 0x01, 0x02
SREG_PRODUCT_ID, SREG_VERSION = 0x0A, 0x0B
SREG_EXPOSURE, SREG_COM8, SREG_EXPOSURE_HI = 0x10, 0x13, 0x45
COM8_AEC, COM8_AWB, COM8_AGC = 0x01, 0x02, 0x04

EXPOSURE_MIN, EXPOSURE_MAX = 16, 255
GAIN_MIN, GAIN_MAX = 0x00, 0x1F

WIDTH, HEIGHT = ov5620.WIDTH, ov5620.HEIGHT
FRAME_BYTES = ov5620.FRAME_BYTES


class ScannerError(Exception):
    pass


def find_device():
    """Return the raw USB device, or None when the scanner is not plugged in."""
    return usb.core.find(idVendor=VID, idProduct=PID)


class Scanner:
    """An open scanner. Use it as a context manager so the lamp and interface are released."""

    def __init__(self):
        self.dev = find_device()
        if self.dev is None:
            raise ScannerError(
                "scanner not found (USB 05a9:1550). Check that it is plugged in and that no "
                "other program is using it.")
        try:
            usb.util.claim_interface(self.dev, INTERFACE)
        except usb.core.USBError as e:
            raise ScannerError(f"could not claim the scanner interface: {e}") from e
        self._streaming = False
        self._iso = None
        self._buf = bytearray(FRAME_BYTES)
        self._filled = 0
        self._overrun = False
        self._fid = None

    # ---------------------------------------------------------------- bridge

    def write_reg(self, reg, value):
        self.dev.ctrl_transfer(0x40, 1, 0x0000, reg, bytes([value]), timeout=1000)

    def read_reg(self, reg):
        return self.dev.ctrl_transfer(0xC0, 1, 0x0000, reg, 1, timeout=1000)[0]

    def update_reg(self, reg, value, mask=0xFF):
        """Write `value` under `mask`. The driver's .inf tables carry a mask with every write."""
        if mask == 0xFF:
            self.write_reg(reg, value)
        else:
            self.write_reg(reg, (self.read_reg(reg) & ~mask) | (value & mask))

    # ------------------------------------------------------------------ sccb

    def _sccb_wait(self):
        for _ in range(40):
            status = self.read_reg(SCCB_STATUS)
            if status == SCCB_DONE:
                return True
            if status == SCCB_NAK:
                return False
            time.sleep(0.001)
        return False

    def sccb_write(self, sub, value, addr=SENSOR_ADDR):
        """Write one sensor register. Returns False if the sensor did not acknowledge."""
        self.write_reg(SCCB_ADDR, addr)
        self.write_reg(SCCB_SUB, sub)
        self.write_reg(SCCB_WDATA, value)
        self.write_reg(SCCB_OP, SCCB_WRITE)
        return self._sccb_wait()

    def sccb_read(self, sub, addr=SENSOR_ADDR):
        """Read one sensor register, or None if the sensor did not acknowledge."""
        self.write_reg(SCCB_ADDR, addr)
        self.write_reg(SCCB_SUB, sub)
        self.write_reg(SCCB_OP, SCCB_READ_ADDR)
        if not self._sccb_wait():
            return None
        self.write_reg(SCCB_OP, SCCB_READ_DATA)
        if not self._sccb_wait():
            return None
        return self.read_reg(SCCB_RDATA)

    def update_sccb(self, sub, value, mask=0xFF):
        if mask == 0xFF:
            return self.sccb_write(sub, value)
        current = self.sccb_read(sub)
        if current is None:
            return False
        return self.sccb_write(sub, (current & ~mask) | (value & mask))

    # ------------------------------------------------------------- power-up

    def power_on(self):
        """The driver's SetUsbWork, PowerOnCamera and TurnOnLed sequences, in that order."""
        self.write_reg(REG_USB_CTRL, 0x00)
        self.write_reg(REG_POWER, 0x3A)
        self.write_reg(REG_USB_CTRL, 0x08)
        self.write_reg(REG_USB_CTRL, 0x00)
        self.update_reg(REG_LED_A, 0x80, 0x80)
        self.update_reg(REG_LED_B, 0x80, 0x80)
        time.sleep(0.15)

    def power_off(self):
        """TurnOffLed then PowerDownCamera. Leaves the lamp dark so the unit does not cook film."""
        self.write_reg(REG_USB_CTRL, 0x08)
        self.update_reg(REG_LED_A, 0x80, 0x80)
        self.update_reg(REG_LED_B, 0x00, 0x80)
        self.update_reg(REG_LED_A, 0x00, 0x80)
        self.write_reg(REG_POWER, 0x3B)
        self.write_reg(REG_USB_CTRL, 0x08)

    def sensor_id(self):
        """Return (product, version) from sensor registers 0x0A/0x0B, or (None, None)."""
        return self.sccb_read(SREG_PRODUCT_ID), self.sccb_read(SREG_VERSION)

    def check_sensor(self):
        """Raise unless the sensor identifies as an OV5620, which is what our tables are for."""
        product, version = self.sensor_id()
        if product is None or version is None:
            raise ScannerError(
                "the sensor did not answer on the SCCB bus; unplug the scanner and plug it in again")
        if product != 0x56 or (version & 0xF0) != 0x20:
            raise ScannerError(
                f"unexpected sensor 0x{product:02X}/0x{version:02X}; this driver is for the OV5620 "
                f"(0x56/0x2x) used by this scanner")
        return product, version

    def init_sensor(self, log=None):
        """Power up, verify the sensor, and write the mode tables. Call once after opening."""
        self.power_on()
        product, version = self.check_sensor()
        if log:
            log(f"OV5620 found (ID 0x{product:02X}/0x{version:02X})")

        nak = []
        for reg, value in ov5620.SENSOR_INIT:
            if not self.sccb_write(reg, value):
                nak.append(reg)
            if reg == 0x12 and value & 0x80:
                time.sleep(0.05)        # software reset: give the sensor time to come back
        if nak:
            raise ScannerError(
                "the sensor rejected " + ", ".join(f"0x{r:02X}" for r in nak[:6]) +
                f" ({len(nak)} registers); unplug the scanner and plug it in again")
        for reg, value in ov5620.BRIDGE_INIT:
            self.write_reg(reg, value)
        if log:
            log(f"wrote {len(ov5620.SENSOR_INIT)} sensor and {len(ov5620.BRIDGE_INIT)} bridge registers")

    # ------------------------------------------------------------- exposure

    def set_auto_exposure(self, enabled):
        """Turn the sensor's own exposure, gain and white balance loops on or off.

        They are left off for scanning: they meter for an average scene, but a backlit negative is
        nearly all highlight, so they drive the film base into clipping and drift between frames.
        """
        bits = COM8_AEC | COM8_AWB | COM8_AGC
        self.update_sccb(SREG_COM8, bits if enabled else 0x00, bits)
        if not enabled:
            self.sccb_write(SREG_EXPOSURE_HI, 0x00)

    def set_exposure(self, value):
        value = max(EXPOSURE_MIN, min(EXPOSURE_MAX, int(value)))
        self.sccb_write(SREG_EXPOSURE, value)
        return value

    def set_gain(self, value):
        value = max(GAIN_MIN, min(GAIN_MAX, int(value)))
        self.sccb_write(SREG_GAIN, value)
        return value

    def set_channel_gains(self, blue, red):
        """Set the sensor's blue and red channel gains (0x40 is unity in the vendor's table).

        Raising these to match the film's orange mask is what keeps the blue channel out of the
        noise: through colour negative base, blue gets a small fraction of the light red does.
        """
        blue = max(0, min(255, int(blue)))
        red = max(0, min(255, int(red)))
        self.sccb_write(SREG_BLUE_GAIN, blue)
        self.sccb_write(SREG_RED_GAIN, red)
        return blue, red

    # --------------------------------------------------------------- stream

    def start_stream(self):
        if self._streaming:
            return
        self.dev.set_interface_altsetting(INTERFACE, ALT_STREAM)
        self.write_reg(REG_USB_CTRL, 0x00)
        self._iso = IsoStream(self.dev, EP_IMAGE, ISO_PACKET)
        self._reset_frame()
        self._fid = None
        self._streaming = True

    def stop_stream(self):
        if not self._streaming:
            return
        self._streaming = False
        try:
            if self._iso is not None:
                self._iso.close()
        finally:
            self._iso = None
            self._reset_frame()
            self._fid = None
            try:
                self.write_reg(REG_USB_CTRL, 0x08)
                self.dev.set_interface_altsetting(INTERFACE, ALT_IDLE)
            except usb.core.USBError:
                pass

    @contextmanager
    def streaming(self):
        self.start_stream()
        try:
            yield self
        finally:
            self.stop_stream()

    def _reset_frame(self):
        self._filled = 0
        self._overrun = False

    def read_frame(self, timeout=6.0, should_stop=None):
        """Return the next complete raw Bayer frame as `bytes`, or None on timeout/cancel.

        Packets are reassembled on the frame-ID bit in the 12-byte payload header, which toggles
        between frames. A frame that does not come out at exactly FRAME_BYTES is dropped rather
        than returned short: a frame missing bytes in the middle shears every row after the gap.

        Payloads are copied into `_buf` as they arrive, never held as views. The views `poll()`
        returns alias the transfer buffers, which libusb resubmits and overwrites within
        milliseconds, long before a whole frame has been collected.
        """
        if not self._streaming:
            raise ScannerError("the stream is not running")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if should_stop is not None and should_stop():
                return None
            for packet in self._iso.poll(0.002):
                if len(packet) < 12:
                    continue
                header_len = packet[0]
                if header_len < 12 or header_len > len(packet) or packet[1] & 0x40:
                    continue            # malformed, or the bridge flagged an error
                fid = packet[1] & 0x01
                frame = None
                if self._fid is not None and fid != self._fid:
                    if self._filled == FRAME_BYTES and not self._overrun:
                        frame = bytes(self._buf)
                    self._reset_frame()
                self._fid = fid
                payload = packet[header_len:]
                end = self._filled + len(payload)
                if end > FRAME_BYTES:
                    self._overrun = True
                    self._filled = FRAME_BYTES
                else:
                    self._buf[self._filled:end] = payload
                    self._filled = end
                if frame is not None:
                    return frame
        return None

    # ---------------------------------------------------------------- misc

    def read_button(self, timeout_ms=50):
        """Poll the button endpoint. Returns the packet bytes, or None if nothing was waiting."""
        try:
            data = self.dev.read(EP_BUTTON, 64, timeout=timeout_ms)
        except usb.core.USBError:
            return None
        return bytes(data) or None

    def close(self):
        try:
            self.stop_stream()
            self.power_off()
        except usb.core.USBError:
            pass
        finally:
            try:
                usb.util.release_interface(self.dev, INTERFACE)
                usb.util.dispose_resources(self.dev)
            except usb.core.USBError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
