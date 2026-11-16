"""Kali Linux terminal card: the real interactive sandbox PTY.

The card owns a TerminalBridge (Docker exec PTY inside the running Kali container) plus an
AnsiScreen renderer and paints the live terminal grid into a read-only QTextEdit. Keystrokes
typed in the terminal are forwarded to the PTY; resizing the card resizes the PTY. When the PTY
cannot start (Docker offline, container missing, pyte missing) the card switches to a clearly
labelled command console that runs commands through controller.execute_terminal_command - real
output only, never simulated content.

Agent/tool activity from the investigation loop is shown in a compact AGENT ACTIVITY stream above
the shell so informational blocks never corrupt the interactive terminal buffer.

Public API (see .handoff/docs/INTERFACES.md):
    maximize_toggled = Signal(); command_entered = Signal(str)
    set_connected(connected: bool, detail: str = '')
    set_container(container)
    focus_terminal()
    append_tool_output(tool_name, argv, output)
    append_dispatch(argv)
    append_system(text)
    clear()
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import os
import threading
from datetime import UTC, datetime
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from bletiner.ui import theme
from bletiner.ui.terminal_bridge import (
    AnsiScreen,
    DockerPtyTransport,
    PtyUnavailable,
    RendererUnavailable,
    TerminalBridge,
    encode_paste,
    key_to_bytes,
)

AUTOCONNECT_ENV = "BLETINER_TERMINAL_AUTOCONNECT"

#: CSS pinned on the rendered pre block so the QTextEdit character grid matches the PTY grid.
PRE_STYLE = (
    "margin:0; white-space:pre; "
    "font-family:'Cascadia Mono','JetBrains Mono','Consolas','Courier New',monospace; font-size:12px;"
)
MONO_STYLE = (
    "font-family:'Cascadia Mono','JetBrains Mono','Consolas','Courier New',monospace; font-size:11px;"
)

#: Shell setup sent right after the PTY attaches: a deterministic, colourised prompt. Bash
#: interprets the backslash escapes inside PS1; the single quotes keep them intact through the TTY.
PROMPT_SETUP = (
    b"export PS1='\\[\\e[1;32m\\]bletiner@kali\\[\\e[0m\\]:\\[\\e[1;34m\\]/workspace\\[\\e[0m\\]\\$ '\r"
)
CLEAR_SEQUENCE = b"\x0c"  # Ctrl-L: readline clears the screen and redraws a clean prompt

CARD_QSS = """
QFrame#agentStream {
    background-color: #0d0f16;
    border: 1px solid #1a1e2b;
    border-radius: 8px;
}
QTextEdit#agentStreamView {
    background-color: transparent;
    border: none;
    color: #cbd5e1;
    font-family: "Cascadia Mono", "JetBrains Mono", "Consolas", monospace;
    font-size: 11px;
    padding: 4px 6px;
}
QPushButton.termReconnect {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 6px;
    color: #38bdf8;
    font-size: 11px;
    font-weight: 600;
    padding: 3px 8px;
}
QPushButton.termReconnect:hover {
    background-color: #0284c7;
    border-color: #38bdf8;
    color: #ffffff;
}
"""

_QT_KEY_NAMES: dict[int, str] = {
    Qt.Key.Key_Return: "Enter",
    Qt.Key.Key_Enter: "Enter",
    Qt.Key.Key_Backspace: "Backspace",
    Qt.Key.Key_Tab: "Tab",
    Qt.Key.Key_Backtab: "Shift+Tab",
    Qt.Key.Key_Escape: "Escape",
    Qt.Key.Key_Up: "Up",
    Qt.Key.Key_Down: "Down",
    Qt.Key.Key_Left: "Left",
    Qt.Key.Key_Right: "Right",
    Qt.Key.Key_Home: "Home",
    Qt.Key.Key_End: "End",
    Qt.Key.Key_PageUp: "PageUp",
    Qt.Key.Key_PageDown: "PageDown",
    Qt.Key.Key_Insert: "Insert",
    Qt.Key.Key_Delete: "Delete",
    Qt.Key.Key_F1: "F1",
    Qt.Key.Key_F2: "F2",
    Qt.Key.Key_F3: "F3",
    Qt.Key.Key_F4: "F4",
    Qt.Key.Key_F5: "F5",
    Qt.Key.Key_F6: "F6",
    Qt.Key.Key_F7: "F7",
    Qt.Key.Key_F8: "F8",
    Qt.Key.Key_F9: "F9",
    Qt.Key.Key_F10: "F10",
    Qt.Key.Key_F11: "F11",
    Qt.Key.Key_F12: "F12",
}


def _autoconnect_enabled() -> bool:
    value = os.environ.get(AUTOCONNECT_ENV, "1").strip().lower()
    return value not in ("0", "false", "no", "off")


def _escape_multiline(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")


class _TerminalView(QTextEdit):
    """Read-only terminal surface that forwards every keystroke to the owning card."""

    def __init__(self, card: TerminalCard) -> None:
        super().__init__()
        self._card = card
        self.setObjectName("terminalView")
        self.setReadOnly(True)
        self.setAcceptRichText(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.document().setDocumentMargin(0)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.setCursor(Qt.CursorShape.IBeamCursor)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._card._handle_view_key(event):
            event.accept()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event: Any) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)


class _HistoryLineEdit(QLineEdit):
    """Command input with Up/Down shell-history navigation."""

    history_navigated = Signal(int)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Up and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.history_navigated.emit(-1)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Down and not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.history_navigated.emit(1)
            event.accept()
            return
        super().keyPressEvent(event)


class TerminalCard(QFrame):
    """Kali Linux terminal card: live sandbox PTY with an honest command-console fallback."""

    maximize_toggled = Signal()
    command_entered = Signal(str)

    _fallback_done = Signal(str, int, str, str)
    _fallback_failed = Signal(str, str)

    def __init__(self, controller: Any = None, parent: Any = None, *, auto_connect: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("terminalCard")
        self.setMinimumWidth(360)
        self.setStyleSheet(CARD_QSS)

        self._controller = controller
        self._container: Any = None
        self._bridge: TerminalBridge | None = None
        self._auto_connect = auto_connect
        self._pty_connected = False
        self._attempted = False
        self._readiness_told = False
        self._fallback_active = False
        self._fallback_reason = ""
        self._pending = bytearray()
        self._last_rendered = ""
        self._last_size = (0, 0)
        self._agent_has_content = False
        self._history: list[str] = []
        self._history_index = -1
        self._readiness_timer: QTimer | None = None
        self._readiness_ticks = 0
        self._connection_banner = ""

        self._renderer: AnsiScreen | None
        self._renderer_error = ""
        try:
            self._renderer = AnsiScreen()
        except RendererUnavailable as exc:
            self._renderer = None
            self._renderer_error = str(exc)

        self._build_ui()

        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(25)
        self._repaint_timer.timeout.connect(self._flush_repaint)

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(120)
        self._resize_timer.timeout.connect(self._sync_size)

        self._fallback_done.connect(self._display_fallback_result)
        self._fallback_failed.connect(self._on_fallback_failed)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

        self._show_initial_state()

    # -- construction -------------------------------------------------------------
    def _build_ui(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(4)
        title = QLabel(">_  KALI LINUX TERMINAL")
        title.setProperty("role", "cardHeader")
        header.addWidget(title)
        header.addStretch(1)

        self.btn_reconnect = QPushButton("Reconnect")
        self.btn_reconnect.setProperty("class", "termReconnect")
        self.btn_reconnect.setToolTip("Attach a new interactive PTY inside the Kali sandbox")
        self.btn_reconnect.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reconnect.clicked.connect(self.reconnect)
        self.btn_reconnect.hide()
        header.addWidget(self.btn_reconnect)

        self.conn = QLabel("● Disconnected")
        self.conn.setStyleSheet(theme.pill_qss("gray"))
        self.conn.setToolTip("Interactive PTY status")
        header.addWidget(self.conn)
        header.addSpacing(4)

        self.btn_copy = self._icon_button("⧉", "Copy terminal output")
        self.btn_copy.clicked.connect(self._copy_selection)
        header.addWidget(self.btn_copy)

        self.btn_expand = self._icon_button("⤢", "Expand terminal (toggle)")
        self.btn_expand.clicked.connect(self.maximize_toggled.emit)
        header.addWidget(self.btn_expand)

        self.btn_opts = self._icon_button("⋮", "Terminal options")
        self.btn_opts.clicked.connect(self._show_options_menu)
        header.addWidget(self.btn_opts)

        col.addLayout(header)

        self.status_detail = QLabel("")
        self.status_detail.setWordWrap(True)
        self.status_detail.setStyleSheet("color: #f59e0b; font-size: 11px; background: transparent;")
        self.status_detail.hide()
        col.addWidget(self.status_detail)

        self.agent_frame = QFrame()
        self.agent_frame.setObjectName("agentStream")
        agent_layout = QVBoxLayout(self.agent_frame)
        agent_layout.setContentsMargins(8, 6, 8, 6)
        agent_layout.setSpacing(2)
        agent_header = QLabel("AGENT ACTIVITY")
        agent_header.setProperty("role", "sectionHeader")
        agent_layout.addWidget(agent_header)
        self.agent_log = QTextEdit()
        self.agent_log.setObjectName("agentStreamView")
        self.agent_log.setReadOnly(True)
        self.agent_log.setMinimumHeight(58)
        self.agent_log.setMaximumHeight(112)
        self.agent_log.document().setDocumentMargin(0)
        agent_layout.addWidget(self.agent_log)
        self.agent_frame.hide()
        col.addWidget(self.agent_frame)

        self.view = _TerminalView(self)
        col.addWidget(self.view, 1)

        self.cmd_bar = QFrame()
        self.cmd_bar.setObjectName("terminalInputBar")
        bar_layout = QHBoxLayout(self.cmd_bar)
        bar_layout.setContentsMargins(6, 4, 6, 4)
        bar_layout.setSpacing(6)

        self.prompt_lbl = QLabel("❯")
        self.prompt_lbl.setStyleSheet(
            "font-family:'Cascadia Mono','JetBrains Mono',monospace; font-size:13px; color:#10b981; "
            "background: transparent; padding: 0 2px;"
        )
        bar_layout.addWidget(self.prompt_lbl)

        self.cmd_input = _HistoryLineEdit()
        self.cmd_input.setObjectName("terminalCmdInput")
        self.cmd_input.setPlaceholderText("Run a command in the Kali sandbox and press Enter...")
        self.cmd_input.returnPressed.connect(self._on_submit)
        self.cmd_input.history_navigated.connect(self._on_history_navigate)
        bar_layout.addWidget(self.cmd_input, 1)

        self.btn_run = QPushButton("↵ Run")
        self.btn_run.setObjectName("btnRunCmd")
        self.btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_run.setToolTip("Run the command in the Kali sandbox")
        self.btn_run.clicked.connect(self._on_submit)
        bar_layout.addWidget(self.btn_run)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setObjectName("btnClearTerm")
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.setToolTip("Clear the terminal buffer (sends Ctrl-L to the live shell)")
        self.btn_clear.clicked.connect(self.clear)
        bar_layout.addWidget(self.btn_clear)

        col.addWidget(self.cmd_bar)

        caption = QLabel("ⓘ  Agent and user share the sandbox workspace.")
        caption.setStyleSheet("color: #64748b; font-size: 11px; background: transparent;")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(caption)

    def _icon_button(self, text: str, tooltip: str) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("class", "termBtn")
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedWidth(28)
        return button

    # -- public API ---------------------------------------------------------------
    @property
    def is_pty_connected(self) -> bool:
        return self._pty_connected

    def set_connected(self, connected: bool, detail: str = "") -> None:
        """Reflect readiness transitions; connect the PTY when the sandbox is reported ready."""
        self._readiness_told = True
        self._stop_readiness_timer()
        if connected:
            if self._bridge is not None and self._bridge.is_connected:
                self._set_pill("connected", detail)
                return
            self._start_pty(detail)
        else:
            if self._bridge is not None and self._bridge.is_connected:
                self._stop_bridge()
            self._enter_fallback(detail or "sandbox is not reported ready yet")

    def set_container(self, container: Any) -> None:
        """Attach the exact Docker container the PTY should exec into."""
        self._container = container

    def focus_terminal(self) -> None:
        if self._pty_connected:
            self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self.cmd_input.setFocus(Qt.FocusReason.OtherFocusReason)

    def append_tool_output(self, tool_name: str, argv: list[str], output: str) -> None:
        """Render an informational agent tool block without touching the interactive shell buffer."""
        argv_str = (
            " ".join(str(item) for item in argv) if isinstance(argv, (list, tuple)) else str(argv or "")
        )
        header = f"AGENT TOOL {tool_name}" + (f" {argv_str}" if argv_str else "")
        body = str(output or "")
        truncated = len(body) > 6000
        if truncated:
            body = body[:6000].rstrip()
        parts = [
            f'<div style="{MONO_STYLE} color:#a78bfa; font-weight:700; margin-top:6px;">▸ '
            f"{html.escape(header)}</div>"
        ]
        if body:
            parts.append(f'<pre style="{MONO_STYLE} margin:2px 0 0 0;">{html.escape(body)}</pre>')
        else:
            parts.append(f'<div style="{MONO_STYLE} color:#64748b;">(no output)</div>')
        if truncated:
            parts.append(
                f'<div style="{MONO_STYLE} color:#64748b;">[output truncated for display; '
                "full output is stored with the investigation evidence]</div>"
            )
        self._append_stream("".join(parts))

    def append_dispatch(self, argv: str) -> None:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        self._append_stream(
            f'<div style="{MONO_STYLE} color:#38bdf8; font-weight:700; margin-top:6px;">'
            f"→ AGENT DISPATCH: {html.escape(str(argv))} "
            f'<span style="color:#64748b; font-weight:400;">[{ts} UTC]</span></div>'
        )

    def append_system(self, text: str) -> None:
        if not str(text or "").strip():
            return
        self._append_stream(
            f'<div style="{MONO_STYLE} color:#94a3b8; margin-top:2px;">ⓘ {_escape_multiline(str(text))}</div>'
        )

    def append_user_command(self, cmd: str) -> None:
        """Echo an operator command into the informational stream (used by the console fallback)."""
        self._append_stream(self._prompt_fragment(cmd))

    def append_command_result(self, exit_code: int, stdout: str, stderr: str) -> None:
        """Render the result of a non-interactive command (console fallback / external wiring)."""
        self._append_stream(self._result_fragment(exit_code, stdout, stderr))

    def clear(self) -> None:
        self.agent_log.clear()
        self._agent_has_content = False
        self.agent_frame.hide()
        if self._pty_connected and self._bridge is not None:
            if self._renderer is not None:
                self._renderer.clear()
            self._last_rendered = ""
            self.view.setHtml("")
            self._bridge.send(b"\x0c")
            return
        if self._fallback_active:
            self._enter_fallback(self._fallback_reason or "sandbox is not ready")
        else:
            self._show_initial_state()

    def reconnect(self) -> None:
        self._attempted = True
        self._stop_bridge()
        self._start_pty("manual reconnect")

    def shutdown(self) -> None:
        """Stop timers and the PTY reader thread (safe to call from aboutToQuit/close)."""
        with contextlib.suppress(RuntimeError):
            self._repaint_timer.stop()
        with contextlib.suppress(RuntimeError):
            self._resize_timer.stop()
        self._stop_readiness_timer()
        self._stop_bridge()

    # -- Qt events ----------------------------------------------------------------
    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if not self._attempted and not self._readiness_told and self._auto_connect and _autoconnect_enabled():
            if self._controller is not None:
                self._wait_for_sandbox_readiness()
            else:
                self._start_pty()
        QTimer.singleShot(0, self._sync_size)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._resize_timer.start()

    # -- PTY lifecycle ------------------------------------------------------------
    def _start_pty(self, detail: str = "") -> None:
        self._attempted = True
        if self._renderer is None:
            self._enter_fallback(self._renderer_error or "ANSI renderer unavailable")
            return
        if self._bridge is not None and self._bridge.is_connected:
            return
        self._stop_bridge()
        bridge = TerminalBridge(self._make_transport)
        bridge.data_received.connect(self._on_pty_data)
        bridge.state_changed.connect(self._on_pty_state)
        self._bridge = bridge
        self._pty_connected = False
        self._last_rendered = ""
        self._renderer.clear()
        self._set_pill("connecting", detail or "connecting to the Kali sandbox PTY")
        self._set_status("Connecting to the interactive Kali PTY...", tone="muted")
        self.view.setHtml(self._neutral_document("Connecting to the interactive Kali sandbox PTY..."))
        bridge.start()

    def _wait_for_sandbox_readiness(self) -> None:
        """Ask the controller whether the sandbox is ready before opening the PTY.

        The readiness probe runs in the background; when it has not completed yet the card stays
        in an amber 'Connecting' state and re-checks every 250 ms instead of guessing.
        """
        self._readiness_ticks = 0
        self._set_pill("connecting", "waiting for the sandbox readiness check")
        self._set_status(
            "Waiting for the sandbox readiness check before attaching the interactive Kali PTY...",
            tone="muted",
        )
        self.view.setHtml(
            self._neutral_document(
                "Waiting for the sandbox readiness check...\n"
                "The interactive shell attaches automatically once Docker and the my-kali container are verified."
            )
        )
        if not self._check_controller_readiness():
            if self._readiness_timer is None:
                self._readiness_timer = QTimer(self)
                self._readiness_timer.setInterval(250)
                self._readiness_timer.timeout.connect(self._poll_controller_readiness)
            self._readiness_timer.start()

    def _poll_controller_readiness(self) -> None:
        if self._attempted or self._readiness_told:
            self._stop_readiness_timer()
            return
        self._readiness_ticks += 1
        if self._check_controller_readiness():
            self._stop_readiness_timer()
            return
        if self._readiness_ticks >= 24:  # ~6s: readiness probe never completed
            self._stop_readiness_timer()
            self._enter_fallback(
                "The sandbox readiness check did not complete. Start Docker Desktop and the my-kali container, "
                "then press Reconnect."
            )

    def _check_controller_readiness(self) -> bool:
        """Start the PTY when readiness is positive; enter the labelled console when it is not.

        Returns True when a terminal state (started / fallback) was reached, False while waiting.
        """
        controller = self._controller
        if controller is None:
            self._start_pty()
            return True
        getter = getattr(controller, "readiness", None)
        if not callable(getter):
            self._start_pty()
            return True
        try:
            snapshot = getter()
        except Exception as exc:
            self._enter_fallback(f"Could not read sandbox readiness: {exc}")
            return True
        probing = bool(getattr(snapshot, "probe_complete", True)) is False
        if probing:
            return False
        if bool(getattr(snapshot, "local_ready", False)):
            container = str(getattr(snapshot, "kali_container", "") or "")
            self._start_pty(f"Kali sandbox '{container}' is ready." if container else "")
            return True
        notes = tuple(getattr(snapshot, "notes", ()) or ())
        reason = str(notes[0]) if notes else "The Kali sandbox is not ready."
        self._enter_fallback(reason)
        return True

    def _stop_readiness_timer(self) -> None:
        timer = getattr(self, "_readiness_timer", None)
        if timer is not None:
            timer.stop()

    def _make_transport(self) -> DockerPtyTransport:
        container = self._container
        if container is None and self._controller is not None:
            getter = getattr(self._controller, "kali_container", None)
            if callable(getter):
                try:
                    container = getter()
                except Exception as exc:
                    raise PtyUnavailable(f"Could not query the Kali sandbox: {exc}") from exc
                if container is None:
                    reason = "Kali sandbox container is not running."
                    readiness_getter = getattr(self._controller, "readiness", None)
                    if callable(readiness_getter):
                        with contextlib.suppress(Exception):
                            snapshot = readiness_getter()
                            if not bool(getattr(snapshot, "docker_running", True)):
                                reason = "Docker Desktop is not running."
                    raise PtyUnavailable(
                        f"{reason} Start Docker and the my-kali container, then press Reconnect."
                    )
        return DockerPtyTransport(container=container)

    def _stop_bridge(self) -> None:
        bridge = self._bridge
        self._bridge = None
        self._pty_connected = False
        if bridge is not None:
            with contextlib.suppress(Exception):
                bridge.stop(join_timeout=0.3)

    def _on_pty_state(self, connected: bool, detail: str) -> None:
        if connected:
            self._pty_connected = True
            self._fallback_active = False
            self._connection_banner = self._banner_from_detail(detail)
            self._set_pill("connected", detail)
            self._set_status("", tone="muted")
            self.agent_frame.setVisible(self._agent_has_content)
            self._last_rendered = ""
            self.view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            if self._renderer is not None:
                self.view.setHtml(self._renderer.render_html(pre_style=PRE_STYLE))
            self.cmd_input.setPlaceholderText(
                "Type a command for the interactive Kali shell and press Enter..."
            )
            QTimer.singleShot(0, self._after_connect)
        else:
            was_connected = self._pty_connected
            self._pty_connected = False
            note = "The interactive shell exited." if was_connected else ""
            self._enter_fallback(detail or "PTY unavailable", note=note)

    def _after_connect(self) -> None:
        self._sync_size(force=True)
        self._refresh_screen(force=True)
        bridge = self._bridge
        if bridge is not None:
            # Start from a clean, deterministic shell instead of the Kali login banner.
            bridge.send(PROMPT_SETUP)
            QTimer.singleShot(450, self._reset_shell_screen)

    def _reset_shell_screen(self) -> None:
        """Drop the login banner from the viewport and let readline redraw a clean prompt."""
        bridge = self._bridge
        if bridge is None or not self._pty_connected:
            return
        if self._renderer is not None:
            self._renderer.clear()
        self._last_rendered = ""
        self.view.setHtml("")
        bridge.send(CLEAR_SEQUENCE)

    @staticmethod
    def _banner_from_detail(detail: str) -> str:
        user, host, path = "bletiner", "my-kali", "/workspace"
        text = str(detail or "").strip()
        user_host, _, remainder = text.partition(" on ")
        if "@" in user_host:
            user, host = user_host.split("@", 1)
        if remainder.strip():
            path = remainder.strip()
        return f"Connected to Kali sandbox container '{host}' ({user}@{path})"

    def _on_pty_data(self, chunk: Any) -> None:
        if isinstance(chunk, (bytes, bytearray, memoryview)):
            self._pending.extend(bytes(chunk))
        if not self._repaint_timer.isActive():
            self._repaint_timer.start()

    def _flush_repaint(self) -> None:
        if not self._pending:
            return
        data = bytes(self._pending)
        self._pending.clear()
        if self._renderer is not None:
            self._renderer.feed(data)
            self._refresh_screen()
        bridge = self._bridge
        transport = getattr(bridge, "transport", None) if bridge is not None else None
        if transport is not None:
            for message in transport.take_errors():
                self.append_system(message)

    def _sync_size(self, force: bool = False) -> None:
        if self._renderer is None:
            return
        metrics = self.view.fontMetrics()
        char_width = max(1, metrics.horizontalAdvance("M"))
        line_height = max(1, metrics.lineSpacing())
        viewport = self.view.viewport().size()
        columns = max(24, int((viewport.width() - 6) / char_width))
        rows = max(8, int((viewport.height() - 6) / line_height))
        if not force and (columns, rows) == self._last_size:
            return
        self._last_size = (columns, rows)
        self._renderer.resize(columns, rows)
        if self._bridge is not None:
            self._bridge.resize(rows, columns)
        self._last_rendered = ""
        self._refresh_screen(force=True)

    def _refresh_screen(self, force: bool = False) -> None:
        if not self._pty_connected or self._renderer is None:
            return
        document = self._renderer.render_html(pre_style=PRE_STYLE)
        if self._connection_banner:
            document = (
                f'<div style="{MONO_STYLE} color:#22d3ee; font-weight:700; margin-bottom:4px;">'
                f"&#10003; {html.escape(self._connection_banner)}</div>" + document
            )
        if not force and document == self._last_rendered:
            return
        self._last_rendered = document
        scrollbar = self.view.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        self.view.setHtml(document)
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    # -- console fallback ---------------------------------------------------------
    def _enter_fallback(self, reason: str, *, note: str = "") -> None:
        self._pty_connected = False
        self._fallback_active = True
        self._fallback_reason = reason
        self._connection_banner = ""
        self._set_pill("error", reason)
        self.status_detail.setText(f"Command console (PTY unavailable): {reason}")
        self.status_detail.setStyleSheet("color: #f59e0b; font-size: 11px; background: transparent;")
        self.status_detail.show()
        self.agent_frame.hide()
        self.view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.view.setHtml(self._fallback_document(reason, note))
        self._last_rendered = ""
        self.cmd_input.setPlaceholderText("Run a command in the Kali sandbox (non-interactive console)...")

    def _fallback_document(self, reason: str, note: str) -> str:
        note_html = (
            f'<div style="{MONO_STYLE} color:#94a3b8; margin-top:2px;">{html.escape(note)}</div>'
            if note
            else ""
        )
        return (
            f'<div style="{MONO_STYLE} color:#f59e0b; font-weight:700;">PTY unavailable</div>'
            f'<div style="{MONO_STYLE} color:#94a3b8; margin-top:2px;">{html.escape(reason)}</div>'
            f"{note_html}"
            f'<div style="{MONO_STYLE} color:#38bdf8; font-weight:700; margin-top:8px;">Command console mode</div>'
            f'<div style="{MONO_STYLE} color:#94a3b8; margin-top:2px;">Commands run non-interactively as '
            "bletiner in /workspace of the Kali container through the investigation controller. "
            "Start Docker Desktop and the my-kali container, then press Reconnect for the full "
            "interactive shell.</div>"
        )

    def _neutral_document(self, text: str) -> str:
        return f'<div style="{MONO_STYLE} color:#64748b;">{_escape_multiline(text)}</div>'

    def _show_initial_state(self) -> None:
        self._fallback_active = False
        self._set_pill("offline")
        self.status_detail.hide()
        self.agent_frame.hide()
        self.view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.view.setHtml(
            self._neutral_document(
                "Interactive Kali PTY is not attached yet.\n"
                "It starts when this card is first shown or when the workspace reports the sandbox ready. "
                "Press Reconnect to attach it now."
            )
        )
        self.cmd_input.setPlaceholderText("Run a command in the Kali sandbox and press Enter...")

    def _on_submit(self) -> None:
        text = self.cmd_input.text()
        if not text.strip():
            return
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        self._history_index = -1
        self.cmd_input.clear()
        self.command_entered.emit(text)
        if self._pty_connected and self._bridge is not None:
            for line in text.replace("\r\n", "\n").split("\n"):
                if not self._bridge.send(line.encode("utf-8") + b"\r"):
                    break
            return
        self._run_fallback_command(text)

    def _on_history_navigate(self, direction: int) -> None:
        if not self._history:
            return
        if direction == -1:
            if self._history_index == -1:
                self._history_index = len(self._history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
        elif self._history_index != -1:
            if self._history_index < len(self._history) - 1:
                self._history_index += 1
            else:
                self._history_index = -1
                self.cmd_input.clear()
                return
        if 0 <= self._history_index < len(self._history):
            self.cmd_input.setText(self._history[self._history_index])
            self.cmd_input.setCursorPosition(len(self.cmd_input.text()))

    def _run_fallback_command(self, cmd: str) -> None:
        controller = self._controller
        if controller is None:
            self._append_stream(
                f'<div style="{MONO_STYLE} color:#ef4444; margin-top:4px;">No investigation controller is '
                "attached, so the command cannot run. Start the application normally and retry.</div>"
            )
            return
        executor = getattr(controller, "execute_terminal_command", None)
        if not callable(executor):
            self._append_stream(
                f'<div style="{MONO_STYLE} color:#ef4444; margin-top:4px;">The attached controller cannot '
                "execute shell commands.</div>"
            )
            return
        self._append_stream(self._prompt_fragment(cmd))
        self._set_busy(True)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            task = loop.create_task(executor(cmd))
            task.add_done_callback(lambda finished, command=cmd: self._fallback_task_done(command, finished))
            return
        thread = threading.Thread(target=self._fallback_thread_main, args=(cmd, executor), daemon=True)
        thread.start()

    def _fallback_thread_main(self, cmd: str, executor: Any) -> None:
        try:
            result = asyncio.run(executor(cmd))
        except Exception as exc:
            self._fallback_failed.emit(cmd, f"Execution failed: {exc}")
            return
        exit_code, stdout, stderr = result
        self._fallback_done.emit(cmd, int(exit_code), str(stdout or ""), str(stderr or ""))

    def _fallback_task_done(self, cmd: str, task: Any) -> None:
        self._set_busy(False)
        if task.cancelled():
            self._on_fallback_failed(cmd, "Command cancelled.")
            return
        exception = task.exception()
        if exception is not None:
            self._on_fallback_failed(cmd, f"Execution failed: {exception}")
            return
        exit_code, stdout, stderr = task.result()
        self._display_fallback_result(cmd, int(exit_code), str(stdout or ""), str(stderr or ""))

    def _display_fallback_result(self, cmd: str, exit_code: int, stdout: str, stderr: str) -> None:
        self._set_busy(False)
        if self._pty_connected:
            return
        self._append_stream(self._result_fragment(exit_code, stdout, stderr))

    def _on_fallback_failed(self, cmd: str, message: str) -> None:
        self._set_busy(False)
        if self._pty_connected:
            return
        self._append_stream(
            f'<div style="{MONO_STYLE} color:#ef4444; margin-top:4px;">{html.escape(message)}</div>'
        )

    def _set_busy(self, busy: bool) -> None:
        self.cmd_input.setEnabled(not busy)
        self.btn_run.setEnabled(not busy)
        self.btn_run.setText("..." if busy else "↵ Run")

    def _prompt_fragment(self, cmd: str) -> str:
        return (
            f'<div style="{MONO_STYLE} margin-top:6px;">'
            '<span style="color:#10b981; font-weight:700;">bletiner@kali</span>'
            '<span style="color:#94a3b8;">:</span>'
            '<span style="color:#38bdf8; font-weight:700;">/workspace</span>'
            f'<span style="color:#94a3b8;">$ </span><span style="color:#f8fafc;">'
            f"{html.escape(str(cmd))}</span></div>"
        )

    def _result_fragment(self, exit_code: int, stdout: str, stderr: str) -> str:
        parts: list[str] = []
        out = str(stdout or "").rstrip("\n")
        err = str(stderr or "").rstrip("\n")
        if out:
            parts.append(f'<pre style="{MONO_STYLE} margin:2px 0 0 0;">{html.escape(out)}</pre>')
        if err:
            parts.append(
                f'<pre style="{MONO_STYLE} margin:2px 0 0 0; color:#f87171;">{html.escape(err)}</pre>'
            )
        if exit_code != 0:
            parts.append(
                f'<div style="{MONO_STYLE} color:#f87171;">[process exited with code {exit_code}]</div>'
            )
        if not parts:
            parts.append(f'<div style="{MONO_STYLE} color:#64748b;">(no output)</div>')
        return "".join(parts)

    # -- stream / status helpers --------------------------------------------------
    def _append_stream(self, fragment: str) -> None:
        if self._pty_connected:
            self.agent_log.append(fragment)
            scrollbar = self.agent_log.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            self._agent_has_content = True
            self.agent_frame.show()
            return
        self.view.append(fragment)
        self._scroll_view_bottom()

    def _scroll_view_bottom(self) -> None:
        scrollbar = self.view.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    def _set_pill(self, state: str, tooltip: str = "") -> None:
        variants = {"connected": "green", "connecting": "yellow", "offline": "gray", "error": "red"}
        if state == "connected":
            label = "● Connected"
        elif state == "connecting":
            label = "● Connecting…"
        elif state == "error" and tooltip:
            reason = str(tooltip).strip()
            if len(reason) > 52:
                reason = reason[:49].rstrip() + "…"
            label = f"● Disconnected — {reason}"
        else:
            label = "● Disconnected"
        self.conn.setText(label)
        self.conn.setStyleSheet(theme.pill_qss(variants.get(state, "gray")))
        self.conn.setToolTip(tooltip or "Interactive PTY status")
        self.btn_reconnect.setVisible(state != "connected")

    def _set_status(self, text: str, *, tone: str = "muted") -> None:
        if not text:
            self.status_detail.hide()
            return
        color = "#f59e0b" if tone == "warn" else "#94a3b8"
        self.status_detail.setStyleSheet(f"color: {color}; font-size: 11px; background: transparent;")
        self.status_detail.setText(text)
        self.status_detail.show()

    # -- keyboard / clipboard -----------------------------------------------------
    def _handle_view_key(self, event: QKeyEvent) -> bool:
        if not self._pty_connected or self._bridge is None:
            return False
        modifiers = event.modifiers()
        control = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if control and shift:
            if event.key() == Qt.Key.Key_C:
                self._copy_selection()
                return True
            if event.key() == Qt.Key.Key_V:
                self._paste_clipboard()
                return True
        data = self._encode_key_event(event)
        if not data:
            return True
        self._bridge.send(data)
        return True

    def _encode_key_event(self, event: QKeyEvent) -> bytes | None:
        modifiers = event.modifiers()
        mod_names: set[str] = set()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            mod_names.add("ctrl")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            mod_names.add("alt")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            mod_names.add("shift")
        named = _QT_KEY_NAMES.get(event.key())
        if named is not None:
            return key_to_bytes(named, mod_names)
        text = event.text()
        if not text:
            return None
        if "ctrl" in mod_names:
            try:
                return text.encode("latin-1")
            except UnicodeEncodeError:
                return None
        if "alt" in mod_names:
            return b"\x1b" + text.encode("utf-8")
        return text.encode("utf-8")

    def _copy_selection(self) -> None:
        cursor = self.view.textCursor()
        if cursor.hasSelection():
            text = cursor.selectedText().replace(chr(0x2029), "\n")
        else:
            text = self.view.toPlainText()
        clipboard = QApplication.clipboard()
        if clipboard is not None and text:
            clipboard.setText(text)

    def _copy_all(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.view.toPlainText())

    def _paste_clipboard(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        text = clipboard.text()
        if not text:
            return
        if self._pty_connected and self._bridge is not None:
            bracketed = bool(self._renderer is not None and self._renderer.bracketed_paste)
            self._bridge.send(encode_paste(text, bracketed=bracketed))
            return
        self.cmd_input.insert(text.replace("\r", " ").replace("\n", " "))
        self.cmd_input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_options_menu(self) -> None:
        menu = QMenu(self)
        copy_act = menu.addAction("Copy all output")
        copy_act.triggered.connect(self._copy_all)
        paste_act = menu.addAction("Paste into terminal")
        paste_act.triggered.connect(self._paste_clipboard)
        clear_act = menu.addAction("Clear buffer")
        clear_act.triggered.connect(self.clear)
        menu.addSeparator()
        reconnect_act = menu.addAction("Reconnect PTY")
        reconnect_act.triggered.connect(self.reconnect)
        interrupt_act = menu.addAction("Send Ctrl-C")
        interrupt_act.setEnabled(self._pty_connected)
        interrupt_act.triggered.connect(self._send_ctrl_c)
        menu.exec(self.btn_opts.mapToGlobal(self.btn_opts.rect().bottomLeft()))

    def _send_ctrl_c(self) -> None:
        if self._pty_connected and self._bridge is not None:
            self._bridge.send(b"\x03")
