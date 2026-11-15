# mypy: ignore-errors
"""PySide6 desktop UI implementing DOC-05 (top bar, 3-card deck, bottom steering bar, dark theme).

Imported only when the app is actually launched (see ``app.run``), so the rest of the package stays
importable without a Qt toolchain. Presentation logic (feed formatting, badges, status text, terminal token)
comes from the headless, unit-tested view-models in ``bletiner.ui.viewmodels``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bletiner.agent.events import AgentEvent, AgentState, EventType
from bletiner.ui import theme
from bletiner.ui.viewmodels import FeedViewModel, new_terminal_token, status_label

_SAMPLE_TERMINAL = (
    "root@bletiner:/workspace# nmap -sV -sC -p- 198.51.100.10\n"
    "Starting Nmap 7.94 ( https://nmap.org )\n"
    "Nmap scan report for local-lab.test (198.51.100.10)\n"
    "Host is up (0.0012s latency).\n"
    "Not shown: 998 closed tcp ports\n"
    "PORT     STATE SERVICE VERSION\n"
    "80/tcp   open  http    Apache httpd 2.4.52\n"
    "443/tcp  open  https   OpenSSL 3.0.2 TLS\n"
    "|_http-title: Local Lab Target\n"
    "Service Info: OS: Linux; CPE: cpe:/o:linux\n"
    "\n"
    "--- [AGENT DISPATCH: nmap -sT -sV -p 80,443 local-lab.test] ---\n"
    "root@bletiner:/workspace# "
)


def _pill(text: str, variant: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(theme.pill_qss(variant))
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


def _card_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "cardHeader")
    return lbl


class TopBar(QFrame):
    minimize = Signal()
    maximize = Signal()
    close_ = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("topBar")
        self.setFixedHeight(48)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 6, 10, 6)
        shield = QLabel("\U0001f6e1")
        shield.setObjectName("brandShield")
        title = QLabel("Bletiner AI")
        title.setObjectName("brandTitle")
        row.addWidget(shield)
        row.addWidget(title)
        row.addStretch(1)
        self.status = _pill("● Investigating", "green")
        row.addWidget(self.status)
        row.addSpacing(12)
        for text, obj, sig in (
            ("—", "winMin", self.minimize),
            ("□", "winMax", self.maximize),
            ("✕", "winClose", self.close_),
        ):
            btn = QPushButton(text)
            btn.setObjectName(obj)
            btn.setProperty("class", "winBtn")
            btn.setStyleSheet(
                "QPushButton{background:transparent;border:none;color:#9ca3af;padding:2px 10px;}"
                "QPushButton:hover{color:#f3f4f6;background:#222630;border-radius:6px;}"
                + ("QPushButton:hover{background:#ef4444;color:#fff;}" if obj == "winClose" else "")
            )
            btn.clicked.connect(sig.emit)
            row.addWidget(btn)

    def set_state(self, state: AgentState) -> None:
        variant = {
            AgentState.PAUSED: "yellow",
            AgentState.STOPPED: "gray",
            AgentState.COMPLETED: "green",
        }.get(state, "green")
        self.status.setText(f"● {status_label(state)}")
        self.status.setStyleSheet(theme.pill_qss(variant))


class InvestigationCard(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("investigationCard")
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(8)
        col.addWidget(_card_header("INVESTIGATION"))

        target_row = QHBoxLayout()
        tlabel = QLabel("Target:")
        tlabel.setProperty("role", "muted")
        self.target = QLabel("https://local-lab.test")
        self.target.setObjectName("targetLink")
        target_row.addWidget(tlabel)
        target_row.addWidget(self.target, 1)
        col.addLayout(target_row)

        activity = QLabel("Activity log")
        activity.setProperty("role", "muted")
        col.addWidget(activity)
        self.feed = FeedViewModel(max_lines=500)
        self.feed_list = QListWidget()
        self.feed_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.feed_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.feed_list.setWordWrap(False)
        col.addWidget(self.feed_list, 1)

        summary = QLabel("Findings summary")
        summary.setProperty("role", "muted")
        col.addWidget(summary)
        badges = QHBoxLayout()
        self.potential = _pill("2 Potential", "yellow")
        self.confirmed = _pill("1 Confirmed", "red")
        badges.addWidget(self.potential)
        badges.addWidget(self.confirmed)
        badges.addStretch(1)
        col.addLayout(badges)

    def add_event(self, event: AgentEvent) -> None:
        line = self.feed.ingest(event)
        if line is not None:
            self.feed_list.addItem(QListWidgetItem(line))
            self.feed_list.scrollToBottom()

    def set_badges(self, potential: int, confirmed: int) -> None:
        self.potential.setText(f"{potential} Potential")
        self.confirmed.setText(f"{confirmed} Confirmed")


class TerminalCard(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("terminalCard")
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(8)
        header = QHBoxLayout()
        header.addWidget(_card_header(">_  KALI LINUX TERMINAL"))
        header.addStretch(1)
        self.conn = _pill("● Connected", "green")
        header.addWidget(self.conn)
        col.addLayout(header)

        self.view = QPlainTextEdit()
        self.view.setObjectName("terminalView")
        self.view.setReadOnly(True)
        self.view.setPlainText(_SAMPLE_TERMINAL)
        col.addWidget(self.view, 1)

        caption = QLabel("ℹ  Agent and user share the sandbox workspace.")
        caption.setProperty("role", "muted")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(caption)
        # The loopback xterm.js bridge token (DOC-05 section 5); kept even though this build renders a
        # read-only canvas rather than QWebEngineView, so the auth seam is already in place.
        self.bridge_token = new_terminal_token()

    def append_dispatch(self, argv: str) -> None:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        self.view.appendPlainText(f"\n--- [AGENT DISPATCH: {argv}] [{ts}] ---")


class SessionsFilesCard(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sessionsFilesCard")
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(8)
        col.addWidget(_card_header("SESSIONS & FILES"))

        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        current = QTreeWidgetItem(["▼ Current investigation"])
        for name in ("\U0001f4c4 report.md", "\U0001f310 report.html"):
            current.addChild(QTreeWidgetItem([name]))
        evidence = QTreeWidgetItem(["\U0001f4c1 evidence/"])
        for ev in ("ev_8f10_banner.txt", "ev_c491_sqli.json"):
            evidence.addChild(QTreeWidgetItem([ev]))
        logs = QTreeWidgetItem(["\U0001f4c1 logs/"])
        for lg in ("agent_runtime.log", "terminal_audit.log"):
            logs.addChild(QTreeWidgetItem([lg]))
        current.addChild(evidence)
        current.addChild(logs)
        tree.addTopLevelItem(current)
        current.setExpanded(True)
        evidence.setExpanded(True)
        logs.setExpanded(True)

        previous = QTreeWidgetItem(["▶ Previous sessions"])
        previous.addChild(QTreeWidgetItem(["• Session 001 - api.corp.internal (Completed | 4 findings)"]))
        previous.addChild(QTreeWidgetItem(["• Session 002 - staging.corp.internal (Stopped | 1 finding)"]))
        tree.addTopLevelItem(previous)
        col.addWidget(tree, 1)


class BottomControlCard(QFrame):
    steering_submitted = Signal(str)
    pause_clicked = Signal()
    stop_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("bottomControlCard")
        self.setFixedHeight(64)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(10)
        self.state = _pill("● Agent running", "green")
        row.addWidget(self.state)
        self.input = QLineEdit()
        self.input.setObjectName("steeringInput")
        self.input.setPlaceholderText("Send instructions to steer the investigation...")
        self.input.returnPressed.connect(self._emit_steering)
        row.addWidget(self.input, 1)
        pause = QPushButton("‖ Pause")
        pause.setObjectName("btnPause")
        pause.clicked.connect(self.pause_clicked.emit)
        stop = QPushButton("■ Stop")
        stop.setObjectName("btnStop")
        stop.clicked.connect(self.stop_clicked.emit)
        row.addWidget(pause)
        row.addWidget(stop)

    def _emit_steering(self) -> None:
        text = self.input.text().strip()
        if text:
            self.steering_submitted.emit(text)
            self.input.clear()

    def set_state(self, text: str, variant: str) -> None:
        self.state.setText(f"● {text}")
        self.state.setStyleSheet(theme.pill_qss(variant))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Bletiner AI — Autonomous Security Workstation")
        self.resize(1360, 860)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.top = TopBar()
        self.top.minimize.connect(self.showMinimized)
        self.top.maximize.connect(self._toggle_max)
        self.top.close_.connect(self.close)
        root.addWidget(self.top)

        deck_holder = QWidget()
        deck = QHBoxLayout(deck_holder)
        deck.setContentsMargins(8, 8, 8, 8)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.investigation = InvestigationCard()
        self.terminal = TerminalCard()
        self.sessions = SessionsFilesCard()
        splitter.addWidget(self.investigation)
        splitter.addWidget(self.terminal)
        splitter.addWidget(self.sessions)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([320, 880, 300])
        deck.addWidget(splitter)
        root.addWidget(deck_holder, 1)

        bottom_holder = QWidget()
        bl = QHBoxLayout(bottom_holder)
        bl.setContentsMargins(8, 0, 8, 6)
        self.bottom = BottomControlCard()
        self.bottom.steering_submitted.connect(self._on_steering)
        self.bottom.pause_clicked.connect(self._on_pause)
        self.bottom.stop_clicked.connect(self._on_stop)
        bl.addWidget(self.bottom)
        root.addWidget(bottom_holder)

        footer = QWidget()
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(16, 2, 16, 8)
        left = QLabel("Conceptual layout — live demo build.")
        left.setObjectName("footerLeft")
        right = QLabel("CPU: 12%  |  RAM: 2.1GB  |  Model: Locked")
        right.setObjectName("footerRight")
        fl.addWidget(left)
        fl.addStretch(1)
        fl.addWidget(right)
        root.addWidget(footer)

        self.setCentralWidget(central)
        self._seed_demo()

    def _toggle_max(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _seed_demo(self) -> None:
        def ev(t: EventType, **p: object) -> AgentEvent:
            return AgentEvent(t, payload=dict(p))

        for e in (
            ev(EventType.STATE_CHANGED, state="PLANNING"),
            ev(EventType.TOOL_STARTED, tool="nmap_service", argv=["nmap", "-sT", "-sV", "local-lab.test"]),
            ev(EventType.TOOL_COMPLETED, tool="nmap_service", exit_code=0),
            ev(EventType.FINDING, severity="MEDIUM", title="Outdated OpenSSL", asset="local-lab.test"),
            ev(EventType.TOOL_STARTED, tool="nuclei_cve", argv=["nuclei", "-target", "local-lab.test"]),
            ev(
                EventType.FINDING,
                severity="CRITICAL",
                title="Unauth RCE on debug endpoint",
                asset="local-lab.test",
            ),
            ev(EventType.POLICY_BLOCK, tool="masscan", tier=4, error="Tier 4 blocked"),
        ):
            self.investigation.add_event(e)
        self.investigation.set_badges(potential=2, confirmed=1)

    def _on_steering(self, text: str) -> None:
        self.investigation.add_event(AgentEvent(EventType.STEERING_ACCEPTED, payload={"instruction": text}))

    def _on_pause(self) -> None:
        self.top.set_state(AgentState.PAUSED)
        self.bottom.set_state("Agent paused", "yellow")

    def _on_stop(self) -> None:
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
