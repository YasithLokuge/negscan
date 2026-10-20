"""The app icon.

Qt renders the SVG at whatever size it is asked for, so one file covers the window, the Dock and
the task switcher. `assets/icon.png` is a fallback for builds without QtSvg, and
`assets/Negscan.icns` is only needed if the app is ever wrapped in a .app bundle. Both are produced
from the SVG by scripts/make_icon.py.
"""
import os

from PySide6.QtGui import QIcon

from .platform_support import resource_path

SVG = resource_path("assets", "icon.svg")
PNG = resource_path("assets", "icon.png")


def app_icon():
    """Return the app icon, or an empty QIcon if the assets are missing."""
    for path in (SVG, PNG):
        if os.path.exists(path):
            icon = QIcon(path)
            if not icon.isNull():
                return icon
    return QIcon()
