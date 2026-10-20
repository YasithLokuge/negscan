"""Window behaviour: the capture lock, and the sidebar keeping its controls usable.

Disabling the button is not enough on its own: a QShortcut fires whatever the button's state, so
before this was gated on `_capturing`, holding Space through a scan queued more scans behind it.

These run the real window offscreen. They need no scanner and no display.
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # must precede any Qt import
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts.warning=false")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from PySide6.QtCore import Qt, QEventLoop, QTimer
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QScrollArea
    from scanner_app.main_window import CAPTURE_BUSY_LABEL, CAPTURE_LABEL, MainWindow
    QT_ERROR = None
except Exception as e:                                   # pragma: no cover - environment problem
    QT_ERROR = str(e)


@unittest.skipIf(QT_ERROR, f"Qt is unavailable: {QT_ERROR}")
class CaptureLockTests(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        # Take the real worker out of the loop: with no scanner attached it would reject the
        # request and release the lock, which is right but is not what these tests are about.
        self.window.request_capture.disconnect(self.window.worker.capture)
        self.requests = []
        self.window.request_capture.connect(self.requests.append)
        self.window.show()
        self.window._on_connected(True)                  # pretend, without touching USB

    def tearDown(self):
        self.window.close()
        self.pump()

    def pump(self, ms=40):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def start_capture(self):
        self.window.capture_button.click()
        self.pump()

    def test_idle_state(self):
        self.assertTrue(self.window.capture_button.isEnabled())
        self.assertEqual(self.window.capture_button.text(), CAPTURE_LABEL)

    def test_capture_disables_the_button_and_says_so(self):
        self.start_capture()
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.window.capture_button.isEnabled())
        self.assertEqual(self.window.capture_button.text(), CAPTURE_BUSY_LABEL)

    def test_space_is_ignored_while_a_scan_is_in_flight(self):
        self.start_capture()
        for _ in range(5):
            QTest.keyClick(self.window, Qt.Key_Space)
        self.pump()
        self.assertEqual(len(self.requests), 1, "Space queued extra scans")

    def test_clicking_again_is_ignored_while_a_scan_is_in_flight(self):
        self.start_capture()
        self.window.capture_button.click()
        self.pump()
        self.assertEqual(len(self.requests), 1)

    def test_saving_releases_both_routes(self):
        self.start_capture()
        self.window._on_saved("/tmp/scan_test.png")
        self.pump()
        self.assertTrue(self.window.capture_button.isEnabled())
        self.assertEqual(self.window.capture_button.text(), CAPTURE_LABEL)
        QTest.keyClick(self.window, Qt.Key_Space)
        self.pump()
        self.assertEqual(len(self.requests), 2)

    def test_failure_releases_the_lock(self):
        self.start_capture()
        self.window._on_failed("something went wrong")
        self.pump()
        self.assertFalse(self.window._capturing)
        self.assertTrue(self.window.capture_button.isEnabled())

    def test_disconnecting_mid_scan_does_not_wedge_it(self):
        self.start_capture()
        self.assertTrue(self.window._capturing)
        self.window._on_connected(False)
        self.pump()
        self.assertFalse(self.window._capturing)
        self.assertFalse(self.window.capture_button.isEnabled(),
                         "capture must stay off while disconnected")
        self.window._on_connected(True)
        self.pump()
        self.assertTrue(self.window.capture_button.isEnabled())

    def test_capture_does_nothing_when_disconnected(self):
        self.window._on_connected(False)
        self.window.capture()
        self.pump()
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(QT_ERROR, f"Qt is unavailable: {QT_ERROR}")
class SidebarLayoutTests(unittest.TestCase):
    """The sidebar wants more height than the window has, so it must scroll, not squeeze.

    Qt makes a too-tall layout fit by shrinking widgets below their size hint, and buttons give way
    first: the Capture button collapsed to 16 px with its label clipped. Adding another control
    should not be able to do that again.
    """

    MIN_WIDTH, MIN_HEIGHT = 1040, 680

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def windows(self):
        """The window at its minimum size and at a couple of larger ones."""
        for height in (self.MIN_HEIGHT, 800, 1100):
            window = MainWindow()
            window.resize(self.MIN_WIDTH, height)
            window.show()
            self.app.processEvents()
            try:
                yield height, window
            finally:
                window.close()

    def test_capture_button_keeps_its_height_at_every_window_size(self):
        for height, window in self.windows():
            with self.subTest(window_height=height):
                button = window.capture_button
                needed = QFontMetrics(button.font()).height() + 12
                self.assertGreaterEqual(button.height(), needed,
                                        f"capture button is {button.height()} px tall, "
                                        f"text needs {needed}")

    def test_capture_label_fits_the_button_width(self):
        for height, window in self.windows():
            with self.subTest(window_height=height):
                button = window.capture_button
                metrics = QFontMetrics(button.font())
                for label in (CAPTURE_LABEL, CAPTURE_BUSY_LABEL):
                    self.assertLess(metrics.horizontalAdvance(label) + 24, button.width(),
                                    f"{label!r} does not fit {button.width()} px")

    def test_secondary_button_is_not_squashed(self):
        for height, window in self.windows():
            with self.subTest(window_height=height):
                self.assertGreaterEqual(window.open_button.height(), 28)

    def test_controls_are_in_a_scroll_area(self):
        for height, window in self.windows():
            with self.subTest(window_height=height):
                self.assertTrue(window.findChildren(QScrollArea),
                                "without a scroll area the controls squeeze instead")
                break

    def test_actions_sit_outside_the_scroll_area(self):
        """Capture must stay put and stay visible, not scroll away with the controls."""
        for height, window in self.windows():
            scroll = window.findChildren(QScrollArea)[0]
            self.assertFalse(scroll.isAncestorOf(window.capture_button))
            self.assertFalse(scroll.isAncestorOf(window.open_button))
            break
