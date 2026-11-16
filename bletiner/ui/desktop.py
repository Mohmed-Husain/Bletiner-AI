# mypy: ignore-errors
"""PySide6 desktop UI matching the reference SOC investigation deck layout.

Features:
- Left Navigation Sidebar Dock (Investigation, Sessions, Targets, Models, Settings, Docker status, v0.3.0)
- Top Bar (Bletiner AI brand, subtitle, Target/Mode capsule pill, glowing status badge, window controls)
- 3-Card Deck:
  1. Investigation Card (Target link, Progress bar 42%, Activity log checklist, Findings buttons)
  2. Kali Linux Terminal Card (Connected badge, copy/expand controls, colorized PTY output, sandbox caption)
  3. Sessions & Files Card (Collapsible hierarchical tree: current session, reports, evidence, logs, previous sessions)
- Bottom Steering Bar (Agent running pill, steering input with send icon, Pause & Stop buttons)
"""

from __future__ import annotations

import asyncio
import html
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bletiner.agent.events import AgentEvent, AgentState, EventType
from bletiner.agent.loop import AgentResult
from bletiner.ui import theme
from bletiner.ui.controller import InvestigationController, is_docker_available
from bletiner.ui.panels import ModelsPanel, SessionsPanel, SettingsPanel, TargetsPanel
from bletiner.ui.settings_manager import get_settings_manager
from bletiner.ui.viewmodels import FeedViewModel, new_terminal_token, status_label

_TERMINAL_HTML = """
<div style="font-family:'SF Mono','JetBrains Mono','Consolas',monospace; font-size:12px; line-height:1.45; color:#d1d5db;">
<div><span style="color:#ef4444; font-weight:700;">root@bletiner</span>:<span style="color:#38bdf8; font-weight:700;">/workspace</span># <span style="color:#ffffff;">nmap -sV example.com</span></div>
<br>
<div>Starting Nmap 7.94 ( https://nmap.org ) at 2026-11-11 12:05 IST</div>
<div>Nmap scan report for example.com (93.184.216.34)</div>
<div>Host is up (0.032s latency).</div>
<br>
<div style="color:#8892b0; font-weight:600;">PORT&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;STATE SERVICE&nbsp;&nbsp;VERSION</div>
<div><span style="color:#f3f4f6;">22/tcp</span>&nbsp;&nbsp;&nbsp;<span style="color:#10b981;">open</span>&nbsp;&nbsp;ssh&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;OpenSSH 8.9p1 (Ubuntu)</div>
<div><span style="color:#f3f4f6;">80/tcp</span>&nbsp;&nbsp;&nbsp;<span style="color:#10b981;">open</span>&nbsp;&nbsp;http&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Apache httpd 2.4.58</div>
<div><span style="color:#f3f4f6;">443/tcp</span>&nbsp;&nbsp;<span style="color:#10b981;">open</span>&nbsp;&nbsp;https&nbsp;&nbsp;&nbsp;&nbsp;Apache httpd 2.4.58 (SSL)</div>
<br>
<div><span style="color:#8892b0;">Service Info:</span> OS: Linux; CPE: cpe:/o:linux:linux_kernel</div>
<br>
<div>Nmap done: 1 IP address (1 host up) scanned in 8.42 seconds</div>
<br>
<div><span style="color:#ef4444; font-weight:700;">root@bletiner</span>:<span style="color:#38bdf8; font-weight:700;">/workspace</span># <span style="background-color:#ffffff; color:#08090d;">&nbsp;</span></div>
</div>
"""


def _pill(text: str, variant: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(theme.pill_qss(variant))
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


def _card_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "cardHeader")
    return lbl


class SidebarDock(QFrame):
    """Left navigation dock with brand icon, nav tabs, Docker status, and version."""

    tab_selected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sidebarDock")
        self.setFixedWidth(84)
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 12, 6, 12)
        col.setSpacing(8)

        # Brand shield icon card
        brand_card = QFrame()
        brand_card.setObjectName("sidebarBrand")
        brand_card.setFixedSize(56, 54)
        b_layout = QVBoxLayout(brand_card)
        b_layout.setContentsMargins(0, 0, 0, 0)
        shield = QLabel("\U0001f6e1")
        shield.setAlignment(Qt.AlignmentFlag.AlignCenter)
        shield.setStyleSheet("font-size: 24px; color: #38bdf8; background: transparent;")
        b_layout.addWidget(shield)
        col.addWidget(brand_card, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(10)

        # Navigation buttons
        self.buttons: dict[str, QPushButton] = {}
        nav_items = [
            ("Investigation", "\u2302", "Investigation"),
            ("Sessions", "\U0001f5c2", "Sessions"),
            ("Targets", "\u25ce", "Targets"),
            ("Models", "\u29c9", "Models"),
            ("Settings", "\u2699", "Settings"),
        ]

        for key, icon, label in nav_items:
            btn = QPushButton(f"{icon}\n{label}")
            btn.setFixedSize(72, 50)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            if key == "Investigation":
                btn.setProperty("class", "navBtnActive")
            else:
                btn.setProperty("class", "navBtn")
            btn.clicked.connect(lambda _, k=key: self._on_tab_click(k))
            self.buttons[key] = btn
            col.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)

        col.addStretch(1)

        # Docker status badge
        self.docker_card = QFrame()
        self.docker_card.setObjectName("dockerBadge")
        self.docker_card.setFixedWidth(70)
        self.docker_card.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.docker_card.mousePressEvent = lambda _: self.refresh_docker_status()
        d_layout = QVBoxLayout(self.docker_card)
        d_layout.setContentsMargins(4, 6, 4, 6)
        d_layout.setSpacing(2)

        self.docker_icon = QLabel("\U0001f4e6")
        self.docker_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.docker_icon.setStyleSheet("font-size: 14px; background: transparent; color: #34d399;")
        d_layout.addWidget(self.docker_icon)

        self.docker_status_lbl = QLabel("\u25cf Docker:\nRunning")
        self.docker_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.docker_status_lbl.setStyleSheet(
            "font-size: 9px; font-weight: 700; color: #10b981; background: transparent;"
        )
        d_layout.addWidget(self.docker_status_lbl)

        col.addWidget(self.docker_card, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(4)

        # Version string
        ver = QLabel("v0.3.0")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver.setStyleSheet("color: #4b5563; font-size: 10px; font-weight: 500; background: transparent;")
        col.addWidget(ver, 0, Qt.AlignmentFlag.AlignHCenter)

        # Periodic timer to check Docker status dynamically every 5s
        self._docker_timer = QTimer(self)
        self._docker_timer.setInterval(5000)
        self._docker_timer.timeout.connect(self.refresh_docker_status)
        self._docker_timer.start()

        # Dynamically refresh Docker status
        self.refresh_docker_status()

    def set_docker_status(self, running: bool) -> None:
        """Update sidebar Docker status icon and text dynamically."""
        if running:
            self.docker_icon.setStyleSheet("font-size: 14px; background: transparent; color: #34d399;")
            self.docker_status_lbl.setText("\u25cf Docker:\nRunning")
            self.docker_status_lbl.setStyleSheet(
                "font-size: 9px; font-weight: 700; color: #10b981; background: transparent;"
            )
        else:
            self.docker_icon.setStyleSheet("font-size: 14px; background: transparent; color: #ef4444;")
            self.docker_status_lbl.setText("\u25cb Docker:\nOffline")
            self.docker_status_lbl.setStyleSheet(
                "font-size: 9px; font-weight: 700; color: #ef4444; background: transparent;"
            )

    def refresh_docker_status(self) -> bool:
        """Check docker availability and refresh badge."""
        running = is_docker_available()
        self.set_docker_status(running)
        return running

    def select_tab(self, key: str) -> None:
        """Update button active states to match active tab."""
        for k, btn in self.buttons.items():
            btn.setProperty("class", "navBtnActive" if k == key else "navBtn")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _on_tab_click(self, key: str) -> None:
        self.select_tab(key)
        self.tab_selected.emit(key)


class TopBar(QFrame):
    """Top Bar with brand title, subtitle, target/mode capsule, status badge, and controls."""

    minimize = Signal()
    maximize = Signal()
    close_ = Signal()
    target_clicked = Signal()
    start_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("topBar")
        self.setFixedHeight(56)
        self.setMinimumWidth(0)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 6, 12, 6)
        row.setSpacing(12)

        # Left brand title & subtitle
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("Bletiner AI")
        title.setObjectName("brandTitle")
        subtitle = QLabel("Autonomous Security Investigation Workspace")
        subtitle.setObjectName("brandSubtitle")
        subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        subtitle.setMinimumWidth(0)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        row.addLayout(title_box)

        row.addStretch(1)

        # Capsule pill: Target & Mode
        self.capsule = QFrame()
        self.capsule.setObjectName("targetCapsule")
        self.capsule.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.capsule.setMinimumWidth(0)
        c_layout = QHBoxLayout(self.capsule)
        c_layout.setContentsMargins(12, 4, 12, 4)
        c_layout.setSpacing(10)
        self.c_text = QLabel(
            "<span style='color:#8892b0;'>\U0001f310 Target:</span> "
            "<span style='color:#f3f4f6; font-weight:600;'>example.com</span> "
            "<span style='color:#3a4256;'>&nbsp;|&nbsp;</span> "
            "<span style='color:#8892b0;'>\U0001f4da Mode:</span> "
            "<span style='color:#f3f4f6; font-weight:600;'>Deep</span>"
        )
        self.c_text.setStyleSheet("font-size: 11px; background: transparent;")
        self.c_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.c_text.setMinimumWidth(0)
        c_layout.addWidget(self.c_text)
        row.addWidget(self.capsule)

        # Quick start button in top bar
        self.btn_launch = QPushButton("▶  Start Scan")
        self.btn_launch.setObjectName("btnTopLaunch")
        self.btn_launch.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_launch.clicked.connect(self.start_clicked.emit)
        row.addWidget(self.btn_launch)

        # Clicking the capsule or label emits target_clicked
        self.capsule.mousePressEvent = self._on_capsule_click
        self.c_text.mousePressEvent = self._on_capsule_click

        # Status badge (Investigating / Paused / Stopped)
        self.status = _pill("\u25cf Investigating", "green")
        row.addWidget(self.status)
        row.addSpacing(8)

        # Window controls
        for text, obj, sig in (
            ("\u2014", "winMin", self.minimize),
            ("\u25a1", "winMax", self.maximize),
            ("\u2715", "winClose", self.close_),
        ):
            btn = QPushButton(text)
            btn.setObjectName(obj)
            btn.setProperty("class", "winBtn")
            btn.clicked.connect(sig.emit)
            row.addWidget(btn)

    def _on_capsule_click(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.target_clicked.emit()

    def set_target_and_mode(self, target: str, mode: str) -> None:
        """Update target and mode display inside the TopBar capsule."""
        mode_str = mode.title()
        self.c_text.setText(
            f"<span style='color:#8892b0;'>\U0001f310 Target:</span> "
            f"<span style='color:#f3f4f6; font-weight:600;'>{target}</span> "
            f"<span style='color:#3a4256;'>&nbsp;|&nbsp;</span> "
            f"<span style='color:#8892b0;'>\U0001f4da Mode:</span> "
            f"<span style='color:#f3f4f6; font-weight:600;'>{mode_str}</span>"
        )

    def set_state_label(self, text: str, variant: str) -> None:
        """Explicitly set status label text and styling variant."""
        self.status.setText(f"\u25cf {text}")
        self.status.setStyleSheet(theme.pill_qss(variant))

    def set_state(self, state: AgentState) -> None:
        variant = {
            AgentState.PAUSED: "yellow",
            AgentState.STOPPED: "gray",
            AgentState.COMPLETED: "green",
        }.get(state, "green")
        self.status.setText(f"\u25cf {status_label(state)}")
        self.status.setStyleSheet(theme.pill_qss(variant))


class FindingButton(QPushButton):
    """Button card with styled text on left and right chevron on right."""

    def __init__(self, icon_text: str, object_name: str) -> None:
        super().__init__()
        self.setObjectName(object_name)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedHeight(38)
        self.setMinimumWidth(0)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        self.label = QLabel(icon_text)
        self.label.setStyleSheet(
            "background: transparent; font-weight: 600; font-size: 12px; color: inherit;"
        )
        self.chev = QLabel(">")
        self.chev.setStyleSheet("background: transparent; font-weight: 600; font-size: 13px; color: inherit;")
        layout.addWidget(self.label)
        layout.addStretch(1)
        layout.addWidget(self.chev)

    def set_display_text(self, text: str) -> None:
        self.label.setText(text)
        self.setText(text)


class InvestigationCard(QFrame):
    """Card 1: Target link, progress bar, checklist activity feed, and findings buttons."""

    target_clicked = Signal()
    start_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("investigationCard")
        self.setMinimumWidth(260)
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(10)

        # Header
        col.addWidget(_card_header("\u2316  INVESTIGATION"))

        # Target section
        tlabel = QLabel("Target")
        tlabel.setProperty("role", "sectionHeader")
        col.addWidget(tlabel)
        self.target = QLabel("example.com \u2197")
        self.target.setObjectName("targetLink")
        self.target.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.target.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.target.mousePressEvent = self._on_target_click
        col.addWidget(self.target)

        # Target action buttons: Start Scan & Configure Target
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_start = QPushButton("▶  Start Scan")
        self.btn_start.setObjectName("btnStartScan")
        self.btn_start.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_start.clicked.connect(self.start_clicked.emit)

        self.btn_configure = QPushButton("⚙  Target")
        self.btn_configure.setObjectName("btnConfigTarget")
        self.btn_configure.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_configure.clicked.connect(self.target_clicked.emit)

        btn_row.addWidget(self.btn_start, 2)
        btn_row.addWidget(self.btn_configure, 1)
        col.addLayout(btn_row)

        # Progress section
        prog_header = QHBoxLayout()
        prog_label = QLabel("Progress")
        prog_label.setProperty("role", "sectionHeader")
        self.prog_pct = QLabel("42%")
        self.prog_pct.setStyleSheet("color: #e2e8f0; font-size: 11px; font-weight: 600;")
        prog_header.addWidget(prog_label)
        prog_header.addStretch(1)
        prog_header.addWidget(self.prog_pct)
        col.addLayout(prog_header)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("progressBar")
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(42)
        col.addWidget(self.progress_bar)

        # Activity log section
        activity = QLabel("Activity log")
        activity.setProperty("role", "sectionHeader")
        col.addWidget(activity)

        self.feed = FeedViewModel(max_lines=500)
        self.feed_list = QListWidget()
        self.feed_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.feed_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.feed_list.setWordWrap(False)
        self.feed_list.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        col.addWidget(self.feed_list, 1)

        # Findings section
        findings_hdr = QLabel("Findings")
        findings_hdr.setProperty("role", "sectionHeader")
        col.addWidget(findings_hdr)

        self.btn_potential = FindingButton("\u26a0\ufe0f  2 Potential", "btnPotential")
        self.btn_potential.setText("\u26a0\ufe0f  2 Potential")
        col.addWidget(self.btn_potential)

        self.btn_confirmed = FindingButton("\U0001f41e  1 Confirmed", "btnConfirmed")
        self.btn_confirmed.setText("\U0001f41e  1 Confirmed")
        col.addWidget(self.btn_confirmed)

    def _on_target_click(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.target_clicked.emit()

    def set_target(self, target: str) -> None:
        self.target.setText(f"{target} \u2197")

    def set_progress(self, percentage: int) -> None:
        percentage = max(0, min(100, int(percentage)))
        self.progress_bar.setValue(percentage)
        self.prog_pct.setText(f"{percentage}%")

    def add_activity_row(
        self, status_icon: str, title: str, timestamp: str, status_color: str = "#10b981"
    ) -> None:
        item = QListWidgetItem()
        item.setSizeHint(QSize(200, 28))
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(4, 2, 6, 2)
        hl.setSpacing(6)

        icon_lbl = QLabel(status_icon)
        icon_lbl.setStyleSheet(f"color: {status_color}; font-size: 13px; font-weight: 700;")
        text_lbl = QLabel(title)
        text_lbl.setStyleSheet("color: #e2e8f0; font-size: 11px;")
        text_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        time_lbl = QLabel(timestamp)
        time_lbl.setStyleSheet("color: #64748b; font-size: 10px;")

        hl.addWidget(icon_lbl)
        hl.addWidget(text_lbl, 1)
        hl.addWidget(time_lbl)

        self.feed_list.addItem(item)
        self.feed_list.setItemWidget(item, row)
        self.feed_list.scrollToBottom()

    def add_event(self, event: AgentEvent) -> None:
        line = self.feed.ingest(event)
        if line is not None:
            ts = event.timestamp.strftime("%H:%M:%S")
            desc = self.feed._format(event)
            if event.type == EventType.TOOL_COMPLETED:
                icon, color = "\u2713", "#10b981"
            elif event.type == EventType.TOOL_STARTED:
                icon, color = "\u29bf", "#38bdf8"
            elif event.type == EventType.POLICY_BLOCK:
                icon, color = "\u26d4", "#ef4444"
            elif event.type == EventType.FINDING:
                icon, color = "\u2605", "#f59e0b"
            else:
                icon, color = "\u25cf", "#38bdf8"
            self.add_activity_row(icon, desc, ts, color)

    def set_badges(self, potential: int, confirmed: int) -> None:
        self.btn_potential.set_display_text(f"\u26a0\ufe0f  {potential} Potential")
        self.btn_confirmed.set_display_text(f"\U0001f41e  {confirmed} Confirmed")


class TerminalInput(QLineEdit):
    """QLineEdit with Up/Down arrow history navigation support for the Kali terminal."""

    history_navigated = Signal(int)

    def keyPressEvent(self, event: Any) -> None:
        if event.key() == Qt.Key.Key_Up:
            self.history_navigated.emit(-1)
            event.accept()
        elif event.key() == Qt.Key.Key_Down:
            self.history_navigated.emit(1)
            event.accept()
        else:
            super().keyPressEvent(event)


class TerminalCard(QFrame):
    """Card 2: Kali Linux Terminal with Connected badge, interactive command prompt, and rich stream."""

    maximize_toggled = Signal()
    command_entered = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("terminalCard")
        self.setMinimumWidth(380)
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(10)

        self._history: list[str] = []
        self._history_index: int = -1

        # Header row
        header = QHBoxLayout()
        header.addWidget(_card_header(">_  KALI LINUX TERMINAL"))
        header.addStretch(1)

        self.conn = _pill("\u25cf Connected", "green")
        header.addWidget(self.conn)
        header.addSpacing(6)

        # Action buttons: Copy, Maximize, Options
        self.btn_copy = QPushButton("\u29c9")
        self.btn_copy.setToolTip("Copy terminal text")
        self.btn_copy.setProperty("class", "termBtn")
        self.btn_copy.clicked.connect(self._copy_text)
        header.addWidget(self.btn_copy)

        self.btn_expand = QPushButton("\u2922")
        self.btn_expand.setToolTip("Toggle expand")
        self.btn_expand.setProperty("class", "termBtn")
        self.btn_expand.clicked.connect(self.maximize_toggled.emit)
        header.addWidget(self.btn_expand)

        self.btn_opts = QPushButton("\u22ee")
        self.btn_opts.setToolTip("Terminal Options")
        self.btn_opts.setProperty("class", "termBtn")
        self.btn_opts.clicked.connect(self._show_options_menu)
        header.addWidget(self.btn_opts)

        col.addLayout(header)

        # Terminal text display
        self.view = QTextEdit()
        self.view.setObjectName("terminalView")
        self.view.setReadOnly(True)
        self.view.setHtml(_TERMINAL_HTML)
        self.view.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        col.addWidget(self.view, 1)

        # Interactive Terminal Input Bar
        self.cmd_bar = QFrame()
        self.cmd_bar.setObjectName("terminalInputBar")
        bar_layout = QHBoxLayout(self.cmd_bar)
        bar_layout.setContentsMargins(6, 4, 6, 4)
        bar_layout.setSpacing(6)

        self.prompt_lbl = QLabel(
            "<span style='color:#ef4444; font-weight:700;'>root@bletiner</span>:<span style='color:#38bdf8; font-weight:700;'>/workspace</span># "
        )
        self.prompt_lbl.setStyleSheet(
            "font-family:'SF Mono','JetBrains Mono',monospace; font-size:12px; background:transparent;"
        )
        bar_layout.addWidget(self.prompt_lbl)

        self.cmd_input = TerminalInput()
        self.cmd_input.setObjectName("terminalCmdInput")
        self.cmd_input.setPlaceholderText(
            "Type Kali command (e.g. nmap -sV target, curl -I url, whois domain, ls -la) and press Enter..."
        )
        self.cmd_input.returnPressed.connect(self._on_submit_command)
        self.cmd_input.history_navigated.connect(self._on_history_navigate)
        bar_layout.addWidget(self.cmd_input, 1)

        self.btn_run = QPushButton("\u21b5 Run")
        self.btn_run.setObjectName("btnRunCmd")
        self.btn_run.setToolTip("Run command directly in Kali Linux sandbox")
        self.btn_run.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_run.clicked.connect(self._on_submit_command)
        bar_layout.addWidget(self.btn_run)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setObjectName("btnClearTerm")
        self.btn_clear.setToolTip("Clear terminal buffer")
        self.btn_clear.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_clear.clicked.connect(self.clear_terminal)
        bar_layout.addWidget(self.btn_clear)

        col.addWidget(self.cmd_bar)

        # Caption
        caption = QLabel("\u24d8  Agent and user share the sandbox workspace.")
        caption.setStyleSheet("color: #64748b; font-size: 11px; background: transparent;")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        caption.setMinimumWidth(0)
        col.addWidget(caption)

        self.bridge_token = new_terminal_token()

    def _on_submit_command(self) -> None:
        cmd = self.cmd_input.text().strip()
        if not cmd:
            return
        if not self._history or self._history[-1] != cmd:
            self._history.append(cmd)
        self._history_index = -1
        self.cmd_input.clear()
        self.command_entered.emit(cmd)

    def _on_history_navigate(self, direction: int) -> None:
        if not self._history:
            return
        if direction == -1:  # Up / older
            if self._history_index == -1:
                self._history_index = len(self._history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
        elif direction == 1 and self._history_index != -1:
            if self._history_index < len(self._history) - 1:
                self._history_index += 1
            else:
                self._history_index = -1
                self.cmd_input.clear()
                return
        if 0 <= self._history_index < len(self._history):
            self.cmd_input.setText(self._history[self._history_index])
            self.cmd_input.setCursorPosition(len(self.cmd_input.text()))

    def append_user_command(self, cmd: str) -> None:
        """Echo user-executed shell command to the terminal stream."""
        escaped = html.escape(cmd)
        self.view.append(
            f"<div style='margin-top:8px; margin-bottom:2px; font-family:\"SF Mono\",\"JetBrains Mono\",monospace; font-size:12px;'>"
            f"<span style='color:#ef4444; font-weight:700;'>root@bletiner</span>:<span style='color:#38bdf8; font-weight:700;'>/workspace</span># "
            f"<span style='color:#f8fafc; font-weight:600;'>{escaped}</span>"
            f"</div>"
        )
        sb = self.view.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def append_command_result(self, exit_code: int, stdout: str, stderr: str) -> None:
        """Display stdout/stderr from direct interactive terminal execution."""
        if stdout:
            escaped_out = html.escape(stdout).replace("\n", "<br>")
            self.view.append(
                f"<div style='font-family:\"SF Mono\",\"JetBrains Mono\",monospace; font-size:11px; color:#cbd5e1; margin-left:4px; line-height:1.4;'>{escaped_out}</div>"
            )
        if stderr:
            escaped_err = html.escape(stderr).replace("\n", "<br>")
            self.view.append(
                f"<div style='font-family:\"SF Mono\",\"JetBrains Mono\",monospace; font-size:11px; color:#f87171; margin-left:4px;'>{escaped_err}</div>"
            )
        if exit_code != 0 and not stderr and not stdout:
            self.view.append(
                f"<div style='font-family:\"SF Mono\",\"JetBrains Mono\",monospace; font-size:11px; color:#f87171; margin-left:4px;'>[Process exited with code {exit_code}]</div>"
            )
        sb = self.view.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def clear_terminal(self) -> None:
        """Clear terminal view buffer."""
        self.view.clear()
        self.view.setHtml(
            "<div style=\"font-family:'SF Mono','JetBrains Mono','Consolas',monospace; font-size:12px; color:#d1d5db;\">"
            "<div><span style='color:#ef4444; font-weight:700;'>root@bletiner</span>:<span style='color:#38bdf8; font-weight:700;'>/workspace</span># </div>"
            "</div>"
        )

    def show_terminal_help(self) -> None:
        """Show interactive Kali terminal help and tool shortcuts."""
        help_html = (
            "<div style='color:#38bdf8; font-weight:600;'><br>--- Kali Linux Interactive Terminal Help ---</div>"
            "<div style='color:#94a3b8; font-size:11px; margin-left:4px; font-family:monospace; line-height:1.5;'>"
            "Direct sandbox execution (unprivileged 'bletiner' in /workspace):<br>"
            "  \u2022 <b>nmap</b> -sT -F &lt;target&gt; &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Fast TCP connect port scan<br>"
            "  \u2022 <b>nmap</b> -sT -sV -F &lt;target&gt; &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Service banner fingerprinting<br>"
            "  \u2022 <b>whois</b> &lt;domain&gt; &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;WHOIS domain registration lookup<br>"
            "  \u2022 <b>curl</b> -sS -I &lt;url&gt; &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;HTTP response headers inspect<br>"
            "  \u2022 <b>whatweb</b> &lt;url&gt; &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Web technology stack analysis<br>"
            "  \u2022 <b>dig</b> &lt;domain&gt; +short &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;DNS resolver records query<br>"
            "  \u2022 <b>nikto</b> -h &lt;target&gt; &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Web vulnerability assessment<br>"
            "  \u2022 <b>ls -la</b> / <b>pwd</b> / <b>id</b> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Inspect sandbox filesystem<br>"
            "  \u2022 <b>clear</b> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Clear terminal buffer<br>"
            "</div>"
        )
        self.view.append(help_html)
        sb = self.view.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _show_options_menu(self) -> None:
        menu = QMenu(self)
        clear_act = menu.addAction("Clear Terminal Buffer")
        clear_act.triggered.connect(self.clear_terminal)
        help_act = menu.addAction("Show Security Tools (Help)")
        help_act.triggered.connect(self.show_terminal_help)
        copy_act = menu.addAction("Copy All Output")
        copy_act.triggered.connect(self._copy_text)
        menu.exec(self.btn_opts.mapToGlobal(self.btn_opts.rect().bottomLeft()))

    def _copy_text(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.view.toPlainText())

    def append_dispatch(self, argv: str) -> None:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        self.view.append(
            f"<div style='color:#38bdf8; font-weight:600;'><br>--- [AGENT DISPATCH: {argv}] [{ts}] ---</div>"
        )
        sb = self.view.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def append_tool_output(self, tool_name: str, argv: list[str], output: str) -> None:
        argv_str = " ".join(argv) if isinstance(argv, list) else str(argv)
        self.append_dispatch(f"{tool_name}: {argv_str}")
        if output:
            escaped = html.escape(output).replace("\n", "<br>")
            self.view.append(
                f"<div style='font-family:monospace; font-size:11px; color:#cbd5e1; margin-left:8px;'>{escaped}</div>"
            )
            sb = self.view.verticalScrollBar()
            if sb is not None:
                sb.setValue(sb.maximum())


class SessionsFilesCard(QFrame):
    """Card 3: Current investigation files (report, evidence, logs) and previous sessions."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sessionsFilesCard")
        self.setMinimumWidth(220)
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(10)
        col.addWidget(_card_header("\U0001f4c1  SESSIONS & FILES"))

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        self._populate_demo()
        col.addWidget(self.tree, 1)

    def _populate_demo(self) -> None:
        """Seed the tree with standard demo hierarchy."""
        self.tree.clear()
        current = QTreeWidgetItem(["\U0001f4c1 Current investigation"])
        current.setExpanded(True)

        target_folder = QTreeWidgetItem(["\U0001f4c1 example.com"])
        target_folder.addChild(QTreeWidgetItem(["\U0001f4c4 report.md"]))
        target_folder.addChild(QTreeWidgetItem(["\U0001f310 report.html"]))

        evidence = QTreeWidgetItem(["\U0001f4c1 evidence/"])
        evidence.addChild(QTreeWidgetItem(["ev_8f10_banner.txt"]))
        evidence.addChild(QTreeWidgetItem(["ev_c491_sqli.json"]))

        logs = QTreeWidgetItem(["\U0001f4c1 logs/"])
        logs.addChild(QTreeWidgetItem(["agent_runtime.log"]))
        logs.addChild(QTreeWidgetItem(["terminal_audit.log"]))

        target_folder.addChild(evidence)
        target_folder.addChild(logs)
        target_folder.setExpanded(True)
        evidence.setExpanded(True)
        logs.setExpanded(True)

        current.addChild(target_folder)
        self.tree.addTopLevelItem(current)

        # Previous sessions section
        previous = QTreeWidgetItem(["\U0001f552 Previous sessions"])
        previous.addChild(QTreeWidgetItem(["\U0001f4c4 Session 001         2026-11-10"]))
        previous.addChild(QTreeWidgetItem(["\U0001f4c4 Session 002         2026-11-09"]))
        previous.setExpanded(True)
        self.tree.addTopLevelItem(previous)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        path_str = item.data(0, Qt.ItemDataRole.UserRole)
        if path_str:
            path = Path(path_str)
            if path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def populate_session(
        self,
        target: str,
        paths: Any | None = None,
        *,
        previous_sessions: list[str] | None = None,
    ) -> None:
        """Populate tree with actual generated files from investigation session."""
        if paths is None:
            return

        self.tree.clear()
        current = QTreeWidgetItem(["\U0001f4c1 Current investigation"])
        current.setExpanded(True)

        target_folder = QTreeWidgetItem([f"\U0001f4c1 {target}"])
        target_folder.setExpanded(True)

        # Reports
        reports_dir = getattr(paths, "reports", None)
        if reports_dir and Path(reports_dir).is_dir():
            md_path = Path(reports_dir) / "report.md"
            if md_path.exists():
                md_item = QTreeWidgetItem(["\U0001f4c4 report.md"])
                md_item.setData(0, Qt.ItemDataRole.UserRole, str(md_path))
                target_folder.addChild(md_item)

            html_path = Path(reports_dir) / "report.html"
            if html_path.exists():
                html_item = QTreeWidgetItem(["\U0001f310 report.html"])
                html_item.setData(0, Qt.ItemDataRole.UserRole, str(html_path))
                target_folder.addChild(html_item)

        # Evidence files
        evidence_dir = getattr(paths, "evidence", None)
        evidence_item = QTreeWidgetItem(["\U0001f4c1 evidence/"])
        evidence_item.setExpanded(True)
        if evidence_dir and Path(evidence_dir).is_dir():
            for p in sorted(Path(evidence_dir).iterdir()):
                if p.is_file():
                    item = QTreeWidgetItem([f"\U0001f4c4 {p.name}"])
                    item.setData(0, Qt.ItemDataRole.UserRole, str(p))
                    evidence_item.addChild(item)
        target_folder.addChild(evidence_item)

        # Logs
        logs_dir = getattr(paths, "logs", None)
        logs_item = QTreeWidgetItem(["\U0001f4c1 logs/"])
        logs_item.setExpanded(True)
        if logs_dir and Path(logs_dir).is_dir():
            for p in sorted(Path(logs_dir).iterdir()):
                if p.is_file():
                    item = QTreeWidgetItem([f"\U0001f4c4 {p.name}"])
                    item.setData(0, Qt.ItemDataRole.UserRole, str(p))
                    logs_item.addChild(item)
        target_folder.addChild(logs_item)

        current.addChild(target_folder)
        self.tree.addTopLevelItem(current)

        # Previous sessions
        prev_item = QTreeWidgetItem(["\U0001f552 Previous sessions"])
        prev_item.setExpanded(True)
        root_dir = getattr(paths, "root", None)
        if root_dir and Path(root_dir).parent.is_dir():
            for sdir in sorted(Path(root_dir).parent.iterdir(), reverse=True):
                if sdir.is_dir() and sdir != Path(root_dir):
                    item = QTreeWidgetItem([f"\U0001f4c1 {sdir.name}"])
                    item.setData(0, Qt.ItemDataRole.UserRole, str(sdir))
                    prev_item.addChild(item)
        if prev_item.childCount() == 0 and previous_sessions:
            for s in previous_sessions:
                prev_item.addChild(QTreeWidgetItem([f"\U0001f4c4 {s}"]))
        self.tree.addTopLevelItem(prev_item)


class BottomControlCard(QFrame):
    """Bottom Steering Bar with Agent running pill, search/steer input, Pause, and Stop."""

    steering_submitted = Signal(str)
    pause_clicked = Signal()
    stop_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("bottomControlCard")
        self.setFixedHeight(58)
        self.setMinimumWidth(0)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(12)

        # Agent running pill
        self.state = _pill("\u2728 Agent running", "green")
        row.addWidget(self.state)

        # Capsule Steering Input container
        input_container = QFrame()
        input_container.setObjectName("steeringContainer")
        input_container.setMinimumWidth(0)
        ic_layout = QHBoxLayout(input_container)
        ic_layout.setContentsMargins(12, 0, 6, 0)
        ic_layout.setSpacing(6)

        steer_icon = QLabel("\u27a4")
        steer_icon.setStyleSheet("color: #60a5fa; font-size: 13px; background: transparent;")
        ic_layout.addWidget(steer_icon)

        self.input = QLineEdit()
        self.input.setObjectName("steeringInput")
        self.input.setPlaceholderText(
            "Send instructions to steer, or type 'scan <target>' and press Enter..."
        )
        self.input.returnPressed.connect(self._emit_steering)
        self.input.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.input.setMinimumWidth(0)
        ic_layout.addWidget(self.input, 1)

        btn_send = QPushButton("\u27a4")
        btn_send.setObjectName("btnSendSteering")
        btn_send.setToolTip("Send steering prompt")
        btn_send.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_send.clicked.connect(self._emit_steering)
        ic_layout.addWidget(btn_send)

        row.addWidget(input_container, 1)

        # Pause and Stop buttons
        pause = QPushButton("\u2016 Pause")
        pause.setObjectName("btnPause")
        pause.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        pause.clicked.connect(self.pause_clicked.emit)

        stop = QPushButton("\u25a0 Stop")
        stop.setObjectName("btnStop")
        stop.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        stop.clicked.connect(self.stop_clicked.emit)

        row.addWidget(pause)
        row.addWidget(stop)

    def _emit_steering(self) -> None:
        text = self.input.text().strip()
        if text:
            self.steering_submitted.emit(text)
            self.input.clear()

    def set_state(self, text: str, variant: str) -> None:
        self.state.setText(f"\u2728 {text}")
        self.state.setStyleSheet(theme.pill_qss(variant))


class MainWindow(QMainWindow):
    """Main workstation window combining the Left Sidebar Dock, Top Bar, 3-Card Deck, and Bottom Bar."""

    def __init__(self, controller: InvestigationController | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Bletiner AI \u2014 Autonomous Security Workstation")
        self.resize(1360, 860)

        # Controller connection
        self.controller = controller or InvestigationController()
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        self._current_target = "example.com"
        self._current_mode = "deep"
        self._potential_findings = 2
        self._confirmed_findings = 1

        central = QWidget()
        h_root = QHBoxLayout(central)
        h_root.setContentsMargins(0, 0, 0, 0)
        h_root.setSpacing(0)

        # 1. Left Sidebar Dock
        self.sidebar = SidebarDock()
        h_root.addWidget(self.sidebar)

        # 2. Main content vertical layout
        main_content = QWidget()
        v_main = QVBoxLayout(main_content)
        v_main.setContentsMargins(0, 0, 0, 0)
        v_main.setSpacing(0)

        # Top Bar
        self.top = TopBar()
        self.top.minimize.connect(self.showMinimized)
        self.top.maximize.connect(self._toggle_max)
        self.top.close_.connect(self.close)
        self.top.target_clicked.connect(self.open_new_investigation_dialog)
        self.top.start_clicked.connect(self._on_start_btn_clicked)
        v_main.addWidget(self.top)

        # 3-Card Deck (Housed in QSplitter)
        deck_holder = QWidget()
        deck = QHBoxLayout(deck_holder)
        deck.setContentsMargins(10, 8, 10, 6)
        deck.setSpacing(10)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(True)
        self.investigation = InvestigationCard()
        self.terminal = TerminalCard()
        self.sessions = SessionsFilesCard()

        self.terminal.maximize_toggled.connect(self._toggle_terminal_expand)
        self.terminal.command_entered.connect(self._on_terminal_command)
        self.investigation.target_clicked.connect(self.open_new_investigation_dialog)
        self.investigation.start_clicked.connect(self._on_start_btn_clicked)

        self.splitter.addWidget(self.investigation)
        self.splitter.addWidget(self.terminal)
        self.splitter.addWidget(self.sessions)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([320, 720, 280])
        deck.addWidget(self.splitter)

        # Bottom Steering Bar
        bottom_holder = QWidget()
        bl = QHBoxLayout(bottom_holder)
        bl.setContentsMargins(10, 0, 10, 10)
        self.bottom = BottomControlCard()
        self.bottom.steering_submitted.connect(self._on_steering)
        self.bottom.pause_clicked.connect(self._on_pause)
        self.bottom.stop_clicked.connect(self._on_stop)
        bl.addWidget(self.bottom)

        # Stacked Pages
        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(800)

        # Page 0: Investigation Workspace (3-Card Deck + Bottom Steering Bar)
        self.investigation_page = QWidget()
        inv_vbox = QVBoxLayout(self.investigation_page)
        inv_vbox.setContentsMargins(0, 0, 0, 0)
        inv_vbox.setSpacing(0)
        inv_vbox.addWidget(deck_holder, 1)
        inv_vbox.addWidget(bottom_holder)
        self.stack.addWidget(self.investigation_page)

        # Page 1: Sessions Panel
        self.sessions_panel = SessionsPanel()
        self.stack.addWidget(self.sessions_panel)

        # Page 2: Targets Panel
        self.targets_panel = TargetsPanel()
        self.targets_panel.target_selected.connect(self._on_target_panel_selected)
        self.stack.addWidget(self.targets_panel)

        # Page 3: Models Panel
        self.models_panel = ModelsPanel()
        self.models_panel.model_changed.connect(self._on_model_panel_changed)
        self.stack.addWidget(self.models_panel)

        # Page 4: Settings Panel
        self.settings_panel = SettingsPanel()
        self.stack.addWidget(self.settings_panel)

        v_main.addWidget(self.stack, 1)
        self.sidebar.tab_selected.connect(self._on_sidebar_tab)

        h_root.addWidget(main_content, 1)
        self.setCentralWidget(central)

        self._is_paused = False
        self._is_stopped = False
        self._terminal_expanded = False
        self._seed_ready()

        # Connect controller signals
        self.controller.on_event.connect(self._on_agent_event)
        self.controller.on_tool_output.connect(self._on_tool_output)
        self.controller.on_state_change.connect(self._on_controller_state_change)
        self.controller.on_completed.connect(self._on_completed)
        self.controller.on_error.connect(self._on_error)

    def _on_sidebar_tab(self, key: str) -> None:
        """Switch views in the main stacked widget."""
        tab_indices = {
            "Investigation": 0,
            "Sessions": 1,
            "Targets": 2,
            "Models": 3,
            "Settings": 4,
        }
        idx = tab_indices.get(key, 0)
        self.stack.setCurrentIndex(idx)
        if key == "Sessions":
            self.sessions_panel.refresh()
        elif key == "Settings":
            self.settings_panel.refresh()
        elif key == "Investigation":
            self.sidebar.refresh_docker_status()

    def switch_to_tab(self, key: str) -> None:
        """Programmatically switch sidebar button and stacked page."""
        self.sidebar.select_tab(key)
        self._on_sidebar_tab(key)

    def _on_target_panel_selected(self, target: str, mode: str) -> None:
        """Apply target and mode selected from the Targets view."""
        self._current_target = target
        self._current_mode = mode
        self.top.set_target_and_mode(target, mode)
        self.investigation.set_target(target)
        self.switch_to_tab("Investigation")
        self.start_investigation(target, mode)

    def _on_model_panel_changed(self, model_id: str, provider: str) -> None:
        """Handle AI model selection change."""
        model_name = self.models_panel.get_active_model_name()
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        self.investigation.add_activity_row(
            "⚡", f"AI Model Selected: {model_name} ({provider.title()})", ts, "#38bdf8"
        )
        self.terminal.append_dispatch(f"AI Model configured: {model_name} [provider={provider}]")

    def _toggle_max(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _toggle_terminal_expand(self) -> None:
        self._terminal_expanded = not self._terminal_expanded
        self.investigation.setVisible(not self._terminal_expanded)
        self.sessions.setVisible(not self._terminal_expanded)

    def _seed_ready(self) -> None:
        """Initialize the UI in a clean standby state ready for the operator to start."""
        self.investigation.feed_list.clear()
        self.investigation.add_activity_row(
            "\u25cb",
            "Standby \u2014 Click 'Start Scan' to begin",
            datetime.now(UTC).strftime("%H:%M:%S"),
            "#38bdf8",
        )
        self.top.set_state_label("Ready \u2014 Click 'Start Scan'", "green")
        self.bottom.set_state("Ready to scan", "green")
        self.investigation.set_progress(0)
        self.investigation.set_badges(0, 0)

    def _seed_demo(self) -> None:
        """Seed the activity log with demo checklist steps."""
        self._seed_ready()

    def _on_start_btn_clicked(self) -> None:
        if self.controller.is_running:
            return
        self.start_investigation(self._current_target, self._current_mode)

    def open_new_investigation_dialog(self) -> None:
        """Open dark-themed modal dialog or navigate to Targets tab."""
        self.switch_to_tab("Targets")

    def start_investigation(
        self,
        target: str,
        mode: str = "deep",
        provider: str | None = None,
        api_key: str | None = None,
    ) -> asyncio.Task[AgentResult] | None:
        """Configure target and start investigation loop via the controller."""
        mgr = get_settings_manager()
        active_provider = provider or mgr.settings.active_provider
        active_model = mgr.settings.active_model
        model_name = self.models_panel.get_active_model_name() if hasattr(self, "models_panel") else active_model

        # Verification: if a real LLM is chosen without an API key, do not silently fake results!
        effective_key = api_key or mgr.get_api_key(active_provider)
        if active_provider not in ("fake", "ollama") and not effective_key:
            ts = datetime.now(UTC).strftime("%H:%M:%S")
            self.investigation.add_activity_row(
                "❌", f"Missing API Key for {active_provider.title()}.", ts, "#ef4444"
            )
            self.terminal.append_dispatch(
                f"[ERROR] API Key for {active_provider.title()} ({model_name}) is not configured."
            )
            self.terminal.append_dispatch(
                "Please open the 'Settings' tab in the left sidebar to enter your API key, or switch to 'Deterministic Simulation Lab' in the 'Models' tab."
            )
            self.investigation.btn_start.setText("▶  Start Scan")
            self.investigation.btn_start.setEnabled(True)
            self.top.btn_launch.setText("▶  Start Scan")
            self.top.btn_launch.setEnabled(True)
            return None

        self._current_target = target
        self._current_mode = mode
        self._potential_findings = 0
        self._confirmed_findings = 0
        self.top.set_target_and_mode(target, mode)
        self.investigation.set_target(target)
        self.investigation.set_progress(5)
        self.investigation.set_badges(0, 0)

        # Update button text to show live status
        self.investigation.btn_start.setText("\u23f3  Scanning...")
        self.investigation.btn_start.setEnabled(False)
        self.top.btn_launch.setText("\u23f3  Scanning...")
        self.top.btn_launch.setEnabled(False)

        # Clear feed and terminal for the new live run
        self.investigation.feed_list.clear()
        self.terminal.view.clear()

        sim_notice = (
            "<div style='color:#f59e0b; font-weight:700;'>[SIMULATION LAB - OFFLINE DEMO] Running deterministic lab demo. No live target packets dispatched.</div>"
            if active_provider == "fake"
            else f"<div style='color:#10b981; font-weight:700;'>[LIVE AGENT ENGAGED] Using {model_name} ({active_provider.title()})</div>"
        )
        self.terminal.view.setHtml(
            f"<div style=\"font-family:'SF Mono','JetBrains Mono','Consolas',monospace; font-size:12px; color:#d1d5db;\">"
            f"<div><span style='color:#ef4444; font-weight:700;'>root@bletiner</span>:<span style='color:#38bdf8; font-weight:700;'>/workspace</span># bletiner investigate --target {html.escape(target)} --mode {mode} --model {html.escape(active_model)}</div>"
            f"{sim_notice}"
            f"<div style='color:#38bdf8;'><br>[SYSTEM] Initializing autonomous security agent loop...</div>"
            f"<div style='color:#8892b0;'>Target: {html.escape(target)} | Model: {html.escape(model_name)} | Mode: {mode.upper()}</div><br></div>"
        )

        ts = datetime.now(UTC).strftime("%H:%M:%S")
        if active_provider == "fake":
            self.investigation.add_activity_row("🔬", "Simulation Lab active (offline demo mode)", ts, "#f59e0b")
        else:
            self.investigation.add_activity_row("🤖", f"AI Model active: {model_name}", ts, "#10b981")
        self.investigation.add_activity_row(
            "\u25b6", f"Target configured: {target} ({mode.upper()})", ts, "#38bdf8"
        )
        self.terminal.append_dispatch(f"starting investigation against {target} [mode={mode}, model={model_name}]")

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(
                self.controller.start(
                    target=target,
                    mode=mode,
                    provider=active_provider,
                    api_key=effective_key,
                    model=active_model,
                    step_delay_s=0.5,
                )
            )
            return task
        except RuntimeError:
            return None

    def _on_terminal_command(self, cmd: str) -> None:
        """Handle operator direct command input in the Kali terminal without requiring AI scan."""
        cmd_clean = cmd.strip()
        if not cmd_clean:
            return

        self.terminal.append_user_command(cmd_clean)

        if cmd_clean.lower() in ("clear", "cls"):
            self.terminal.clear_terminal()
            return
        elif cmd_clean.lower() in ("help", "?"):
            self.terminal.show_terminal_help()
            return

        async def _run() -> None:
            exit_code, stdout, stderr = await self.controller.execute_terminal_command(cmd_clean)
            self.terminal.append_command_result(exit_code, stdout, stderr)

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_run())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        except RuntimeError:
            try:
                exit_code, stdout, stderr = asyncio.run(self.controller.execute_terminal_command(cmd_clean))
                self.terminal.append_command_result(exit_code, stdout, stderr)
            except Exception as e:
                self.terminal.append_command_result(1, "", f"Execution error: {e}")

    def _on_steering(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return

        # If investigation is running, send as steering instruction to AI agent
        if self.controller.is_running:
            ts = datetime.now(UTC).strftime("%H:%M:%S")
            self.investigation.add_activity_row("\u27a4", f"Steering: {text}", ts, "#38bdf8")
            self.terminal.append_dispatch(f"operator steering: {text}")
            self.controller.submit_steering(text)
            return

        # Not running - check if explicit scan or investigate command
        if cleaned.lower().startswith("scan "):
            target = cleaned[5:].strip()
            self.start_investigation(target, self._current_mode)
            return
        elif cleaned.lower().startswith("investigate "):
            target = cleaned[12:].strip()
            self.start_investigation(target, self._current_mode)
            return

        # Check if user typed a terminal/shell tool command
        cmd_words = cleaned.split()
        first_word = cmd_words[0].lower() if cmd_words else ""
        known_shell_cmds = {
            "nmap", "curl", "whois", "whatweb", "dig", "nikto", "ls", "pwd", "cat",
            "ping", "echo", "id", "bash", "sh", "find", "grep", "ps", "clear", "cls",
            "uname", "df", "which", "head", "tail", "ip", "ifconfig", "netstat", "ss",
            "help", "python", "python3"
        }
        if first_word in known_shell_cmds or (
            len(cmd_words) > 1 and not (cleaned.startswith("http://") or cleaned.startswith("https://"))
        ):
            self._on_terminal_command(cleaned)
            return

        # If it looks like a domain / target, start scan
        if "." in cleaned or "localhost" in cleaned:
            self.start_investigation(cleaned, self._current_mode)
            return

        # Default fallback: execute as command in terminal
        self._on_terminal_command(cleaned)

    def _on_pause(self) -> None:
        now_paused = self.controller.pause()
        self._is_paused = now_paused
        if now_paused:
            self.top.set_state(AgentState.PAUSED)
            self.bottom.set_state("Agent paused", "yellow")
        else:
            self.top.set_state(AgentState.EXECUTING)
            self.bottom.set_state("Agent running", "green")

    def _on_stop(self) -> None:
        self.controller.stop()
        self._is_stopped = True
        self.top.set_state(AgentState.STOPPED)
        self.bottom.set_state("Agent stopped", "gray")

    def _on_agent_event(self, event: AgentEvent) -> None:
        self.investigation.add_event(event)

        # Update progress percentage based on state transitions
        if event.type == EventType.STATE_CHANGED:
            state = str(event.payload.get("state", ""))
            pct_map = {
                AgentState.INITIALIZING.value: 10,
                AgentState.PLANNING.value: 25,
                AgentState.EXECUTING.value: 50,
                AgentState.OBSERVING.value: 65,
                AgentState.ANALYZING.value: 80,
                AgentState.REPLANNING.value: 85,
                AgentState.REPORT_GENERATION.value: 95,
                AgentState.COMPLETED.value: 100,
            }
            if state in pct_map:
                self.investigation.set_progress(pct_map[state])

        elif event.type == EventType.TOOL_COMPLETED:
            cur = self.investigation.progress_bar.value()
            if cur < 90:
                self.investigation.set_progress(min(90, cur + 8))

        elif event.type == EventType.FINDING:
            sev = str(event.payload.get("severity", "INFO")).upper()
            if sev in ("HIGH", "CRITICAL"):
                self._confirmed_findings += 1
            else:
                self._potential_findings += 1
            self.investigation.set_badges(self._potential_findings, self._confirmed_findings)

    def _on_tool_output(self, tool_name: str, argv: list[str], output: str) -> None:
        self.terminal.append_tool_output(tool_name, argv, output)

    def _on_controller_state_change(self, state: AgentState) -> None:
        self.top.set_state(state)
        if state == AgentState.PAUSED:
            self.bottom.set_state("Agent paused", "yellow")
        elif state == AgentState.STOPPED:
            self.bottom.set_state("Agent stopped", "gray")
        elif state == AgentState.COMPLETED:
            self.bottom.set_state("Investigation completed", "green")
        elif state == AgentState.EXECUTING:
            self.bottom.set_state("Executing tool", "green")
        elif state == AgentState.PLANNING:
            self.bottom.set_state("Agent planning", "green")
        else:
            self.bottom.set_state(status_label(state), "green")

    def _on_completed(self, result: AgentResult, paths: Any) -> None:
        self.investigation.set_progress(100)
        self.investigation.btn_start.setText("▶  Start Scan")
        self.investigation.btn_start.setEnabled(True)
        self.top.btn_launch.setText("▶  Start Scan")
        self.top.btn_launch.setEnabled(True)
        self.sessions.populate_session(self._current_target, paths)
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        self.investigation.add_activity_row("\u2713", f"Completed: {result.stop_reason}", ts, "#10b981")
        self.terminal.append_dispatch(
            f"investigation finished: {result.stop_reason} ({len(result.findings)} findings)"
        )
        self.terminal.append_dispatch(
            "reports generated: report.md and report.html (double-click in Sessions & Files to view)"
        )

    def _on_error(self, error: Exception) -> None:
        self.investigation.btn_start.setText("▶  Start Scan")
        self.investigation.btn_start.setEnabled(True)
        self.top.btn_launch.setText("▶  Start Scan")
        self.top.btn_launch.setEnabled(True)
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        self.investigation.add_activity_row("\u26d4", f"Error: {error}", ts, "#ef4444")
        self.terminal.append_dispatch(f"error: {error}")


def build_main_window(controller: InvestigationController | None = None) -> MainWindow:
    """Construct the main window (works under QT_QPA_PLATFORM=offscreen for headless verification)."""
    return MainWindow(controller=controller)


def launch() -> int:
    import asyncio

    import qasync

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Bletiner AI")
    theme.apply_dark_theme(app)
    controller = InvestigationController()
    window = build_main_window(controller=controller)
    window.show()
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        loop.run_forever()
    return 0
