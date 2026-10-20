#!/usr/bin/env python3
"""Render assets/icon.svg into every icon file the app and its installers need.

    ./.venv/bin/python scripts/make_icon.py

Writes:
    assets/icon.png      1024 px, used at run time via QIcon
    assets/Negscan.ico   Windows: embedded in the .exe by PyInstaller
    assets/Negscan.icns  macOS: the .app bundle icon (needs iconutil, so macOS only)

Every size is rendered from the SVG rather than downscaled from one large bitmap, so the 16 px
icon is drawn at 16 px instead of being squashed into it. All three outputs are committed, because
the build machines have no way to produce them: PyInstaller can only convert a PNG to an .ico if
Pillow happens to be installed, and iconutil exists only on macOS.
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(PROJECT, "assets")
SVG = os.path.join(ASSETS, "icon.svg")

# The sizes iconutil expects in an .iconset, as (pixel size, file name).
ICONSET = [(16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
           (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
           (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
           (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
           (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png")]

# What Windows asks for. 256 is stored as PNG, the rest as uncompressed DIBs: Vista and later
# accept PNG at any size, but small BMP entries are what every shell has always understood.
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
ICO_PNG_FROM = 128


_APP = None


def _ensure_app():
    """Qt needs a QGuiApplication alive before anything can paint, including offscreen."""
    global _APP
    if _APP is None and QGuiApplication.instance() is None:
        _APP = QGuiApplication([])


def render(renderer, size, path):
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    if not image.save(path, "PNG"):
        raise OSError(f"could not write {path}")


def _render_image(renderer, size):
    image = QImage(size, size, QImage.Format_ARGB32)      # straight alpha, which is what ICO wants
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def _png_bytes(image):
    # The QByteArray has to be held in a name: QBuffer only borrows it, so passing a temporary
    # lets Python collect it while Qt is still writing into it, which segfaults.
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def _dib_bytes(image):
    """One ICO entry as an uncompressed 32-bit DIB.

    The format is a BITMAPINFOHEADER whose height is doubled to cover the colour rows plus a 1-bit
    AND mask, then BGRA pixels bottom-up. The mask is left all zeros: the alpha channel already
    carries transparency, and every Windows version that matters honours it.
    """
    width, height = image.width(), image.height()
    # Read through pixel() rather than the raw buffer: these entries are at most 64x64, so the
    # cost is irrelevant, and it avoids depending on QImage's stride and buffer lifetime.
    rows = bytearray()
    for y in range(height - 1, -1, -1):                   # DIB rows run bottom-up
        for x in range(width):
            argb = image.pixel(x, y)
            rows += bytes(((argb >> 0) & 0xFF,            # blue
                           (argb >> 8) & 0xFF,            # green
                           (argb >> 16) & 0xFF,           # red
                           (argb >> 24) & 0xFF))          # alpha
    pixels = bytes(rows)
    mask_stride = ((width + 31) // 32) * 4
    mask = b"\x00" * (mask_stride * height)
    header = struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0,
                         len(pixels) + len(mask), 0, 0, 0, 0)
    return header + pixels + mask


def write_ico(renderer, path, sizes=ICO_SIZES):
    """Assemble a multi-resolution Windows .ico."""
    images = []
    for size in sizes:
        image = _render_image(renderer, size)
        data = _png_bytes(image) if size >= ICO_PNG_FROM else _dib_bytes(image)
        images.append((size, data))

    offset = 6 + 16 * len(images)
    directory, payload = b"", b""
    for size, data in images:
        directory += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                                 len(data), offset)
        payload += data
        offset += len(data)
    with open(path, "wb") as handle:
        handle.write(struct.pack("<HHH", 0, 1, len(images)) + directory + payload)
    return path


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--svg", default=SVG)
    p.add_argument("--out-dir", default=ASSETS,
                   help="where to write icon.png and Negscan.icns (default: assets/). Rendering a "
                        "variant without this would quietly overwrite the real icon.")
    p.add_argument("--preview", default=None,
                   help="also write a strip of small sizes here, to check legibility")
    args = p.parse_args(argv)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _ensure_app()
    renderer = QSvgRenderer(args.svg)
    if not renderer.isValid():
        print(f"error: {args.svg} is not valid SVG", file=sys.stderr)
        return 1

    os.makedirs(args.out_dir, exist_ok=True)
    png = os.path.join(args.out_dir, "icon.png")
    render(renderer, 1024, png)
    print(f"wrote {png}")

    ico = os.path.join(args.out_dir, "Negscan.ico")
    write_ico(renderer, ico)
    print(f"wrote {ico}")

    if args.preview:
        render_preview(renderer, args.preview)
        print(f"wrote {args.preview}")

    if not shutil.which("iconutil"):
        print("iconutil not found; skipping the .icns (it is only needed for an app bundle)")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "Negscan.iconset")
        os.makedirs(iconset)
        for size, name in ICONSET:
            render(renderer, size, os.path.join(iconset, name))
        icns = os.path.join(args.out_dir, "Negscan.icns")
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], check=True)
    print(f"wrote {icns}")
    return 0


def render_preview(renderer, path, sizes=(16, 32, 64, 128, 256)):
    """A single row of the icon at small sizes on a mid grey, for judging it by eye."""
    pad, gap = 16, 16
    width = sum(sizes) + gap * (len(sizes) + 1)
    height = max(sizes) + pad * 2
    strip = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    strip.fill(Qt.gray)
    painter = QPainter(strip)
    painter.setRenderHint(QPainter.Antialiasing, True)
    x = gap
    for size in sizes:
        renderer.render(painter, QRectF(x, (height - size) / 2, size, size))
        x += size + gap
    painter.end()
    strip.save(path, "PNG")


if __name__ == "__main__":
    sys.exit(main())
