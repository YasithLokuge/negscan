#!/usr/bin/env python3
"""Build Negscan into something installable for the platform it is run on.

    python packaging/build.py --version 1.2.3

Runs PyInstaller, then wraps the result:

    macOS    a .dmg containing Negscan.app and a shortcut to /Applications
    Windows  a Setup .exe (when Inno Setup is available) and a portable .zip
    Linux    a .tar.gz holding the app, a .desktop file, the udev rule and install.sh

Everything lands in dist/release/. The CI workflow calls this and uploads whatever it finds.
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PACKAGING = PROJECT / "packaging"
DIST = PROJECT / "dist"
RELEASE = DIST / "release"
APP_NAME = "Negscan"

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"


def run(cmd, **kwargs):
    printable = " ".join(str(c) for c in cmd)
    print(f"--> {printable}", flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def arch_tag():
    machine = platform.machine().lower()
    return {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine)


def platform_tag():
    if IS_MAC:
        return "macOS"
    if IS_WINDOWS:
        return "windows"
    return "linux"


def freeze(version):
    """Run PyInstaller. Returns the onedir folder it produced."""
    for stale in (DIST / APP_NAME, DIST / f"{APP_NAME}.app", PROJECT / "build" / "negscan"):
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)
    env = dict(os.environ, NEGSCAN_VERSION=version)
    run([sys.executable, "-m", "PyInstaller", PACKAGING / "negscan.spec",
         "--noconfirm", "--distpath", DIST, "--workpath", PROJECT / "build"], env=env)
    folder = DIST / APP_NAME
    if not folder.is_dir():
        raise SystemExit(f"PyInstaller did not produce {folder}")
    return folder


def package_macos(version):
    app = DIST / f"{APP_NAME}.app"
    if not app.is_dir():
        raise SystemExit(f"expected {app}")
    # Unsigned builds are quarantined on download; strip any local attribute so the DMG is clean.
    subprocess.run(["xattr", "-cr", str(app)], check=False)

    staging = DIST / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copytree(app, staging / app.name, symlinks=True)
    os.symlink("/Applications", staging / "Applications")

    out = RELEASE / f"{APP_NAME}-{version}-macOS-{arch_tag()}.dmg"
    out.unlink(missing_ok=True)
    run(["hdiutil", "create", "-volname", f"{APP_NAME} {version}", "-srcfolder", staging,
         "-ov", "-format", "UDZO", out])
    shutil.rmtree(staging, ignore_errors=True)
    return [out]


def package_windows(version, folder):
    produced = []
    out_zip = RELEASE / f"{APP_NAME}-{version}-windows-{arch_tag()}-portable.zip"
    out_zip.unlink(missing_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in folder.rglob("*"):
            if path.is_file():
                zf.write(path, Path(APP_NAME) / path.relative_to(folder))
    produced.append(out_zip)

    iscc = shutil.which("iscc") or shutil.which("ISCC")
    if not iscc:
        for guess in (r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                      r"C:\Program Files\Inno Setup 6\ISCC.exe"):
            if Path(guess).is_file():
                iscc = guess
                break
    if iscc:
        run([iscc, f"/DMyAppVersion={version}", f"/DMyOutputDir={RELEASE}",
             f"/DMySourceDir={folder}", PACKAGING / "negscan.iss"])
        produced += sorted(RELEASE.glob(f"{APP_NAME}-*-setup.exe"))
    else:
        print("!! Inno Setup not found; shipping the portable zip only", flush=True)
    return produced


def package_linux(version, folder):
    stage_name = f"{APP_NAME}-{version}-linux-{arch_tag()}"
    stage = DIST / stage_name
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(folder, stage / APP_NAME, symlinks=True)
    for name in ("install.sh", "negscan.desktop", "99-negscan.rules"):
        shutil.copy2(PACKAGING / "linux" / name, stage / name)
    os.chmod(stage / "install.sh", 0o755)
    shutil.copy2(PROJECT / "assets" / "icon.png", stage / "negscan.png")

    out = RELEASE / f"{stage_name}.tar.gz"
    out.unlink(missing_ok=True)
    # COPYFILE_DISABLE stops macOS tar from adding ._ AppleDouble files, which matters only when
    # this path is exercised from a Mac but costs nothing on Linux.
    run(["tar", "-czf", out, "-C", DIST, stage_name],
        env=dict(os.environ, COPYFILE_DISABLE="1"))
    shutil.rmtree(stage, ignore_errors=True)
    return [out]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default=os.environ.get("NEGSCAN_VERSION", "0.0.0"))
    args = parser.parse_args(argv)
    version = args.version.lstrip("v") or "0.0.0"

    RELEASE.mkdir(parents=True, exist_ok=True)
    folder = freeze(version)

    if IS_MAC:
        produced = package_macos(version)
    elif IS_WINDOWS:
        produced = package_windows(version, folder)
    else:
        produced = package_linux(version, folder)

    print(f"\n{platform_tag()} {arch_tag()} artifacts:")
    for path in produced:
        print(f"  {path.name}  ({path.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
