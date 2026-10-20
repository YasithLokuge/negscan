"""Command line access to the scanner, for scripting and for checking the hardware without a GUI.

    python -m scanner_app.cli info
    python -m scanner_app.cli scan out.png [--frames 4] [--film color|bw|slide] [--raw out.raw]
    python -m scanner_app.cli raw out.raw [--exposure 50]
"""
import argparse
import sys
import time

import cv2
import numpy as np

from . import ov5620, processing, raw
from .capture import DEFAULT_EXPOSURE, auto_expose, measure_level
from .usb_scanner import Scanner, ScannerError, find_device

FILM_CHOICES = {"color": processing.FILM_COLOR_NEGATIVE,
                "bw": processing.FILM_BW_NEGATIVE,
                "slide": processing.FILM_POSITIVE}


def cmd_info(args):
    dev = find_device()
    if dev is None:
        print("scanner not found (USB 05a9:1550)")
        return 1
    with Scanner() as s:
        s.power_on()
        product, version = s.sensor_id()
        if product is None:
            print("the sensor did not answer on the SCCB bus")
            return 1
        print("bridge      OV550 (USB 05a9:1550)")
        print(f"sensor      0x0A=0x{product:02X} 0x0B=0x{version:02X} -> "
              f"{'OV5620' if (version & 0xF0) == 0x20 else 'unknown'}")
        print(f"frame       {ov5620.WIDTH} x {ov5620.HEIGHT} raw Bayer {ov5620.BAYER} "
              f"({ov5620.FRAME_BYTES} bytes)")
        regs = " ".join(f"{r:02X}={s.read_reg(r):02X}"
                        for r in (0x20, 0x21, 0x23, 0x31, 0xE0, 0xE7))
        print("bridge regs " + regs)
    return 0


def _grab(scanner, count, exposure=None, verbose=True):
    """Stream and return `count` raw mosaics, after settling the exposure."""
    scanner.set_auto_exposure(False)
    scanner.set_gain(0)
    scanner.start_stream()
    scanner.read_frame()                       # first frame after a start is always black
    if exposure is None:
        exposure = auto_expose(scanner, log=print if verbose else None)
    else:
        scanner.set_exposure(exposure)
        scanner.read_frame()
        scanner.read_frame()
    mosaics = []
    deadline = time.time() + 10 + 2 * count
    while len(mosaics) < count and time.time() < deadline:
        frame = scanner.read_frame(timeout=5.0)
        if frame is not None:
            mosaics.append(raw.as_mosaic(frame))
    scanner.stop_stream()
    if not mosaics:
        raise ScannerError("no complete frame arrived from the scanner")
    if verbose:
        print(f"exposure {exposure}, {len(mosaics)} frame(s), "
              f"peak level {measure_level(mosaics[-1]):.0f}/255")
    return mosaics


def cmd_scan(args):
    with Scanner() as s:
        s.init_sensor(log=print)
        mosaics = _grab(s, args.frames, args.exposure)
    stacked = processing.average_frames(mosaics) if len(mosaics) > 1 else mosaics[0]
    if args.raw:
        stacked.tofile(args.raw)
        print(f"raw mosaic -> {args.raw}")
    bgr = raw.debayer(stacked.tobytes())
    settings = processing.Settings(film=FILM_CHOICES[args.film], gamma=args.gamma,
                                   saturation=args.saturation, rotation=args.rotate)
    out = processing.process(bgr, settings)
    cv2.imwrite(args.output, out, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    print(f"{out.shape[1]}x{out.shape[0]} -> {args.output}")
    return 0


def cmd_raw(args):
    with Scanner() as s:
        s.init_sensor(log=print)
        mosaics = _grab(s, args.frames, args.exposure)
    stacked = processing.average_frames(mosaics) if len(mosaics) > 1 else mosaics[0]
    stacked.tofile(args.output)
    print(f"{ov5620.WIDTH}x{ov5620.HEIGHT} raw {ov5620.BAYER} -> {args.output}")
    return 0


def cmd_develop(args):
    """Turn a previously saved raw mosaic into a picture, with no hardware involved."""
    mosaic = np.fromfile(args.input, dtype=np.uint8)
    bgr = raw.debayer(mosaic.tobytes())
    settings = processing.Settings(film=FILM_CHOICES[args.film], gamma=args.gamma,
                                   saturation=args.saturation, rotation=args.rotate)
    cv2.imwrite(args.output, processing.process(bgr, settings))
    print(f"{args.input} -> {args.output}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="scanner_app.cli", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="identify the scanner and its sensor").set_defaults(func=cmd_info)

    def add_capture_args(sp):
        sp.add_argument("--frames", type=int, default=4,
                        help="frames to average (default 4; more means less noise)")
        sp.add_argument("--exposure", type=int, default=None,
                        help=f"exposure register 16-255 (default: metered, around {DEFAULT_EXPOSURE})")

    sp = sub.add_parser("scan", help="scan and save a finished picture")
    sp.add_argument("output")
    sp.add_argument("--film", choices=FILM_CHOICES, default="color")
    sp.add_argument("--gamma", type=float, default=1.0)
    sp.add_argument("--saturation", type=float, default=1.0,
                    help="0 greyscale, 1 unchanged, 2 double (default 1)")
    sp.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), default=0,
                    help="quarter turns clockwise (default 0)")
    sp.add_argument("--raw", default=None, help="also write the averaged raw mosaic here")
    add_capture_args(sp)
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("raw", help="save the raw Bayer mosaic only")
    sp.add_argument("output")
    add_capture_args(sp)
    sp.set_defaults(func=cmd_raw)

    sp = sub.add_parser("develop", help="process a saved raw mosaic, without the scanner")
    sp.add_argument("input")
    sp.add_argument("output")
    sp.add_argument("--film", choices=FILM_CHOICES, default="color")
    sp.add_argument("--gamma", type=float, default=1.0)
    sp.add_argument("--saturation", type=float, default=1.0,
                    help="0 greyscale, 1 unchanged, 2 double (default 1)")
    sp.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), default=0,
                    help="quarter turns clockwise (default 0)")
    sp.set_defaults(func=cmd_develop)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except ScannerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
