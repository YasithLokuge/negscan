# Negscan

A macOS app for scanning film negatives with the Kaiser Baas / OVT USB film scanner
(USB `05a9:1550`: an OmniVision OV550 bridge with an OV5620 sensor).

The scanner has no Windows-free driver and no UVC video interface — its only USB interface is
vendor-specific class `0xFF`, so macOS never lists it as a camera and AVFoundation cannot see it at
all. Negscan drives it directly over libusb instead: it powers up the bridge, configures the sensor
over the bridge's SCCB bus using the register tables from the Windows driver, reads the
isochronous image stream, and develops the raw Bayer frames into positives.

Output is **2592 × 1680** (4.4 MP), the scanner's full frame.

## Setup

```
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

libusb must be present: `brew install libusb`. No kernel extension, no driver install, and no
camera permission — macOS does not treat this device as a camera, so nothing in
Privacy & Security applies to it.

## Run

```
./.venv/bin/python negscan_app.py
```

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
- `scanner_app/icon.py`, `assets/` — the app icon. `assets/icon.svg` is the source; running
  `./.venv/bin/python scripts/make_icon.py` regenerates `icon.png` and the `Negscan.icns` that a
  `.app` bundle would use. Qt renders the SVG at whatever size it needs, so the window and Dock
  stay crisp.

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

72 tests covering the image pipeline, saturation, rotation, the Bayer decoders, the register tables, the
`libusb` structure layout, the icon assets, the auto-exposure search against a modelled sensor,
and the window's capture locking and sidebar layout (driven offscreen, with real key events).
They need no hardware; three skip unless the vendor `.set` files are present, which they are not
in this repository. The scanner itself was exercised separately: repeated scans, reconnects, and
recovery after killing a process mid-stream.
