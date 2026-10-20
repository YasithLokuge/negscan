"""The parts that differ between macOS, Windows and Linux, and between source and a frozen build.

Kept in one place so the rest of the app can stay platform-agnostic. Nothing here imports Qt
widgets, so it can be used from the CLI too.
"""
import os
import subprocess
import sys

IS_FROZEN = getattr(sys, "frozen", False)
IS_MAC = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"

# Names a packaged libusb can have, in the order worth trying.
_LIBUSB_NAMES = ("libusb-1.0.dll", "libusb-1.0.0.dylib", "libusb-1.0.dylib",
                 "libusb-1.0.so.0", "libusb-1.0.so")


def resource_root():
    """Where the app's data files live: the bundle when frozen, the project tree otherwise."""
    if IS_FROZEN:
        # PyInstaller unpacks to _MEIPASS; a onedir build sets it to the app directory.
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts):
    """Absolute path to a bundled data file, e.g. resource_path("assets", "icon.svg")."""
    return os.path.join(resource_root(), *parts)


def bundled_libusb():
    """Path to a libusb shipped beside the app, or None to fall back to the system one.

    A frozen build cannot rely on the machine having libusb: ctypes.util.find_library looks it up
    at run time, which PyInstaller cannot follow, so the library is listed in the spec file and
    found here instead.
    """
    roots = [resource_root()]
    if IS_FROZEN:
        exe_dir = os.path.dirname(sys.executable)
        roots += [exe_dir, os.path.join(exe_dir, "..", "Frameworks")]
    for root in roots:
        for name in _LIBUSB_NAMES:
            candidate = os.path.normpath(os.path.join(root, name))
            if os.path.isfile(candidate):
                return candidate
    # Running from source: libusb-package is optional, but if it is installed it saves the user
    # from having to provide a system libusb -- which on Windows they otherwise have to find.
    try:
        import libusb_package
        path = str(libusb_package.get_library_path() or "")
        if path and os.path.isfile(path):
            return path
    except Exception:
        pass
    return None


_backend = None
_backend_looked_up = False


def usb_backend():
    """The pyusb backend to use, preferring a bundled libusb. None means "let pyusb decide"."""
    global _backend, _backend_looked_up
    if _backend_looked_up:
        return _backend
    _backend_looked_up = True
    library = bundled_libusb()
    if library:
        import usb.backend.libusb1
        _backend = usb.backend.libusb1.get_backend(find_library=lambda _: library)
    return _backend


def missing_libusb_message():
    """What to tell someone whose machine cannot load libusb at all."""
    if IS_WINDOWS:
        return ("libusb could not be loaded. On Windows the scanner also needs a WinUSB driver "
                "bound to it with Zadig; see the README.")
    if IS_MAC:
        return "libusb could not be loaded. Install it with: brew install libusb"
    return ("libusb could not be loaded. Install it with your package manager, for example: "
            "sudo apt install libusb-1.0-0")


def pictures_dir(app_name="Negscan"):
    """The default place to put scans, following each platform's own convention."""
    try:
        from PySide6.QtCore import QStandardPaths
        base = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
    except Exception:                                    # pragma: no cover - Qt always present
        base = ""
    if not base:
        base = os.path.join(os.path.expanduser("~"), "Pictures")
    return os.path.join(base, app_name)


def reveal(path):
    """Show a file in the system file manager. Returns False if nothing could be launched."""
    if not path or not os.path.exists(path):
        return False
    folder = os.path.dirname(path) or "."
    try:
        if IS_MAC:
            subprocess.run(["open", "-R", path], check=False)
        elif IS_WINDOWS:
            # explorer returns a non-zero exit code even when it works, so its status is ignored.
            subprocess.run(["explorer", "/select,", os.path.normpath(path)], check=False)
        else:
            if subprocess.run(
                ["dbus-send", "--session", "--dest=org.freedesktop.FileManager1",
                 "--type=method_call", "/org/freedesktop/FileManager1",
                 "org.freedesktop.FileManager1.ShowItems",
                 f"array:string:file://{path}", "string:"],
                    check=False, capture_output=True).returncode != 0:
                subprocess.run(["xdg-open", folder], check=False)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
