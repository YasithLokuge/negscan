#!/usr/bin/env python3
"""Launch Negscan.

    negscan_app.py              open the window
    negscan_app.py --check      report what the app can see, then exit
    negscan_app.py --version    print the version

From source, run it with the project's interpreter: ./.venv/bin/python negscan_app.py
"""
import sys

__version__ = "0.1.0"


def check():
    """Print a short diagnosis. The usual problems are libusb and, on Windows, the driver."""
    from scanner_app import platform_support as ps

    print(f"Negscan {__version__}")
    print(f"  python     {sys.version.split()[0]} on {sys.platform}")
    print(f"  frozen     {ps.IS_FROZEN}")
    print(f"  resources  {ps.resource_root()}")
    print(f"  libusb     {ps.bundled_libusb() or 'system (not bundled)'}")

    # Actually run the image pipeline on a blank frame rather than just importing it. In a frozen
    # build a missing OpenCV, or a Bayer constant that did not survive packaging, would otherwise
    # stay hidden until the first preview frame arrived.
    try:
        import cv2
        import numpy
        from scanner_app import ov5620, processing, raw

        preview = raw.debayer_half(bytes(ov5620.FRAME_BYTES))
        processing.process(preview, processing.Settings(rotation=90))
        print(f"  imaging    OpenCV {cv2.__version__}, numpy {numpy.__version__}")
        print(f"  frame      {ov5620.WIDTH}x{ov5620.HEIGHT} Bayer {ov5620.BAYER}, "
              f"pipeline ran on a {preview.shape[1]}x{preview.shape[0]} preview")
    except Exception as e:
        print(f"  imaging    FAILED: {e}")
        return 1

    try:
        from scanner_app.usb_scanner import PID, VID, ScannerError, find_device
    except Exception as e:                               # pragma: no cover - import-time failure
        print(f"  scanner    could not load the USB layer: {e}")
        return 1
    try:
        device = find_device()
    except ScannerError as e:
        print(f"  scanner    {e}")
        return 1
    if device is None:
        print(f"  scanner    not found (USB {VID:04x}:{PID:04x})")
        if ps.IS_WINDOWS:
            print("             on Windows the device also needs a WinUSB driver; see the README")
        return 1
    print(f"  scanner    found at USB {VID:04x}:{PID:04x}")
    return 0


def main():
    args = sys.argv[1:]
    if "--version" in args:
        print(__version__)
        return 0
    if "--check" in args:
        return check()

    from PySide6.QtWidgets import QApplication

    from scanner_app.icon import app_icon
    from scanner_app.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Negscan")
    app.setApplicationDisplayName("Negscan")
    app.setApplicationVersion(__version__)
    # Sets the Dock/taskbar icon too, which otherwise shows the Python interpreter's.
    app.setWindowIcon(app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
