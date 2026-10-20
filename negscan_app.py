#!/usr/bin/env python3
"""Launch Negscan. Run with: ./.venv/bin/python negscan_app.py"""
import sys

from PySide6.QtWidgets import QApplication

from scanner_app.icon import app_icon
from scanner_app.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Negscan")
    app.setApplicationDisplayName("Negscan")
    # Sets the Dock icon too, which otherwise shows the Python interpreter's.
    app.setWindowIcon(app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
