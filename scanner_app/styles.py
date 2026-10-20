"""Dark theme. Colours are defined once here so the look can be changed in a single place."""

BG = "#15171c"
SIDEBAR = "#1c1f26"
PANEL = "#23272f"
BORDER = "#2e333d"
TEXT = "#e8eaf0"
MUTED = "#8a909c"
ACCENT = "#e0a24a"       # warm amber, a nod to film base
ACCENT_HOVER = "#eab566"

STYLESHEET = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: -apple-system, "SF Pro Text", "Helvetica Neue", sans-serif;
    font-size: 13px;
}}
QFrame#sidebar {{
    background: {SIDEBAR};
    border-right: 1px solid {BORDER};
}}
QFrame#previewWrap {{ background: {BG}; }}
QLabel#appTitle {{ font-size: 20px; font-weight: 600; padding-bottom: 4px; }}
QLabel#sectionTitle {{
    color: {MUTED}; font-size: 11px; font-weight: 600;
    letter-spacing: 1px; padding-top: 10px;
}}
QLabel#valueLabel {{ color: {MUTED}; }}
QLabel#statChip {{ color: {MUTED}; padding: 0 10px; }}
QLabel#preview {{
    background: #0b0c0f;
    border: 1px solid {BORDER};
    border-radius: 10px;
    color: {MUTED};
}}
QComboBox, QLineEdit, QSpinBox {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    min-height: 18px;
}}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{
    background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {ACCENT};
    selection-color: #111;
}}
QCheckBox {{ spacing: 8px; padding: 4px 0; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {BORDER}; background: {PANEL};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: {TEXT};
}}
QPushButton {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 14px;
}}
QPushButton:hover {{ border-color: {MUTED}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#primary {{
    background: {ACCENT}; color: #14110a; border: none;
    font-size: 15px; font-weight: 600; padding: 12px;
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#ghost {{ background: transparent; color: {MUTED}; }}
QPushButton#ghost:hover {{ color: {TEXT}; }}
QStatusBar {{ background: {SIDEBAR}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; }}
"""
