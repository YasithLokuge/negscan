"""Main window: live preview, film settings and capture."""
import datetime as dt
import os
import subprocess
from collections import namedtuple

import cv2
from PySide6.QtCore import QSettings, QSize, Qt, QThread, Signal
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from . import ov5620
from . import processing as proc
from . import usb_scanner
from .capture import CaptureWorker
from .icon import app_icon
from .styles import STYLESHEET

DEFAULT_FOLDER = os.path.join(os.path.expanduser("~"), "Pictures", "Negscan")
Slider = namedtuple("Slider", "slider label")


def section(title):
    label = QLabel(title.upper())
    label.setObjectName("sectionTitle")
    return label


def slider_row(name, slider, value_label):
    row = QHBoxLayout()
    row.addWidget(QLabel(name))
    row.addStretch(1)
    row.addWidget(value_label)
    box = QVBoxLayout()
    box.setSpacing(2)
    box.addLayout(row)
    box.addWidget(slider)
    return box


class MainWindow(QMainWindow):
    # Queued into the worker thread; calling its slots directly would run them on the GUI thread.
    request_connect = Signal()
    request_disconnect = Signal()
    request_preview = Signal(bool)
    request_capture = Signal(int)
    request_shutdown = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Negscan")
        self.setWindowIcon(app_icon())   # the window keeps its icon under any entry point
        self.setMinimumSize(QSize(1040, 680))
        self.setStyleSheet(STYLESHEET)

        self.settings = QSettings("Negscan", "Negscan")
        self.latest_preview = None      # last BGR frame from the scanner
        self._connected = False
        self._last_saved = None
        self._loading = False           # True while restoring saved settings

        self._build_ui()
        self._start_worker()
        self._load_settings()
        self.refresh_device()

    # ----- layout -------------------------------------------------------

    def _build_ui(self):
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())
        layout.addWidget(self._build_preview(), 1)
        self.setCentralWidget(root)

        status = self.statusBar()
        self.res_label = QLabel("–")
        self.msg_label = QLabel("")
        self.res_label.setObjectName("statChip")
        status.addPermanentWidget(self.res_label)
        status.addWidget(self.msg_label, 1)

        QShortcut(QKeySequence("Space"), self).activated.connect(self.capture)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.refresh_device)

    def _build_sidebar(self):
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(300)
        v = QVBoxLayout(side)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)

        title = QLabel("Negscan")
        title.setObjectName("appTitle")
        v.addWidget(title)

        v.addWidget(section("Scanner"))
        self.device_label = QLabel("–")
        self.device_label.setWordWrap(True)
        v.addWidget(self.device_label)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.toggle_connection)
        v.addWidget(self.connect_button)

        v.addWidget(section("Film"))
        self.film_combo = QComboBox()
        self.film_combo.addItems(proc.FILM_TYPES)
        self.film_combo.currentIndexChanged.connect(self._on_settings_changed)
        v.addWidget(self.film_combo)
        self.auto_check = QCheckBox("Auto levels (recommended)")
        self.auto_check.stateChanged.connect(self._on_settings_changed)
        v.addWidget(self.auto_check)
        self.neutral_check = QCheckBox("Neutralise colour cast")
        self.neutral_check.stateChanged.connect(self._on_settings_changed)
        v.addWidget(self.neutral_check)

        v.addWidget(section("Tone"))
        self.exposure = self._make_slider(-200, 200, self._fmt_ev)
        self.contrast = self._make_slider(-100, 100, lambda val: f"{val / 100:+.2f}")
        self.gamma = self._make_slider(40, 250, lambda val: f"{val / 100:.2f}")
        self.saturation = self._make_slider(0, 200, self._fmt_saturation)
        v.addLayout(slider_row("Exposure", self.exposure.slider, self.exposure.label))
        v.addLayout(slider_row("Contrast", self.contrast.slider, self.contrast.label))
        v.addLayout(slider_row("Gamma", self.gamma.slider, self.gamma.label))
        v.addLayout(slider_row("Saturation", self.saturation.slider, self.saturation.label))
        reset = QPushButton("Reset tone")
        reset.setObjectName("ghost")
        reset.clicked.connect(self.reset_tone)
        v.addWidget(reset)

        v.addWidget(section("Capture"))
        self.frames_spin = QSpinBox()
        self.frames_spin.setRange(1, 20)
        self.frames_spin.setSuffix(" frames averaged")
        v.addWidget(self.frames_spin)
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        browse = QPushButton("…")
        browse.setObjectName("ghost")
        browse.setFixedWidth(36)
        browse.clicked.connect(self.choose_folder)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        v.addLayout(folder_row)
        self.before_check = QCheckBox("Show original (before processing)")
        self.before_check.stateChanged.connect(self._refresh_preview)
        v.addWidget(self.before_check)

        v.addStretch(1)
        self.capture_button = QPushButton("Capture   Space")
        self.capture_button.setObjectName("primary")
        self.capture_button.setEnabled(False)
        self.capture_button.clicked.connect(self.capture)
        v.addWidget(self.capture_button)
        self.open_button = QPushButton("Show last scan in Finder")
        self.open_button.setObjectName("ghost")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.reveal_last)
        v.addWidget(self.open_button)
        return side

    def _make_slider(self, lo, hi, fmt):
        slider = QSlider(Qt.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(0 if lo < 0 else lo)
        label = QLabel()
        label.setObjectName("valueLabel")
        label.setMinimumWidth(48)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slider.valueChanged.connect(lambda val: (label.setText(fmt(val)), self._on_settings_changed()))
        label.setText(fmt(slider.value()))
        return Slider(slider, label)

    @staticmethod
    def _fmt_ev(value):
        return f"{value / 100:+.1f} EV"

    @staticmethod
    def _fmt_saturation(value):
        if value == 0:
            return "grey"
        return f"{value / 100:.2f}"

    def _build_preview(self):
        wrap = QFrame()
        wrap.setObjectName("previewWrap")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(24, 24, 24, 24)
        self.preview = QLabel("Put a negative in the scanner, then press Connect.")
        self.preview.setObjectName("preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(640, 420)
        v.addWidget(self.preview)
        return wrap

    # ----- worker thread ------------------------------------------------

    def _start_worker(self):
        self.thread = QThread(self)
        self.worker = CaptureWorker()
        self.worker.moveToThread(self.thread)
        self.worker.preview_ready.connect(self._on_preview)
        self.worker.capture_ready.connect(self._on_capture)
        self.worker.status.connect(self.msg_label.setText)
        self.worker.failed.connect(self._on_failed)
        self.worker.connected.connect(self._on_connected)
        self.request_connect.connect(self.worker.connect_scanner)
        self.request_disconnect.connect(self.worker.disconnect_scanner)
        self.request_preview.connect(self.worker.set_previewing)
        self.request_capture.connect(self.worker.capture)
        self.request_shutdown.connect(self.worker.shutdown)
        # The worker owns a QTimer created on its own thread, and Qt will not let the GUI thread
        # destroy it. Deleting the worker from inside its thread as it finishes keeps that right.
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()

    # ----- settings -----------------------------------------------------

    def _load_settings(self):
        s = self.settings
        self._loading = True
        try:
            self.folder_edit.setText(s.value("folder", DEFAULT_FOLDER))
            self.film_combo.setCurrentText(s.value("film", proc.FILM_COLOR_NEGATIVE))
            self.auto_check.setChecked(s.value("auto_levels", True, type=bool))
            self.neutral_check.setChecked(s.value("neutralise", True, type=bool))
            self.frames_spin.setValue(s.value("frames", 4, type=int))
            self.exposure.slider.setValue(s.value("exposure", 0, type=int))
            self.contrast.slider.setValue(s.value("contrast", 0, type=int))
            self.gamma.slider.setValue(s.value("gamma", 100, type=int))
            self.saturation.slider.setValue(s.value("saturation", 100, type=int))
        finally:
            self._loading = False

    def _save_settings(self):
        s = self.settings
        s.setValue("folder", self.folder_edit.text())
        s.setValue("film", self.film_combo.currentText())
        s.setValue("auto_levels", self.auto_check.isChecked())
        s.setValue("neutralise", self.neutral_check.isChecked())
        s.setValue("frames", self.frames_spin.value())
        s.setValue("exposure", self.exposure.slider.value())
        s.setValue("contrast", self.contrast.slider.value())
        s.setValue("gamma", self.gamma.slider.value())
        s.setValue("saturation", self.saturation.slider.value())

    def current_settings(self):
        return proc.Settings(
            film=self.film_combo.currentText(),
            auto_levels=self.auto_check.isChecked(),
            neutralise=self.neutral_check.isChecked(),
            exposure_ev=self.exposure.slider.value() / 100.0,
            contrast=self.contrast.slider.value() / 100.0,
            gamma=self.gamma.slider.value() / 100.0,
            saturation=self.saturation.slider.value() / 100.0,
        )

    def reset_tone(self):
        self.exposure.slider.setValue(0)
        self.contrast.slider.setValue(0)
        self.gamma.slider.setValue(100)
        self.saturation.slider.setValue(100)

    def _on_settings_changed(self, *_):
        if self._loading:
            return
        self._save_settings()
        self._refresh_preview()

    # ----- device and preview -------------------------------------------

    def refresh_device(self):
        if usb_scanner.find_device() is None:
            self.device_label.setText("No scanner found on USB (05a9:1550).")
            self.connect_button.setEnabled(False)
            self.msg_label.setText("Plug the scanner in, then press Ctrl+R.")
        else:
            self.device_label.setText("OVT film scanner (OV550 / OV5620) on USB 05a9:1550.")
            self.connect_button.setEnabled(True)
            self.res_label.setText(f"{ov5620.WIDTH}×{ov5620.HEIGHT}")
            if not self._connected:
                self.msg_label.setText("Scanner found. Press Connect.")

    def toggle_connection(self):
        if self._connected:
            self.request_preview.emit(False)
            self.request_disconnect.emit()
        else:
            self.connect_button.setEnabled(False)
            self.request_connect.emit()

    def _on_connected(self, connected):
        self._connected = connected
        self.connect_button.setEnabled(True)
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.capture_button.setEnabled(connected)
        if connected:
            self.request_preview.emit(True)
        else:
            self.latest_preview = None
            self.preview.setPixmap(QPixmap())
            self.preview.setText("Disconnected.")

    def _on_failed(self, message):
        self.msg_label.setText(message)
        self.connect_button.setEnabled(usb_scanner.find_device() is not None)
        # A capture that fails leaves its button disabled otherwise, with no way to retry.
        self.capture_button.setEnabled(self._connected)

    def _on_preview(self, frame):
        self.latest_preview = frame
        self._refresh_preview()

    def _refresh_preview(self, *_):
        if self.latest_preview is None:
            return
        # Downscale to the widget before processing: the pipeline then costs a fraction as much
        # and the result on screen is identical.
        size = self.preview.size()
        small = proc.fit_to(self.latest_preview, max(size.width(), 1), max(size.height(), 1))
        out = small if self.before_check.isChecked() else proc.process(small, self.current_settings())
        self._show(out)

    def _show(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        image = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()
        self.preview.setPixmap(QPixmap.fromImage(image))

    # ----- capture ------------------------------------------------------

    def capture(self):
        if not self._connected:
            self.msg_label.setText("Connect the scanner first.")
            return
        self.capture_button.setEnabled(False)
        self.request_capture.emit(self.frames_spin.value())

    def _on_capture(self, bgr):
        self.capture_button.setEnabled(True)
        result = proc.process(bgr, self.current_settings())
        folder = self.folder_edit.text() or DEFAULT_FOLDER
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self.msg_label.setText(f"Could not create {folder}: {e}")
            return
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(folder, f"scan_{stamp}.png")
        n = 1
        while os.path.exists(path):   # two captures in the same second must not overwrite
            path = os.path.join(folder, f"scan_{stamp}_{n}.png")
            n += 1
        if cv2.imwrite(path, result, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
            self._last_saved = path
            self.open_button.setEnabled(True)
            self.msg_label.setText(f"Saved {os.path.basename(path)}")
        else:
            self.msg_label.setText(f"Could not write to {folder}")

    def reveal_last(self):
        if self._last_saved:
            subprocess.run(["open", "-R", self._last_saved], check=False)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose scan folder", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)
            self._save_settings()

    def closeEvent(self, event):
        self.request_preview.emit(False)
        self.request_shutdown.emit()
        self.thread.quit()
        self.thread.wait(4000)
        self.worker = None      # deleted on the worker thread by the finished -> deleteLater link
        self._save_settings()
        super().closeEvent(event)
