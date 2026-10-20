#!/bin/sh
# Install Negscan into /opt, add a menu entry, and allow non-root access to the scanner.
# Run from the folder this script lives in:   sudo ./install.sh
set -eu

PREFIX="${PREFIX:-/opt/negscan}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "This needs root to write to $PREFIX and /etc/udev. Re-run with sudo." >&2
    exit 1
fi

echo "Installing to $PREFIX"
rm -rf "$PREFIX"
mkdir -p "$PREFIX"
cp -a "$HERE/Negscan/." "$PREFIX/"

install -Dm644 "$HERE/negscan.desktop" /usr/share/applications/negscan.desktop
install -Dm644 "$HERE/negscan.png" /usr/share/icons/hicolor/512x512/apps/negscan.png
sed -i "s|^Exec=.*|Exec=$PREFIX/Negscan|" /usr/share/applications/negscan.desktop

echo "Installing the udev rule so the scanner can be opened without root"
install -Dm644 "$HERE/99-negscan.rules" /etc/udev/rules.d/99-negscan.rules
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger --subsystem-match=usb 2>/dev/null || true

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database || true

echo
echo "Done. Launch it from your applications menu, or run: $PREFIX/Negscan"
echo "Unplug and replug the scanner so the new udev rule applies."
echo "To check the install:  $PREFIX/Negscan --check"
