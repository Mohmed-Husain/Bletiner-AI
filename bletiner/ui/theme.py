"""Global dark theme and QSS (DOC-05 section 7). Pure string constants + an apply helper; no Qt import at
module load, so this stays importable without PySide6."""

from __future__ import annotations

from typing import Any

# Semantic palette tokens (DOC-05 section 7.1).
BG_WINDOW = "#12141a"
BG_CARD = "#1a1d24"
BG_CARD_HOVER = "#222630"
BG_TERMINAL = "#0b0d11"
BORDER_CARD = "#2a2e39"
BORDER_FOCUS = "#38bdf8"
TEXT_PRIMARY = "#f3f4f6"
TEXT_SECONDARY = "#9ca3af"
ACCENT_BLUE = "#38bdf8"
GREEN_FG = "#34d399"
GREEN_BORDER = "#10b981"
YELLOW_FG = "#f59e0b"
RED_FG = "#ef4444"

QSS = """
QWidget { background-color: #12141a; color: #f3f4f6;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; font-size: 13px; }

/* Top bar */
QFrame#topBar { background-color: #1a1d24; border-bottom: 1px solid #2a2e39; }
QLabel#brandTitle { color: #f3f4f6; font-size: 14px; font-weight: 700; }
QLabel#brandShield { font-size: 16px; }
QPushButton.winBtn { background: transparent; border: none; color: #9ca3af;
    font-size: 14px; padding: 2px 10px; }
QPushButton.winBtn:hover { color: #f3f4f6; background-color: #222630; border-radius: 6px; }
QPushButton#winClose:hover { color: #ffffff; background-color: #ef4444; }

/* Cards */
QFrame#investigationCard, QFrame#terminalCard, QFrame#sessionsFilesCard, QFrame#bottomControlCard {
    background-color: #1a1d24; border: 1px solid #2a2e39; border-radius: 10px; }
QLabel[role="cardHeader"] { color: #9ca3af; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
QLabel[role="muted"] { color: #9ca3af; }
QLabel#targetLink { color: #38bdf8; }

/* Activity feed / lists / trees */
QListWidget, QTreeWidget { background-color: #12141a; border: 1px solid #2a2e39; border-radius: 8px;
    color: #f3f4f6; outline: 0; }
QListWidget::item { padding: 3px 6px; border-bottom: 1px solid #1a1d24; }
QListWidget::item:selected, QTreeWidget::item:selected { background-color: #222630; color: #f3f4f6; }
QTreeWidget::item { padding: 2px 2px; }
QScrollBar:vertical { background: #12141a; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #2a2e39; border-radius: 5px; min-height: 24px; }
QScrollBar:horizontal { background: #12141a; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: #2a2e39; border-radius: 5px; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QAbstractScrollArea::corner { background: #12141a; }

/* Terminal */
QPlainTextEdit#terminalView { background-color: #0b0d11; color: #e6edf3; border: none;
    border-radius: 8px; font-family: "SF Mono", "Menlo", "Consolas", monospace; font-size: 12px; padding: 10px; }

/* Steering input */
QLineEdit#steeringInput { background-color: #12141a; border: 1px solid #2a2e39; border-radius: 6px;
    color: #f3f4f6; padding: 8px 14px; font-size: 13px; }
QLineEdit#steeringInput:focus { border: 1px solid #38bdf8; background-color: #0b0d11; }

/* Pause / Stop */
QPushButton#btnPause, QPushButton#btnStop { background-color: #222630; border: 1px solid #2a2e39;
    border-radius: 6px; color: #f3f4f6; padding: 7px 16px; font-weight: 600; }
QPushButton#btnPause:hover { background-color: #2a2e39; border-color: #f59e0b; color: #f59e0b; }
QPushButton#btnStop:hover { background-color: #2a2e39; border-color: #ef4444; color: #ef4444; }

/* Footer */
QLabel#footerLeft, QLabel#footerRight { color: #9ca3af; font-size: 11px; }
QSplitter::handle { background-color: #12141a; }
"""


def pill_qss(variant: str) -> str:
    """Inline stylesheet for a status pill (Qt QSS has no reliable `.class` selector, so pills self-style)."""
    styles = {
        "green": ("rgba(16, 185, 129, 0.15)", GREEN_FG, GREEN_BORDER),
        "yellow": ("rgba(245, 158, 11, 0.15)", YELLOW_FG, YELLOW_FG),
        "red": ("rgba(239, 68, 68, 0.15)", RED_FG, RED_FG),
        "gray": ("rgba(156, 163, 175, 0.12)", TEXT_SECONDARY, BORDER_CARD),
    }
    bg, fg, border = styles.get(variant, styles["gray"])
    return (
        f"background-color: {bg}; color: {fg}; border: 1px solid {border};"
        "border-radius: 12px; padding: 3px 11px; font-weight: 600; font-size: 12px;"
    )


def apply_dark_theme(app: Any) -> None:
    app.setStyleSheet(QSS)
