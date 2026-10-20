#!/usr/bin/env python3
"""Regenerate scanner_app/ov5620.py from the Windows driver's camera-setting file.

    ./.venv/bin/python scripts/gen_sensor_table.py

The .set files ship with the OVT Windows driver and are parsed by ov550ivx.sys itself, not by
Windows setup, so they are not quite INF files: continued directives contain blank lines, which a
strict INF parser would treat as the end of the value. Blank lines are treated as continuations
here, which is the only reading under which the tables make sense.
"""
import argparse
import os
import re
import sys

HEAD = re.compile(r"^HKR,%(\w+)%\s*,\s*(\w+)\s*,\s*1\s*,(.*)$")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SET = os.path.join(PROJECT, "new", "OVT scanner x64", "Windows", "OvtCam", "MQT5620RAW.set")
DEFAULT_OUT = os.path.join(PROJECT, "scanner_app", "ov5620.py")
MODE_KEY = "5620MQT_str53"          # VideoStream\2592x1944RAW\FrameRate1


def parse(path):
    """Return {(section, name): [(register, value, mask), ...]} for every binary directive."""
    tables, key, body = {}, None, []
    with open(path, encoding="latin-1") as handle:
        for raw_line in handle:
            line = re.sub(r";.*$", "", raw_line.rstrip("\r\n")).rstrip()
            if key is not None:
                if not line:
                    continue                       # blank line inside a continued directive
                body.append(line.rstrip("\\"))
                if not line.endswith("\\"):
                    tables[key], key = _triplets(" ".join(body)), None
                continue
            match = HEAD.match(line)
            if not match:
                continue
            key, first = (match.group(1), match.group(2)), match.group(3).rstrip()
            body = [first.rstrip("\\")]
            if not first.endswith("\\"):
                tables[key], key = _triplets(body[0]), None
    if key is not None:
        tables[key] = _triplets(" ".join(body))
    return tables


def _triplets(body):
    nums = [int(x, 16) for x in re.findall(r"[0-9a-fA-F]{1,2}", body)]
    return [tuple(nums[i:i + 3]) for i in range(0, len(nums) - 2, 3)]


def format_rows(rows, per_line=6):
    out = []
    for i in range(0, len(rows), per_line):
        out.append("    " + ", ".join(f"(0x{r:02X}, 0x{v:02X})" for r, v, _ in rows[i:i + per_line]) + ",")
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--set-file", default=DEFAULT_SET)
    p.add_argument("--out", default=DEFAULT_OUT)
    args = p.parse_args(argv)

    tables = parse(args.set_file)
    try:
        sensor = tables[(MODE_KEY, "CameraSetting")]
        bridge = tables[(MODE_KEY, "UsbSetting")]
    except KeyError:
        print(f"error: {args.set_file} has no {MODE_KEY} tables", file=sys.stderr)
        return 1
    masked = [(r, v, m) for r, v, m in sensor + bridge if m != 0xFF]
    if masked:
        print(f"error: unsupported masked writes: {masked}", file=sys.stderr)
        return 1

    window = {r: v for r, v, _ in sensor}
    width = (window[0x18] - window[0x17]) * 16
    height = (window[0x1A] - window[0x19]) * 8

    rel = os.path.relpath(args.set_file, PROJECT)
    text = f'''"""Register tables for the OV5620 sensor behind the OV550 bridge, in this scanner's only mode.

Generated from the Windows driver's own camera-setting file:
    {rel}, key {MODE_KEY} (VideoStream FrameRate1).
Regenerate with scripts/gen_sensor_table.py. Do not edit by hand.

The sensor is identified by SCCB registers 0x0A/0x0B: 0x56 with (0x0B & 0xF0) == 0x20 is the
OV5620. The 0x10 variant is the OV5610, which uses MQT5610RAW.set and is not this unit -- telling
the two apart matters, because the 5610 table writes registers that stop this sensor streaming.
"""

# Output geometry, from the window registers in the table below: 0x17/0x18 are the horizontal
# limits in units of 16 pixels and 0x19/0x1A the vertical limits in units of 8.
# (0x{window[0x18]:02X} - 0x{window[0x17]:02X}) * 16 = {width}, (0x{window[0x1A]:02X} - 0x{window[0x19]:02X}) * 8 = {height}.
WIDTH = {width}
HEIGHT = {height}
FRAME_BYTES = WIDTH * HEIGHT

# Colour filter order of the top-left 2x2 as the data arrives. Established by scanning a colour
# negative and rendering all four phases: only this one gives blue sky, green foliage and plausible
# skin, and the other diagonal phase swaps them for green sky and purple cloth. It agrees with the
# gain registers: writing the vendor's "BlueGain" (sensor 0x01) brightens the (0,0) sub-lattice.
BAYER = "BGGR"

# Written over SCCB to the sensor at address 0x60, in order. 0x12 bit 7 is a software reset, so the
# writer has to pause after it; 0x04 = 0xF0 mirrors and flips the readout.
SENSOR_INIT = [
{format_rows(sensor)}
]

# Written to the bridge as vendor control transfers, after the sensor table. 0x88-0x8B carry the
# frame size the bridge expects ({width} x {height}), 0x8D selects raw input and 0x1C/0x1D switch
# JPEG off and isochronous raw output on.
BRIDGE_INIT = [
{format_rows(bridge)}
]
'''
    with open(args.out, "w") as handle:
        handle.write(text)
    print(f"{args.out}: {len(sensor)} sensor writes, {len(bridge)} bridge writes, {width}x{height}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
