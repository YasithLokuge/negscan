"""Tests for the USB layer that do not need the scanner plugged in."""
import ctypes
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from scanner_app import libusb_iso, ov5620  # noqa: E402
import gen_sensor_table  # noqa: E402

PROJECT = os.path.join(os.path.dirname(__file__), "..")


class TransferLayoutTests(unittest.TestCase):
    """The layout of struct libusb_transfer, which is easy to get wrong and fails opaquely.

    Its last member is a C99 flexible array, so it starts at the next 4-byte boundary after
    num_iso_packets -- offset 60 -- while sizeof() is padded to 64. Writing the per-packet lengths
    at 64 leaves libusb reading zero for packet 0, and macOS rejects the transfer with a bare
    LIBUSB_ERROR_INVALID_PARAM that says nothing about why.
    """

    def test_struct_size_and_descriptor_offset(self):
        self.assertEqual(ctypes.sizeof(libusb_iso.Transfer), 64)
        self.assertEqual(libusb_iso.DESC_OFFSET, 60)

    def test_descriptor_offset_follows_num_iso_packets(self):
        field = libusb_iso.Transfer.num_iso_packets
        self.assertEqual(field.offset + field.size, libusb_iso.DESC_OFFSET)

    def test_packet_descriptor_size(self):
        self.assertEqual(ctypes.sizeof(libusb_iso.IsoPacketDesc), 12)


HAVE_SET_FILES = os.path.exists(gen_sensor_table.DEFAULT_SET)


@unittest.skipUnless(HAVE_SET_FILES, "vendor .set files are not redistributable; see README")
class SetFileTests(unittest.TestCase):
    def test_parses_the_shipped_5620_file(self):
        tables = gen_sensor_table.parse(gen_sensor_table.DEFAULT_SET)
        sensor = tables[(gen_sensor_table.MODE_KEY, "CameraSetting")]
        bridge = tables[(gen_sensor_table.MODE_KEY, "UsbSetting")]
        self.assertEqual([(r, v) for r, v, _ in sensor], ov5620.SENSOR_INIT)
        self.assertEqual([(r, v) for r, v, _ in bridge], ov5620.BRIDGE_INIT)

    def test_blank_lines_do_not_end_a_continued_directive(self):
        """The driver's own parser tolerates them, and the tables only make sense if they do."""
        tables = gen_sensor_table.parse(gen_sensor_table.DEFAULT_SET)
        sensor = tables[(gen_sensor_table.MODE_KEY, "CameraSetting")]
        self.assertGreater(len(sensor), 50,
                           "stopping at the first blank line would leave only two writes")

    def test_identifies_this_unit_as_a_5620_not_a_5610(self):
        """Sensor 0x0B reads 0x21 on this scanner, which matches the 5620 file's check, not 5610."""
        folder = os.path.dirname(gen_sensor_table.DEFAULT_SET)
        checks = {}
        for name, key in (("MQT5620RAW.set", "5620MQT_str1"), ("MQT5610RAW.set", "5610MQT_str1")):
            tables = gen_sensor_table.parse(os.path.join(folder, name))
            checks[name] = tables[(key, "MinorVersionCheck")]
        # Each entry is (sccb address, register, mask, expected) read as a triplet plus a tail.
        self.assertEqual(checks["MQT5620RAW.set"][0][:3], (0x60, 0x0B, 0xF0))
        self.assertEqual(checks["MQT5610RAW.set"][0][:3], (0x60, 0x0B, 0xF0))


if __name__ == "__main__":
    unittest.main()
