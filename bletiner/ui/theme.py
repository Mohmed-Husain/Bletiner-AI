"""Global dark theme and QSS (DOC-05 section 7).

Matches the reference SOC deck aesthetic:
- Deep obsidian / charcoal palette (#0a0c10 / #12151e / #1c2130)
- Neon / vibrant semantic accents (#38bdf8 blue, #10b981 emerald, #f59e0b amber, #ef4444 red)
- Card border radius, glowing capsules, and crisp typography.
"""

from __future__ import annotations

from typing import Any

# Semantic palette tokens
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

QSS = """
QWidget {
    background-color: #0a0c10;
    color: #f3f4f6;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}

/* Sidebar Dock */
QFrame#sidebarDock {
    background-color: #0c0e14;
    border-right: 1px solid #1c2130;
}
QFrame#sidebarBrand {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a2744, stop:1 #0f172a);
    border: 1px solid #2563eb;
    border-radius: 12px;
}
QPushButton.navBtn {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    color: #8892b0;
    font-size: 10px;
    font-weight: 500;
    padding: 6px 2px;
}
QPushButton.navBtn:hover {
    background-color: #141824;
    color: #f3f4f6;
}
QPushButton.navBtnActive {
    background-color: rgba(37, 99, 235, 0.16);
    border: 1px solid #2563eb;
    border-radius: 10px;
    color: #93c5fd;
    font-size: 10px;
    font-weight: 600;
    padding: 6px 2px;
}
QFrame#dockerBadge {
    background-color: #121622;
    border: 1px solid #1c2438;
    border-radius: 8px;
    padding: 4px;
}

/* Top Bar */
QFrame#topBar {
    background-color: transparent;
    border-bottom: 1px solid #161a26;
}
QLabel#brandTitle {
    color: #ffffff;
    font-size: 18px;
    font-weight: 800;
    letter-spacing: -0.3px;
}
QLabel#brandSubtitle {
    color: #8892b0;
    font-size: 11px;
    font-weight: 400;
}
QFrame#targetCapsule {
    background-color: #12151f;
    border: 1px solid #1c2130;
    border-radius: 16px;
    padding: 4px 14px;
}
QPushButton.winBtn {
    background: transparent;
    border: none;
    color: #8892b0;
    font-size: 14px;
    padding: 4px 10px;
}
QPushButton.winBtn:hover {
    color: #f3f4f6;
    background-color: #1c2130;
    border-radius: 6px;
}
QPushButton#winClose:hover {
    color: #ffffff;
    background-color: #ef4444;
}

/* 3-Card Deck */
QFrame#investigationCard, QFrame#terminalCard, QFrame#sessionsFilesCard, QFrame#bottomControlCard {
    background-color: #12151e;
    border: 1px solid #1c2130;
    border-radius: 12px;
}
QLabel[role="cardHeader"] {
    color: #e2e8f0;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.8px;
}
QLabel[role="sectionHeader"] {
    color: #8892b0;
    font-size: 11px;
    font-weight: 600;
}
QLabel[role="muted"] {
    color: #8892b0;
    font-size: 11px;
}
QLabel#targetLink {
    color: #38bdf8;
    font-size: 13px;
    font-weight: 600;
}
QLabel#targetLink:hover {
    text-decoration: underline;
}

/* Progress Bar */
QProgressBar#progressBar {
    background-color: #1a1f2c;
    border: none;
    border-radius: 3px;
    text-align: center;
}
QProgressBar#progressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10b981, stop:1 #34d399);
    border-radius: 3px;
}

/* Findings Buttons */
QPushButton#btnPotential {
    background-color: rgba(245, 158, 11, 0.12);
    border: 1px solid rgba(245, 158, 11, 0.35);
    border-radius: 8px;
    color: #f59e0b;
    font-weight: 600;
    font-size: 12px;
    padding: 8px 12px;
    text-align: left;
}
QPushButton#btnPotential:hover {
    background-color: rgba(245, 158, 11, 0.20);
    border-color: #f59e0b;
}
QPushButton#btnConfirmed {
    background-color: rgba(239, 68, 68, 0.12);
    border: 1px solid rgba(239, 68, 68, 0.35);
    border-radius: 8px;
    color: #ef4444;
    font-weight: 600;
    font-size: 12px;
    padding: 8px 12px;
    text-align: left;
}
QPushButton#btnConfirmed:hover {
    background-color: rgba(239, 68, 68, 0.20);
    border-color: #ef4444;
}

/* Activity feed / lists / trees */
QListWidget, QTreeWidget {
    background-color: #0d0f16;
    border: 1px solid #1a1e2b;
    border-radius: 8px;
    color: #f3f4f6;
    outline: 0;
    padding: 4px;
}
QListWidget::item {
    border-bottom: 1px solid #141722;
    padding: 5px 6px;
    border-radius: 4px;
}
QListWidget::item:hover {
    background-color: #141824;
}
QListWidget::item:selected, QTreeWidget::item:selected {
    background-color: #1a2030;
    color: #f3f4f6;
}
QTreeWidget::item {
    padding: 4px 4px;
}
QTreeWidget::branch:has-children:!has-siblings:closed,
QTreeWidget::branch:closed:has-children:has-siblings {
    color: #8892b0;
}

/* Scrollbars */
QScrollBar:vertical {
    background: #0d0f16;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #1c2130;
    border-radius: 4px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background: #2a3144;
}
QScrollBar:horizontal {
    background: #0d0f16;
    height: 8px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #1c2130;
    border-radius: 4px;
    min-width: 20px;
}
QScrollBar::add-line, QScrollBar::sub-line {
    height: 0;
    width: 0;
}
QAbstractScrollArea::corner {
    background: #0d0f16;
}

/* Terminal */
QTextEdit#terminalView, QPlainTextEdit#terminalView {
    background-color: #08090d;
    color: #e6edf3;
    border: 1px solid #181c26;
    border-radius: 8px;
    font-family: "SF Mono", "JetBrains Mono", "Menlo", "Consolas", monospace;
    font-size: 12px;
    line-height: 1.45;
    padding: 10px;
}
QPushButton.termBtn {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: #8892b0;
    font-size: 13px;
    padding: 4px 6px;
}
QPushButton.termBtn:hover {
    background-color: #1c2130;
    color: #f3f4f6;
}

/* Steering Deck */
QFrame#steeringContainer {
    background-color: #08090d;
    border: 1px solid #1e2434;
    border-radius: 20px;
    padding: 2px 10px;
}
QFrame#steeringContainer:focus-within {
    border: 1px solid #38bdf8;
}
QLineEdit#steeringInput {
    background-color: transparent;
    border: none;
    color: #f3f4f6;
    font-size: 13px;
    padding: 6px 4px;
}
QPushButton#btnSendSteering {
    background-color: transparent;
    border: none;
    color: #38bdf8;
    font-size: 15px;
    font-weight: 700;
    padding: 4px 6px;
}
QPushButton#btnSendSteering:hover {
    color: #60a5fa;
}

/* Pause / Stop Buttons */
QPushButton#btnPause {
    background-color: #1a1e28;
    border: 1px solid #2a3142;
    border-radius: 8px;
    color: #f1f5f9;
    font-weight: 600;
    font-size: 12px;
    padding: 7px 16px;
}
QPushButton#btnPause:hover {
    background-color: #222838;
    border-color: #f59e0b;
    color: #f59e0b;
}
QPushButton#btnStop {
    background-color: rgba(239, 68, 68, 0.12);
    border: 1px solid #ef4444;
    border-radius: 8px;
    color: #ef4444;
    font-weight: 600;
    font-size: 12px;
    padding: 7px 16px;
}
QPushButton#btnStop:hover {
    background-color: rgba(239, 68, 68, 0.22);
}

/* Splitter */
QSplitter::handle {
    background-color: transparent;
}
"""


def pill_qss(variant: str) -> str:
    """Inline stylesheet for a glowing status pill matching the reference mockup."""
    styles = {
        "green": ("rgba(16, 185, 129, 0.16)", GREEN_FG, GREEN_BORDER),
        "yellow": ("rgba(245, 158, 11, 0.16)", YELLOW_FG, YELLOW_FG),
        "red": ("rgba(239, 68, 68, 0.16)", RED_FG, RED_FG),
        "blue": ("rgba(56, 189, 248, 0.16)", ACCENT_BLUE, ACCENT_BLUE),
        "gray": ("rgba(136, 146, 176, 0.14)", TEXT_SECONDARY, BORDER_CARD),
    }
    bg, fg, border = styles.get(variant, styles["gray"])
    return (
        f"background-color: {bg}; color: {fg}; border: 1px solid {border};"
        "border-radius: 13px; padding: 4px 12px; font-weight: 700; font-size: 11px;"
    )


def apply_dark_theme(app: Any) -> None:
    app.setStyleSheet(QSS)
