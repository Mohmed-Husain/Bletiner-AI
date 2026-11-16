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

from datetime import UTC, datetime

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bletiner.agent.events import AgentEvent, AgentState, EventType
from bletiner.ui import theme
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
        self.setFixedWidth(72)
        col = QVBoxLayout(self)
        col.setContentsMargins(8, 12, 8, 12)
        col.setSpacing(10)

        # Brand shield icon card
        brand_card = QFrame()
        brand_card.setObjectName("sidebarBrand")
        brand_card.setFixedSize(54, 54)
        b_layout = QVBoxLayout(brand_card)
        b_layout.setContentsMargins(0, 0, 0, 0)
        shield = QLabel("\U0001f6e1")
        shield.setAlignment(Qt.AlignmentFlag.AlignCenter)
        shield.setStyleSheet("font-size: 24px; color: #38bdf8; background: transparent;")
        b_layout.addWidget(shield)
        col.addWidget(brand_card, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(14)

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
            btn.setFixedSize(56, 52)
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
        docker_card = QFrame()
        docker_card.setObjectName("dockerBadge")
        docker_card.setFixedWidth(58)
        d_layout = QVBoxLayout(docker_card)
        d_layout.setContentsMargins(4, 6, 4, 6)
        d_layout.setSpacing(2)

        icon_box = QLabel("\U0001f4e6")
        icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_box.setStyleSheet("font-size: 14px; background: transparent; color: #34d399;")
        d_layout.addWidget(icon_box)

        dock_lbl = QLabel("\u25cf Docker:\nRunning")
        dock_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dock_lbl.setStyleSheet("font-size: 9px; font-weight: 700; color: #10b981; background: transparent;")
        d_layout.addWidget(dock_lbl)

        col.addWidget(docker_card, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(4)

        # Version string
        ver = QLabel("v0.3.0")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver.setStyleSheet("color: #4b5563; font-size: 10px; font-weight: 500; background: transparent;")
        col.addWidget(ver, 0, Qt.AlignmentFlag.AlignHCenter)

    def _on_tab_click(self, key: str) -> None:
        for k, btn in self.buttons.items():
            btn.setProperty("class", "navBtnActive" if k == key else "navBtn")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.tab_selected.emit(key)


class TopBar(QFrame):
    """Top Bar with brand title, subtitle, target/mode capsule, status badge, and controls."""

    minimize = Signal()
    maximize = Signal()
    close_ = Signal()

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
        capsule = QFrame()
        capsule.setObjectName("targetCapsule")
        capsule.setMinimumWidth(0)
        c_layout = QHBoxLayout(capsule)
        c_layout.setContentsMargins(12, 4, 12, 4)
        c_layout.setSpacing(10)
        c_text = QLabel(
            "<span style='color:#8892b0;'>\U0001f310 Target:</span> "
            "<span style='color:#f3f4f6; font-weight:600;'>example.com</span> "
            "<span style='color:#3a4256;'>&nbsp;|&nbsp;</span> "
            "<span style='color:#8892b0;'>\U0001f4da Mode:</span> "
            "<span style='color:#f3f4f6; font-weight:600;'>Deep</span>"
        )
        c_text.setStyleSheet("font-size: 11px; background: transparent;")
        c_text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        c_text.setMinimumWidth(0)
        c_layout.addWidget(c_text)
        row.addWidget(capsule)

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
        col.addWidget(self.target)

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


class TerminalCard(QFrame):
    """Card 2: Kali Linux Terminal with Connected badge, action controls, and rich PTY stream."""

    maximize_toggled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("terminalCard")
        self.setMinimumWidth(380)
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(10)

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
        self.btn_opts.setToolTip("Options")
        self.btn_opts.setProperty("class", "termBtn")
        header.addWidget(self.btn_opts)

        col.addLayout(header)

        # Terminal text display
        self.view = QTextEdit()
        self.view.setObjectName("terminalView")
        self.view.setReadOnly(True)
        self.view.setHtml(_TERMINAL_HTML)
        self.view.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        col.addWidget(self.view, 1)

        # Caption
        caption = QLabel("\u24d8  Agent and user share the sandbox workspace.")
        caption.setStyleSheet("color: #64748b; font-size: 11px; background: transparent;")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        caption.setMinimumWidth(0)
        col.addWidget(caption)

        self.bridge_token = new_terminal_token()

    def _copy_text(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.view.toPlainText())

    def append_dispatch(self, argv: str) -> None:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        self.view.append(
            f"<div style='color:#38bdf8; font-weight:600;'><br>--- [AGENT DISPATCH: {argv}] [{ts}] ---</div>"
        )


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

        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setIndentation(16)
        tree.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

        # Current investigation section
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
        tree.addTopLevelItem(current)

        # Previous sessions section
        previous = QTreeWidgetItem(["\U0001f552 Previous sessions"])
        previous.addChild(QTreeWidgetItem(["\U0001f4c4 Session 001         2026-11-10"]))
        previous.addChild(QTreeWidgetItem(["\U0001f4c4 Session 002         2026-11-09"]))
        previous.setExpanded(True)
        tree.addTopLevelItem(previous)

        col.addWidget(tree, 1)


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
        self.input.setPlaceholderText("Send instructions to steer the investigation...")
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

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Bletiner AI \u2014 Autonomous Security Workstation")
        self.resize(1360, 860)

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

        self.splitter.addWidget(self.investigation)
        self.splitter.addWidget(self.terminal)
        self.splitter.addWidget(self.sessions)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([320, 720, 280])
        deck.addWidget(self.splitter)
        v_main.addWidget(deck_holder, 1)

        # Bottom Steering Bar
        bottom_holder = QWidget()
        bl = QHBoxLayout(bottom_holder)
        bl.setContentsMargins(10, 0, 10, 10)
        self.bottom = BottomControlCard()
        self.bottom.steering_submitted.connect(self._on_steering)
        self.bottom.pause_clicked.connect(self._on_pause)
        self.bottom.stop_clicked.connect(self._on_stop)
        bl.addWidget(self.bottom)
        v_main.addWidget(bottom_holder)

        h_root.addWidget(main_content, 1)
        self.setCentralWidget(central)

        self._is_paused = False
        self._is_stopped = False
        self._terminal_expanded = False
        self._seed_demo()

    def _toggle_max(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _toggle_terminal_expand(self) -> None:
        self._terminal_expanded = not self._terminal_expanded
        self.investigation.setVisible(not self._terminal_expanded)
        self.sessions.setVisible(not self._terminal_expanded)

    def _seed_demo(self) -> None:
        """Seed the activity log with the exact checklist steps from the reference UI."""
        self.investigation.add_activity_row("\u2713", "Reconnaissance started", "12:04:12", "#10b981")
        self.investigation.add_activity_row("\u2713", "Ports discovered (3)", "12:04:28", "#10b981")
        self.investigation.add_activity_row("\u2713", "Service analysis", "12:04:47", "#10b981")
        self.investigation.add_activity_row("\u29bf", "Web enumeration", "12:05:10", "#38bdf8")
        self.investigation.add_activity_row("\u25cb", "Vulnerability analysis", "\u2014", "#64748b")
        self.investigation.add_activity_row("\u25cb", "Verification", "\u2014", "#64748b")
        self.investigation.set_badges(potential=2, confirmed=1)

    def _on_steering(self, text: str) -> None:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        self.investigation.add_activity_row("\u27a4", f"Steering: {text}", ts, "#38bdf8")
        self.terminal.append_dispatch(f"operator steering: {text}")

    def _on_pause(self) -> None:
        if not self._is_paused:
            self._is_paused = True
            self.top.set_state(AgentState.PAUSED)
            self.bottom.set_state("Agent paused", "yellow")
        else:
            self._is_paused = False
            self.top.set_state(AgentState.EXECUTING)
            self.bottom.set_state("Agent running", "green")

    def _on_stop(self) -> None:
        self._is_stopped = True
        self.top.set_state(AgentState.STOPPED)
        self.bottom.set_state("Agent stopped", "gray")


def build_main_window() -> MainWindow:
    """Construct the main window (works under QT_QPA_PLATFORM=offscreen for headless verification)."""
    return MainWindow()


def launch() -> int:
    import asyncio

    import qasync

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Bletiner AI")
    theme.apply_dark_theme(app)
    window = build_main_window()
    window.show()
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        loop.run_forever()
    return 0
