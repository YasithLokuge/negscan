# Negscan

An app for scanning film negatives with the Kaiser Baas / OVT USB film scanner
(USB `05a9:1550`: an OmniVision OV550 bridge with an OV5620 sensor). Runs on macOS, Windows and
Linux.

The scanner has no driver outside Windows and no UVC video interface — its only USB interface is
vendor-specific class `0xFF`, so the operating system never lists it as a camera and nothing that
expects a webcam can see it. Negscan drives it directly over libusb instead: it powers up the
bridge, configures the sensor over the bridge's SCCB bus using the register tables from the Windows
driver, reads the isochronous image stream, and develops the raw Bayer frames into positives.

Output is **2592 × 1680** (4.4 MP), the scanner's full frame.

## Install

Download the build for your platform from the
[latest release](../../releases/latest).

| Platform | File | How |
| --- | --- | --- |
| macOS (Intel) | `Negscan-*-macOS-x86_64.dmg` | Open it, drag Negscan to Applications |
| macOS (Apple Silicon) | `Negscan-*-macOS-arm64.dmg` | As above |
| Windows | `Negscan-*-setup.exe` | Run it. A portable `.zip` is also published |
| Linux | `Negscan-*-linux-x86_64.tar.gz` | Unpack, then `sudo ./install.sh` |

The builds are **not code-signed**, so each platform will object the first time:

- **macOS** — right-click the app and choose *Open*, then confirm. Or
  `xattr -dr com.apple.quarantine /Applications/Negscan.app`.
- **Windows** — SmartScreen shows *Windows protected your PC*: *More info* → *Run anyway*.

If the app cannot find the scanner, run it from a terminal with `--check`; it reports what it can
and cannot see:

```
"/Applications/Negscan.app/Contents/MacOS/Negscan" --check     # macOS
"C:\Program Files\Negscan\Negscan.exe" --check                # Windows
/opt/negscan/Negscan --check                                   # Linux
```

### Windows also needs a USB driver

Windows will not let libusb open the scanner until a WinUSB driver is bound to it. Install
[Zadig](https://zadig.akeo.ie/), tick *Options → List All Devices*, select the device with USB ID
`05A9 1550`, choose **WinUSB** as the target driver, and click *Replace Driver*. Removing the
driver in Device Manager restores the original state.

If the vendor's OVT Windows driver is installed, it claims the device first and Negscan will not
see it until Zadig replaces it.

### Linux permissions

`install.sh` puts a udev rule in `/etc/udev/rules.d/99-negscan.rules` so the scanner can be opened
without root. Unplug and replug the scanner afterwards.

The Linux build is made on Ubuntu 22.04. glibc is backward compatible but not forward compatible,
so it runs on that and anything newer, not on older distributions.

## Running from source

```
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python negscan_app.py
```

This needs libusb on the machine: `brew install libusb` (macOS), `sudo apt install libusb-1.0-0`
(Debian/Ubuntu). On Windows, `pip install libusb-package` supplies one. No kernel extension and no
camera permission — the device is not a camera, so nothing in macOS Privacy & Security applies.

## Building the installers

```
./.venv/bin/python -m pip install -r requirements-dev.txt
./.venv/bin/python packaging/build.py --version 0.1.0
```

Output lands in `dist/release/`. The script builds for whichever platform it is run on; PyInstaller
cannot cross-build, which is why the release workflow uses four runners: `macos-15-intel`,
`macos-15`, `windows-latest` and `ubuntu-22.04`.

`macos-15-intel` is the only x86_64 macOS image GitHub still offers and the last one it plans to —
support ends in August 2027. Every other `macos-*` label is Apple Silicon. When it goes, Intel
Macs will need a DMG built locally with the command above.

Tag a commit to publish:

```
git tag v0.1.0 && git push origin v0.1.0
```

### Who can run the workflows

Both workflows gate every job on `github.actor == github.repository_owner`, so only the repository
owner's pushes, tags and manual runs actually build anything. Pushing and `workflow_dispatch`
already require write access; the gate exists for pull requests, which on a public repository
anyone can open and which would otherwise start a build on four runners.

A pull request from anyone else still *creates* a workflow run — its jobs just all skip. To stop
the run being created at all, either delete the `pull_request:` trigger from `ci.yml`, or set
**Settings → Actions → General → Fork pull request workflows from outside collaborators** to
*Require approval for all external contributors*. Two more settings there are worth tightening at
the same time, since neither can be expressed in the workflow files:

- **Actions permissions** — restrict which actions may run, if you would rather not allow any.
- **Workflow permissions** — set the default `GITHUB_TOKEN` to *Read repository contents
  permission*. Both workflows already declare `contents: read` and only the publishing job raises
  it to `contents: write`, so nothing here depends on a permissive default.

1. Put a negative in the holder and insert it, then press **Connect**. The lamp comes on, the app
   meters an exposure, and the live preview starts.
2. Choose the film type:
   - **Color negative**: inverts in density space, which removes the orange mask properly.
   - **B&W negative**: greyscale, inverted.
   - **Positive slide**: no inversion, tone only.
3. If the strip went in sideways, turn the scan with **⟲ / ⟳** or the **[** and **]** keys. It
   applies to the preview and to everything saved afterwards, and it lasts until you quit: the
   rotation belongs to the strip in the holder, not to you, so it is not kept with the other
   settings.
4. Adjust Exposure, Contrast, Gamma and Saturation. Saturation runs from 0 (greyscale) through
   1.00 (unchanged) to 2.00, and is applied last, on the finished tones. Tick **Show original**
   to see the raw negative.
5. Press **Space** or **Capture**. The chosen number of frames is averaged and a lossless PNG is
   written to the scan folder (default `~/Pictures/Negscan`). Developing and saving happen on the
   worker thread, so the window stays live; the button reads **Scanning…** and both it and the
   Space key are ignored until the file is written, so a scan cannot be started on top of another.

### Command line

Useful for scripting, and for checking the hardware without the GUI:

```
./.venv/bin/python -m scanner_app.cli info                 # identify the bridge and sensor
./.venv/bin/python -m scanner_app.cli scan out.png         # scan and develop
./.venv/bin/python -m scanner_app.cli scan out.png --frames 8 --film bw --raw out.raw
./.venv/bin/python -m scanner_app.cli scan out.png --saturation 1.3 --gamma 1.1
./.venv/bin/python -m scanner_app.cli scan out.png --rotate 90
./.venv/bin/python -m scanner_app.cli raw out.raw          # raw Bayer mosaic only
./.venv/bin/python -m scanner_app.cli develop out.raw out.png   # develop a saved mosaic
./.venv/bin/python -m scanner_app.cli develop out.raw grey.png --saturation 0
```

## How it works

- `scanner_app/usb_scanner.py` — the device: bridge registers over vendor control transfers, the
  SCCB bus to the sensor, power and lamp, exposure, and reassembly of the isochronous stream into
  whole frames.
- `scanner_app/libusb_iso.py` — isochronous transfers via libusb's async API, because pyusb has
  none and this scanner sends image data only isochronously.
- `scanner_app/ov5620.py` — the register tables, generated from the Windows driver's own
  `MQT5620RAW.set` by `scripts/gen_sensor_table.py`. Do not edit by hand.
- `scanner_app/raw.py` — Bayer decoding, full resolution for capture and a cheap half-resolution
  path for the preview.
- `scanner_app/processing.py` — negative inversion, levels, tone. No UI, so it is all testable.
- `scanner_app/capture.py` — the worker thread and the auto-exposure loop.
- `scanner_app/main_window.py`, `scanner_app/styles.py` — the window.
- `scanner_app/cli.py` — the command line.
- `scanner_app/platform_support.py` — the handful of things that differ between macOS, Windows and
  Linux, and between running from source and from a frozen build: where data files and libusb
  live, where scans go, and how to show a file in the file manager.
- `packaging/` — the PyInstaller spec, the build script, the Inno Setup script and the Linux
  install files. `.github/workflows/` runs the tests on all three platforms and builds the
  release.
- `scanner_app/icon.py`, `assets/` — the app icon. `assets/icon.svg` is the source; running
  `./.venv/bin/python scripts/make_icon.py` regenerates `icon.png` (used at run time),
  `Negscan.icns` (the macOS bundle) and `Negscan.ico` (embedded in the Windows .exe). Every size
  is rendered from the SVG rather than downscaled from one bitmap, so the 16 px icon is drawn at
  16 px. All three are committed: PyInstaller can only convert a PNG to an .ico when Pillow is
  installed, and `iconutil` exists only on macOS, so the build machines cannot produce them.

`FINDINGS.md` records how the protocol was worked out, and what is still unknown.

## Exposure

The sensor's own auto-exposure is switched off. It meters for an average scene, but a backlit
negative is nearly all highlight, so it drives the film base into clipping and drifts between
frames. Negscan instead finds the largest exposure whose brightest 0.2% still sits at or below 236/255 —
as much signal as possible without clipping the film base. It brackets that value and bisects,
because the sensor's response does not pass through the origin and a plain feedback loop just
oscillates between two neighbouring register values. A first metering costs about six frames; later
captures resume from the last answer and usually settle in two.

## Tests

```
./.venv/bin/python -m unittest discover -s tests -v
```

100 tests covering the image pipeline, saturation, rotation, the Bayer decoders, the register tables, the
`libusb` structure layout, the icon assets, the auto-exposure search against a modelled sensor,
the window's capture locking and sidebar layout (driven offscreen, with real key events),
and the packaging files, workflow permissions and the Windows icon.
They need no hardware; three skip unless the vendor `.set` files are present, which they are not
in this repository. The scanner itself was exercised separately: repeated scans, reconnects, and
recovery after killing a process mid-stream.
