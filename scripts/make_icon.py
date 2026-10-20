#!/usr/bin/env python3
"""Render assets/icon.svg into the PNG and .icns files the app and a bundle need.

    ./.venv/bin/python scripts/make_icon.py

Writes assets/icon.png (1024 px, used at runtime via QIcon) and, on macOS, assets/Negscan.icns
built with iconutil for use as a bundle icon. Rendering goes through Qt's own SVG engine, so what
the window shows is what this produced.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

from PySide6.QtCore import QRectF, Qt
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
