"""Global dark theme, QSS, Windows icon glyphs and dark-titlebar integration.

The desktop app uses the native window frame with a dark DWM caption (no fake window buttons) and a
single QSS stylesheet. One rule is load-bearing: ``QLabel { background: transparent; }`` -- without it
every label paints the window background as a full-width dark bar inside cards.

Public helpers used by the UI layer:
  * ``apply_dark_theme(app)``      -- install the Fusion style, base font, stylesheet and palette.
  * ``pill_qss(variant)``          -- inline stylesheet for a glowing status pill.
  * ``icon_char(name)``            -- Segoe MDL2 glyph for a named icon (with text fallbacks).
  * ``icon_css(size, color)``      -- inline stylesheet for an icon QLabel.
  * ``apply_dark_titlebar(window)``-- Win32 DWM dark caption; safe no-op offscreen/non-Windows.
"""

from __future__ import annotations

import contextlib
import os
import sys
from typing import Any

# --- Semantic palette tokens (DOC-05 section 7) -----------------------------------------------------------
BG_WINDOW = "#0a0c10"
BG_SIDEBAR = "#0c0e14"
BG_CARD = "#12151e"
BG_CARD_INNER = "#0d0f16"
BG_TERMINAL = "#08090d"
BORDER_CARD = "#1c2130"
BORDER_CARD_SUBTLE = "#161a26"
BORDER_FOCUS = "#38bdf8"
TEXT_PRIMARY = "#f3f4f6"
TEXT_SECONDARY = "#8892b0"
TEXT_MUTED = "#64748b"
ACCENT_BLUE = "#38bdf8"
GREEN_FG = "#10b981"
GREEN_BORDER = "#10b981"
YELLOW_FG = "#f59e0b"
RED_FG = "#ef4444"
FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Cascadia Mono"
ICON_FONT_FAMILY = "Segoe MDL2 Assets"

# --- Named icon glyphs (Segoe MDL2 Assets codepoints) ------------------------------------------------------
ICONS: dict[str, str] = {
    "shield": "\uea18",
    "home": "\ue80f",
    "history": "\ue81c",
    "target": "\ue81d",
    "models": "\ue964",
    "settings": "\ue713",
    "terminal": "\ue756",
    "folder": "\ue838",
    "files": "\ue8b7",
    "activity": "\ue8fd",
    "warning": "\ue7ba",
    "info": "\ue946",
    "flag": "\ue7c1",
    "bug": "\uebe8",
    "play": "\ue768",
    "pause": "\ue769",
    "stop": "\ue71a",
    "refresh": "\ue72c",
    "add": "\ue710",
    "search": "\ue721",
    "check": "\ue73e",
    "check_circle": "\ue930",
    "error": "\ue711",
    "clock": "\ue823",
    "copy": "\ue8c8",
    "send": "\ue724",
    "expand": "\ue740",
    "document": "\ue8a5",
    "trash": "\ue74d",
    "chip": "\ue964",
    "eye": "\ue8a0",
}

# Fallbacks for environments without the Windows icon font (e.g. a non-Windows dev box).
_ICON_FALLBACKS: dict[str, str] = {
    "shield": "\u25c6",
    "home": "\u2302",
    "history": "\u25f4",
    "target": "\u25ce",
    "models": "\u25a4",
    "settings": "\u2699",
    "terminal": ">_",
    "folder": "\u25a4",
    "files": "\u2261",
    "activity": "\u2261",
    "warning": "\u26a0",
    "info": "\u24d8",
    "flag": "\u2691",
    "bug": "\u2739",
    "play": "\u25b6",
    "pause": "\u2016",
    "stop": "\u25a0",
    "refresh": "\u27f3",
    "add": "+",
    "search": "\u2315",
    "check": "\u2713",
    "check_circle": "\u2714",
    "error": "\u2715",
    "clock": "\u25f4",
    "copy": "\u29c9",
    "send": "\u27a4",
    "expand": "\u2922",
    "document": "\u25a4",
    "trash": "\u2715",
    "chip": "\u25a3",
    "eye": "\u25c9",
}

_icon_family_cache: str | None = None


def icon_font_family() -> str:
    """Return the installed Segoe icon font family, or an empty string when unavailable."""
    global _icon_family_cache
    if _icon_family_cache is not None:
        return _icon_family_cache
    families: list[str] = []
    try:
        from PySide6.QtGui import QFontDatabase

        families = QFontDatabase.families()
    except Exception:
        families = []
    for candidate in ("Segoe MDL2 Assets", "Segoe Fluent Icons", "Segoe UI Symbol"):
        if candidate in families:
            _icon_family_cache = candidate
            return candidate
    _icon_family_cache = ""
    return ""


def icon_char(name: str) -> str:
    """Glyph for *name*; falls back to a plain unicode symbol when the icon font is unavailable."""
    glyph = ICONS.get(name, ICONS["info"])
    if icon_font_family():
        return glyph
    return _ICON_FALLBACKS.get(name, glyph)


def icon_css(size: int, color: str = TEXT_SECONDARY) -> str:
    """Inline stylesheet for a QLabel that renders a Segoe MDL2 glyph."""
    family = icon_font_family()
    font_css = f"font-family: '{family}';" if family else ""
    return f"{font_css} font-size: {size}px; color: {color}; background: transparent; border: none;"


# --- Stylesheet -------------------------------------------------------------------------------------------
QSS = """
* {
    outline: none;
}

QWidget {
    background-color: transparent;
    color: #f3f4f6;
    font-family: "Segoe UI";
    font-size: 13px;
}

/* Labels are transparent by contract: a label must never paint a window-background bar. */
QLabel {
    background: transparent;
}

QMainWindow, QDialog, QMessageBox, QWidget#appRoot, QWidget#pageRoot {
    background-color: #0a0c10;
}

QToolTip {
    background-color: #12151e;
    color: #e2e8f0;
    border: 1px solid #38bdf8;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 11px;
}

/* --- Sidebar dock -------------------------------------------------------------------------------------- */
QFrame#sidebarDock {
    background-color: #0c0e14;
    border: none;
    border-right: 1px solid #161a26;
}
QFrame#sidebarBrand {
    background-color: #0f1729;
    border: 1px solid #1d4ed8;
    border-radius: 12px;
}
QLabel#sidebarVersion {
    color: #3f4a63;
    font-size: 10px;
    font-weight: 600;
}
QPushButton#dockerBadge {
    background-color: #101420;
    border: 1px solid #1e2434;
    border-radius: 9px;
    color: #8892b0;
    font-size: 9px;
    font-weight: 600;
    padding: 5px 2px;
}
QPushButton#dockerBadge:hover {
    background-color: #161b2a;
    border-color: #38bdf8;
}

/* --- Top bar ------------------------------------------------------------------------------------------- */
QFrame#topBar {
    background-color: transparent;
    border: none;
    border-bottom: 1px solid #161a26;
}
QLabel#brandTitle {
    color: #ffffff;
    font-size: 20px;
    font-weight: 800;
    letter-spacing: -0.4px;
}
QLabel#brandSubtitle {
    color: #8892b0;
    font-size: 11px;
    font-weight: 400;
}
QFrame#targetCapsule {
    background-color: #11141d;
    border: 1px solid #1e2434;
    border-radius: 17px;
}
QLabel#capsuleText {
    color: #cbd5e1;
    font-size: 12px;
}

/* --- Cards --------------------------------------------------------------------------------------------- */
QFrame#investigationCard, QFrame#terminalCard, QFrame#sessionsFilesCard,
QFrame#bottomControlCard, QFrame#bottomBar, QFrame#card {
    background-color: #12151e;
    border: 1px solid #1c2130;
    border-radius: 12px;
}
QLabel[role="cardHeader"] {
    color: #e2e8f0;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1.0px;
}
QLabel[role="sectionHeader"] {
    color: #8892b0;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.2px;
}
QLabel[role="muted"] {
    color: #8892b0;
    font-size: 11px;
}
QLabel[role="hint"] {
    color: #64748b;
    font-size: 10px;
}
QLabel[role="metric"] {
    color: #e2e8f0;
    font-size: 11px;
    font-weight: 600;
}
QLabel[role="footer"] {
    color: #64748b;
    font-size: 11px;
}
QLabel[role="error"] {
    color: #f87171;
    font-size: 11px;
    font-weight: 600;
}

/* --- Buttons ------------------------------------------------------------------------------------------- */
QPushButton {
    border-radius: 8px;
}
QPushButton#btnPrimary {
    background-color: #10b981;
    border: 1px solid #34d399;
    border-radius: 9px;
    color: #04120c;
    font-size: 13px;
    font-weight: 700;
    padding: 0 18px;
    min-height: 36px;
}
QPushButton#btnPrimary:hover {
    background-color: #34d399;
    border-color: #6ee7b7;
}
QPushButton#btnPrimary:pressed {
    background-color: #059669;
}
QPushButton#btnPrimary:disabled {
    background-color: #1a1f2c;
    border: 1px solid #252c3d;
    color: #566179;
}
QPushButton#btnNewInvestigation {
    background-color: rgba(56, 189, 248, 0.10);
    border: 1px solid rgba(56, 189, 248, 0.45);
    border-radius: 8px;
    color: #7dd3fc;
    font-size: 12px;
    font-weight: 600;
    padding: 0 14px;
    min-height: 32px;
}
QPushButton#btnNewInvestigation:hover {
    background-color: rgba(56, 189, 248, 0.20);
    border-color: #38bdf8;
    color: #e0f2fe;
}
QPushButton#btnSecondary {
    background-color: #1a1e28;
    border: 1px solid #2a3142;
    border-radius: 8px;
    color: #cbd5e1;
    font-size: 12px;
    font-weight: 600;
    padding: 0 12px;
    min-height: 28px;
}
QPushButton#btnSecondary:hover {
    background-color: #222838;
    border-color: #38bdf8;
    color: #f3f4f6;
}
QPushButton#btnSecondary:disabled {
    color: #566179;
    border-color: #1e2434;
}
QPushButton#btnPause {
    background-color: #1a1e28;
    border: 1px solid #2a3142;
    border-radius: 9px;
    color: #f1f5f9;
    font-size: 12px;
    font-weight: 600;
    padding: 0 18px;
    min-height: 36px;
}
QPushButton#btnPause:hover {
    background-color: #222838;
    border-color: #f59e0b;
    color: #f59e0b;
}
QPushButton#btnPause:disabled {
    background-color: #141824;
    border-color: #1e2434;
    color: #4b5568;
}
QPushButton#btnStop {
    background-color: rgba(239, 68, 68, 0.10);
    border: 1px solid rgba(239, 68, 68, 0.65);
    border-radius: 9px;
    color: #ef4444;
    font-size: 12px;
    font-weight: 600;
    padding: 0 20px;
    min-height: 36px;
}
QPushButton#btnStop:hover {
    background-color: rgba(239, 68, 68, 0.22);
    border-color: #ef4444;
}
QPushButton#btnStop:disabled {
    background-color: #141824;
    border-color: #2a2f3d;
    color: #4b5568;
}
QPushButton#btnFindingsPotential {
    background-color: rgba(245, 158, 11, 0.10);
    border: 1px solid rgba(245, 158, 11, 0.38);
    border-radius: 9px;
    color: #f59e0b;
    font-size: 12px;
    font-weight: 600;
    padding: 0 12px;
    min-height: 36px;
    text-align: left;
}
QPushButton#btnFindingsPotential:hover {
    background-color: rgba(245, 158, 11, 0.20);
    border-color: #f59e0b;
}
QPushButton#btnFindingsConfirmed {
    background-color: rgba(239, 68, 68, 0.10);
    border: 1px solid rgba(239, 68, 68, 0.38);
    border-radius: 9px;
    color: #ef4444;
    font-size: 12px;
    font-weight: 600;
    padding: 0 12px;
    min-height: 36px;
    text-align: left;
}
QPushButton#btnFindingsConfirmed:hover {
    background-color: rgba(239, 68, 68, 0.20);
    border-color: #ef4444;
}
QPushButton[engineToggle="true"] {
    background-color: #141824;
    border: 1px solid #252c3d;
    border-radius: 8px;
    color: #8892b0;
    font-size: 12px;
    font-weight: 600;
    padding: 0 12px;
    min-height: 30px;
}
QPushButton[engineToggle="true"]:hover {
    border-color: #38bdf8;
    color: #e2e8f0;
}
QPushButton[engineToggle="true"]:checked {
    background-color: rgba(56, 189, 248, 0.14);
    border: 1px solid #38bdf8;
    color: #7dd3fc;
}
QPushButton[engineToggle="true"]:disabled {
    color: #4b5568;
    border-color: #1e2434;
    background-color: #12151e;
}
QPushButton#iconButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    color: #38bdf8;
    font-size: 16px;
    padding: 2px 6px;
}
QPushButton#iconButton:hover {
    background-color: #1c2130;
    color: #7dd3fc;
}

/* --- Inputs -------------------------------------------------------------------------------------------- */
QLineEdit, QComboBox, QAbstractSpinBox {
    background-color: #0d0f16;
    border: 1px solid #1e2434;
    border-radius: 8px;
    color: #f3f4f6;
    font-size: 12px;
    padding: 6px 10px;
    selection-background-color: #1d4ed8;
    selection-color: #ffffff;
    min-height: 20px;
}
QLineEdit:hover, QComboBox:hover {
    border-color: #2a3142;
}
QLineEdit:focus, QComboBox:focus {
    border: 1px solid #38bdf8;
}
QLineEdit:disabled {
    color: #4b5568;
    background-color: #12151e;
}
QLineEdit#steeringInput {
    background-color: transparent;
    border: none;
    font-size: 13px;
    padding: 4px 2px;
}
QLineEdit#steeringInput:focus {
    border: none;
}
QFrame#steeringContainer {
    background-color: #0d0f16;
    border: 1px solid #1e2434;
    border-radius: 19px;
}
QFrame#steeringContainer:focus-within {
    border: 1px solid #38bdf8;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox::down-arrow {
    image: none;
    width: 0px;
    height: 0px;
}
QComboBox QAbstractItemView {
    background-color: #12151e;
    border: 1px solid #1c2130;
    color: #f3f4f6;
    selection-background-color: #1a2030;
    selection-color: #ffffff;
    outline: 0;
}

/* --- Readiness / error banners ------------------------------------------------------------------------- */
QFrame#readinessBanner {
    background-color: rgba(245, 158, 11, 0.08);
    border: 1px solid rgba(245, 158, 11, 0.45);
    border-radius: 9px;
}
QFrame#readinessBanner[severity="error"] {
    background-color: rgba(239, 68, 68, 0.08);
    border: 1px solid rgba(239, 68, 68, 0.45);
}
QFrame#readinessBanner[severity="info"] {
    background-color: rgba(56, 189, 248, 0.08);
    border: 1px solid rgba(56, 189, 248, 0.40);
}
QLabel#readinessText {
    color: #fbbf24;
    font-size: 11px;
}
QFrame#readinessBanner[severity="error"] QLabel#readinessText {
    color: #f87171;
}
QFrame#readinessBanner[severity="info"] QLabel#readinessText {
    color: #7dd3fc;
}
QFrame#errorBanner {
    background-color: rgba(239, 68, 68, 0.08);
    border: 1px solid rgba(239, 68, 68, 0.45);
    border-radius: 9px;
}
QLabel#errorBannerText {
    color: #f87171;
    font-size: 11px;
}

/* --- Progress ------------------------------------------------------------------------------------------ */
QProgressBar {
    background-color: #1a1f2c;
    border: none;
    border-radius: 4px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background-color: #10b981;
    border-radius: 4px;
}
QProgressBar#progressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10b981, stop:1 #34d399);
}

/* --- Lists / trees / feeds ----------------------------------------------------------------------------- */
QListWidget, QTreeWidget, QTableWidget {
    background-color: #0d0f16;
    border: 1px solid #1a1e2b;
    border-radius: 9px;
    color: #f3f4f6;
    outline: 0;
    padding: 3px;
}
QListWidget::item {
    border: none;
    border-radius: 5px;
    padding: 2px 4px;
}
QListWidget::item:hover {
    background-color: #141824;
}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {
    background-color: #1a2333;
    color: #ffffff;
}
QTreeWidget::item {
    padding: 3px 2px;
}
QTreeWidget::item:hover {
    background-color: #141824;
}
QHeaderView::section {
    background-color: #12151e;
    color: #8892b0;
    border: none;
    border-bottom: 1px solid #1c2130;
    padding: 6px 8px;
    font-size: 11px;
    font-weight: 600;
}

/* --- Scrollbars ---------------------------------------------------------------------------------------- */
QScrollArea {
    background: transparent;
    border: none;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 9px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #232a3a;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background: #31405a;
}
QScrollBar:horizontal {
    background: transparent;
    height: 9px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #232a3a;
    border-radius: 4px;
    min-width: 24px;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}
QAbstractScrollArea::corner {
    background: transparent;
}

/* --- Misc ---------------------------------------------------------------------------------------------- */
QFrame#footerBar {
    background-color: #0a0c10;
    border: none;
    border-top: 1px solid #141824;
}
QFrame#bottomBar {
    background-color: #12151e;
}
QSplitter::handle {
    background-color: transparent;
    width: 10px;
    height: 10px;
}
QToolButton {
    background: transparent;
    border: none;
    color: #8892b0;
    padding: 4px;
}
QToolButton:hover {
    color: #f3f4f6;
}
QCheckBox {
    color: #cbd5e1;
    font-size: 12px;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid #334155;
    background-color: #0d0f16;
}
QCheckBox::indicator:checked {
    background-color: #38bdf8;
    border-color: #38bdf8;
}
QMessageBox {
    background-color: #12151e;
}
QMessageBox QLabel {
    color: #e2e8f0;
    background: transparent;
}
QMessageBox QPushButton {
    background-color: #1a1e28;
    border: 1px solid #2a3142;
    border-radius: 8px;
    color: #e2e8f0;
    min-width: 84px;
    min-height: 28px;
    padding: 0 12px;
}
QMessageBox QPushButton:hover {
    background-color: #222838;
    border-color: #38bdf8;
}
"""


# --- Public helpers -----------------------------------------------------------------------------------------
def pill_qss(variant: str) -> str:
    """Inline stylesheet for a glowing status pill; ``variant`` is green/yellow/red/blue/gray."""
    styles = {
        "green": ("rgba(16, 185, 129, 0.16)", GREEN_FG, GREEN_BORDER),
        "yellow": ("rgba(245, 158, 11, 0.16)", YELLOW_FG, YELLOW_FG),
        "amber": ("rgba(245, 158, 11, 0.16)", YELLOW_FG, YELLOW_FG),
        "red": ("rgba(239, 68, 68, 0.16)", RED_FG, RED_FG),
        "blue": ("rgba(56, 189, 248, 0.16)", ACCENT_BLUE, ACCENT_BLUE),
        "gray": ("rgba(136, 146, 176, 0.14)", TEXT_SECONDARY, BORDER_CARD),
    }
    bg, fg, border = styles.get(variant, styles["gray"])
    return (
        f"background-color: {bg}; color: {fg}; border: 1px solid {border};"
        " border-radius: 13px; padding: 4px 13px; font-weight: 700; font-size: 11px;"
    )


def apply_dark_theme(app: Any) -> None:
    """Install the Fusion style, base font and the full dark stylesheet on *app*."""
    with contextlib.suppress(Exception):
        app.setStyle("Fusion")
    try:
        from PySide6.QtGui import QFont

        font = QFont(FONT_FAMILY)
        font.setPointSizeF(9.0)
        app.setFont(font)
    except Exception:
        pass
    app.setStyleSheet(QSS)


def _colorref(hex_color: str) -> int:
    """Convert ``#RRGGBB`` to a Win32 COLORREF (0x00BBGGRR)."""
    value = hex_color.lstrip("#")
    red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return (blue << 16) | (green << 8) | red


def apply_dark_titlebar(window: Any) -> bool:
    """Paint the native Windows title bar dark. Returns True when the DWM call succeeded.

    Never raises: offscreen/minimal Qt platforms and non-Windows hosts simply return False.
    """
    if sys.platform != "win32":
        return False
    platform = os.environ.get("QT_QPA_PLATFORM", "").lower()
    if platform.startswith(("offscreen", "minimal", "vnc")):
        return False
    try:
        import ctypes

        hwnd = int(window.winId())
        if not hwnd:
            return False
        dwm = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        enabled = ctypes.c_int(1)
        applied = False
        # DWMWA_USE_IMMERSIVE_DARK_MODE is 20 on current Windows 10/11, 19 on older builds.
        for attribute in (20, 19):
            result = dwm.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)
            )
            if result == 0:
                applied = True
                break
        # Windows 11 caption/border/text colours (harmless no-ops on Windows 10).
        for attribute, value in (
            (35, _colorref(BG_WINDOW)),
            (34, _colorref(BORDER_CARD_SUBTLE)),
            (36, _colorref(TEXT_PRIMARY)),
        ):
            color = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), attribute, ctypes.byref(color), ctypes.sizeof(color)
            )
        return applied
    except Exception:
        return False
