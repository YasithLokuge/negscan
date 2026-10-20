"""Turning the scanner's raw Bayer frames into BGR images.

The sensor sends one byte per photosite in an RGGB mosaic, and the picture arrives upside down:
the init table mirrors and flips the sensor readout, and the scanner's optics invert it again. Both
functions here undo that with a 180-degree rotation, so their output is the right way up.
"""
import cv2
import numpy as np

from . import ov5620

WIDTH, HEIGHT = ov5620.WIDTH, ov5620.HEIGHT
FRAME_BYTES = ov5620.FRAME_BYTES

# OpenCV names a Bayer conversion after the second row and second/third columns, not after the
# 2x2 block at the origin, so its names are the opposite of the usual ones: COLOR_BayerBG2BGR
# decodes a sensor whose first photosite is red. Getting this backwards swaps red and blue in
# every scan, so tests/test_raw.py checks both decoders against a mosaic of known colour.
_BAYER_TO_BGR = {"RGGB": cv2.COLOR_BayerBG2BGR, "BGGR": cv2.COLOR_BayerRG2BGR,
                 "GRBG": cv2.COLOR_BayerGB2BGR, "GBRG": cv2.COLOR_BayerGR2BGR}


def as_mosaic(frame):
    """View a raw frame (bytes or bytearray) as a HEIGHT x WIDTH uint8 array."""
    if len(frame) != FRAME_BYTES:
        raise ValueError(f"expected {FRAME_BYTES} bytes, got {len(frame)}")
    return np.frombuffer(frame, dtype=np.uint8).reshape(HEIGHT, WIDTH)


def debayer(frame, pattern=ov5620.BAYER):
    """Full-resolution demosaic. Returns a HEIGHT x WIDTH x 3 BGR uint8 image, right way up."""
    bgr = cv2.cvtColor(as_mosaic(frame), _BAYER_TO_BGR[pattern])
    return cv2.rotate(bgr, cv2.ROTATE_180)


def debayer_half(frame, pattern=ov5620.BAYER):
    """Half-resolution demosaic for the live preview: 1296 x 840, right way up.

    Each output pixel comes from one complete 2x2 Bayer quad, so there is no interpolation and no
    colour fringing, and it is several times faster than demosaicing 4.4 MP and scaling down.
    """
    m = as_mosaic(frame)
    quad = {"RGGB": ((0, 0), (0, 1), (1, 0), (1, 1)),
            "BGGR": ((1, 1), (0, 1), (1, 0), (0, 0)),
            "GRBG": ((0, 1), (0, 0), (1, 1), (1, 0)),
            "GBRG": ((1, 0), (0, 0), (1, 1), (0, 1))}[pattern]
    (ry, rx), (g1y, g1x), (g2y, g2x), (by, bx) = quad
    red = m[ry::2, rx::2]
    blue = m[by::2, bx::2]
    green = m[g1y::2, g1x::2].astype(np.uint16) + m[g2y::2, g2x::2]
    bgr = np.dstack([blue, (green >> 1).astype(np.uint8), red])
    return cv2.rotate(bgr, cv2.ROTATE_180)
