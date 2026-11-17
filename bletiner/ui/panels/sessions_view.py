"""Investigation history panel: real persisted sessions with report/folder actions.

Every card is built from a bletiner.services.session_store.SessionSummary returned by
controller.sessions(). There is no demo content: when the store has no history the panel explains how to
create the first investigation.

load(controller) never blocks the Qt thread: inside qasync it schedules controller.sessions() with
asyncio.ensure_future; without a running loop (plain Qt bootstrap) it falls back to a worker thread and
marshals results back through Qt signals.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bletiner.services.session_store import SessionSummary

_EMPTY_TEXT = "No investigations yet. Start one from Investigation."
_NO_CONTROLLER_TEXT = "Session history is unavailable because the investigation controller is not attached."

_STATUS_STYLES: dict[str, tuple[str, str]] = {
    "completed": ("rgba(16, 185, 129, 0.14)", "#10b981"),
    "running": ("rgba(56, 189, 248, 0.14)", "#38bdf8"),
    "stopped": ("rgba(245, 158, 11, 0.14)", "#f59e0b"),
    "interrupted": ("rgba(245, 158, 11, 0.14)", "#f59e0b"),
    "error": ("rgba(239, 68, 68, 0.14)", "#ef4444"),
}

_PANEL_QSS = """
QWidget#sessionsPage { background: transparent; }
QLabel#sessionsPageTitle { color: #ffffff; font-size: 20px; font-weight: 800; }
QLabel#sessionsPageSubtitle { color: #8892b0; font-size: 12px; }
QLabel#sessionsStatus { color: #8892b0; font-size: 11px; }
QLabel#sessionsEmptyTitle { color: #f3f4f6; font-size: 14px; font-weight: 700; }
QLabel#sessionsEmptyHint { color: #8892b0; font-size: 12px; }
QLabel#sessionsError { color: #ef4444; font-size: 12px; }
QFrame#sessionHistoryCard {
    background-color: #12151e; border: 1px solid #1c2130; border-radius: 12px;
}
QFrame#sessionHistoryCard:hover { border-color: #2a3346; }
QLabel#sessionTarget { color: #38bdf8; font-size: 15px; font-weight: 700; }
QLabel#sessionMeta { color: #8892b0; font-size: 11px; }
QLabel#sessionBadge {
    background-color: rgba(245, 158, 11, 0.15); color: #f59e0b;
    border: 1px solid rgba(245, 158, 11, 0.4); border-radius: 9px;
    padding: 2px 9px; font-size: 10px; font-weight: 700;
}
QLabel#sessionBadgeConfirmed {
    background-color: rgba(239, 68, 68, 0.15); color: #ef4444;
    border: 1px solid rgba(239, 68, 68, 0.4); border-radius: 9px;
    padding: 2px 9px; font-size: 10px; font-weight: 700;
}
QPushButton.sessionAction {
    background-color: #1a1e28; border: 1px solid #2a3142; border-radius: 7px;
    color: #cbd5e1; font-size: 11px; font-weight: 600; padding: 5px 12px;
}
QPushButton.sessionAction:hover { background-color: #222838; color: #f3f4f6; border-color: #38bdf8; }
QPushButton.sessionActionPrimary {
    background-color: rgba(56, 189, 248, 0.12); border: 1px solid #38bdf8; border-radius: 7px;
    color: #38bdf8; font-size: 11px; font-weight: 700; padding: 5px 12px;
}
QPushButton.sessionActionPrimary:hover { background-color: rgba(56, 189, 248, 0.22); }
QPushButton.sessionDelete {
    background-color: rgba(239, 68, 68, 0.10); border: 1px solid rgba(239, 68, 68, 0.45);
    border-radius: 7px; color: #ef4444; font-size: 11px; font-weight: 600; padding: 5px 12px;
}
QPushButton.sessionDelete:hover { background-color: rgba(239, 68, 68, 0.22); }
QScrollArea { border: none; background: transparent; }
"""


def _relative_time(value: datetime | None) -> str:
    if value is None:
        return "time unknown"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    seconds = (datetime.now(UTC) - value.astimezone(UTC)).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    if seconds < 86400 * 7:
        return f"{int(seconds // 86400)}d ago"
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(round(seconds), 60)
    return f"{minutes}m {remainder}s"


def _open_path(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if not resolved.exists():
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(resolved)))


class SessionCard(QFrame):
    """One persisted investigation: target, status, findings counts and report actions."""

    open_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, summary: SessionSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.summary = summary
        self.setObjectName("sessionHistoryCard")
        self.setToolTip("Double-click to open this session in the Findings view.")
        self._build_ui()

    def _build_ui(self) -> None:
        summary = self.summary
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.target_label = QLabel(summary.target or "unknown target")
        self.target_label.setObjectName("sessionTarget")
        self.target_label.setWordWrap(True)
        top.addWidget(self.target_label, 1)
        self.status_label = QLabel(str(summary.status).upper())
        background, fore = _STATUS_STYLES.get(str(summary.status).lower(), ("#1a1e28", "#8892b0"))
        self.status_label.setStyleSheet(
            f"QLabel {{ background-color: {background}; color: {fore}; border: 1px solid {fore};"
            " border-radius: 9px; padding: 2px 10px; font-size: 10px; font-weight: 700; }"
        )
        top.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        engine = summary.engine or "local"
        engine_text = f"{engine} / {summary.model}" if summary.model else engine
        meta_parts = [
            engine_text,
            f"mode {summary.mode or 'n/a'}",
            f"started {_relative_time(summary.started_at)}",
        ]
        if summary.completed_at is not None:
            meta_parts.append(f"finished {_relative_time(summary.completed_at)}")
        duration = _format_duration(summary.duration_s)
        if duration:
            meta_parts.append(f"duration {duration}")
        meta_parts.append(f"{summary.tokens_used} tokens")
        self.meta_label = QLabel("  \u00b7  ".join(meta_parts))
        self.meta_label.setObjectName("sessionMeta")
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        badges = QHBoxLayout()
        badges.setSpacing(8)
        findings = QLabel(f"\u2605 {summary.findings_total} findings")
        findings.setObjectName("sessionBadge")
        badges.addWidget(findings)
        confirmed = QLabel(f"\u2713 {summary.findings_confirmed} confirmed")
        confirmed.setObjectName("sessionBadgeConfirmed")
        badges.addWidget(confirmed)
        badges.addStretch(1)
        layout.addLayout(badges)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        if summary.report_html is not None:
            btn_html = QPushButton("Open HTML Report")
            btn_html.setProperty("class", "sessionActionPrimary")
            btn_html.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_html.clicked.connect(lambda: _open_path(self.summary.report_html))
            actions.addWidget(btn_html)
        if summary.report_md is not None:
            btn_md = QPushButton("Markdown")
            btn_md.setProperty("class", "sessionAction")
            btn_md.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_md.clicked.connect(lambda: _open_path(self.summary.report_md))
            actions.addWidget(btn_md)
        if summary.report_html is None and summary.report_md is None:
            no_report = QLabel("No report files recorded")
            no_report.setObjectName("sessionMeta")
            actions.addWidget(no_report)
        btn_folder = QPushButton("Evidence Folder")
        btn_folder.setProperty("class", "sessionAction")
        btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_folder.clicked.connect(lambda: _open_path(self.summary.root))
        actions.addWidget(btn_folder)
        actions.addStretch(1)
        btn_delete = QPushButton("Delete")
        btn_delete.setProperty("class", "sessionDelete")
        btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_delete.setToolTip("Delete this investigation's reports, evidence and history rows.")
        btn_delete.clicked.connect(lambda: self.delete_requested.emit(self.summary.investigation_id))
        actions.addWidget(btn_delete)
        layout.addLayout(actions)

    def mouseDoubleClickEvent(self, event: Any) -> None:
        self.open_requested.emit(self.summary.investigation_id)
        super().mouseDoubleClickEvent(event)


class SessionsPanel(QWidget):
    """Full-page investigation history built from SessionStore summaries."""

    session_open_requested = Signal(str)

    _load_completed = Signal(object, int)
    _load_failed = Signal(str, int)
    _delete_completed = Signal(str, int)
    _delete_failed = Signal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionsPage")
        self.setStyleSheet(_PANEL_QSS)
        self._controller: Any | None = None
        self._generation = 0
        self._load_task: asyncio.Task[Any] | None = None
        self._delete_task: asyncio.Task[Any] | None = None
        self._summaries: list[SessionSummary] = []
        self._cards: list[SessionCard] = []
        self._build_ui()
        self._load_completed.connect(self._on_load_completed)
        self._load_failed.connect(self._on_load_failed)
        self._delete_completed.connect(self._on_delete_completed)
        self._delete_failed.connect(self._on_delete_failed)
        self._render_empty(_NO_CONTROLLER_TEXT)

    # --- construction --------------------------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        title = QLabel("Investigation History")
        title.setObjectName("sessionsPageTitle")
        subtitle = QLabel(
            "Persisted sessions, findings and generated reports from every completed or stopped investigation."
        )
        subtitle.setObjectName("sessionsPageSubtitle")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box, 1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setProperty("class", "sessionAction")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self.status_label = QLabel("")
        self.status_label.setObjectName("sessionsStatus")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cards_host = QWidget()
        self.cards_host.setObjectName("sessionsCardsHost")
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 4, 0)
        self.cards_layout.setSpacing(12)
        self.scroll.setWidget(self.cards_host)
        root.addWidget(self.scroll, 1)

    # --- public API (INTERFACES.md) ------------------------------------------------------------------------
    def load(self, controller: Any) -> None:
        """Attach the investigation controller and schedule an async history load."""
        self._controller = controller
        if controller is None:
            self._render_empty(_NO_CONTROLLER_TEXT)
            return
        self.refresh()

    def refresh(self) -> None:
        """Reload history from controller.sessions() without blocking the Qt event loop."""
        controller = self._controller
        if controller is None:
            self._render_empty(_NO_CONTROLLER_TEXT)
            return
        sessions_method = getattr(controller, "sessions", None)
        if sessions_method is None:
            self._render_error("The controller does not expose session history.")
            return
        self._generation += 1
        generation = self._generation
        self.status_label.setText("Loading session history...")
        self.status_label.setStyleSheet("")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            if self._load_task is not None and not self._load_task.done():
                self._load_task.cancel()
            self._load_task = asyncio.ensure_future(self._load_async(generation))
        else:
            worker = threading.Thread(target=self._load_in_thread, args=(generation,), daemon=True)
            worker.start()

    # --- async plumbing ------------------------------------------------------------------------------------
    async def _load_async(self, generation: int) -> None:
        try:
            summaries = await self._controller.sessions(limit=100)
        except Exception as exc:  # surfaced in the UI; history must never crash the shell
            self._emit_failure(exc, generation)
            return
        self._emit_success(self._summaries_list(summaries), generation)

    def _load_in_thread(self, generation: int) -> None:
        try:
            summaries = asyncio.run(self._controller.sessions(limit=100))
        except Exception as exc:
            self._emit_failure(exc, generation)
            return
        self._emit_success(self._summaries_list(summaries), generation)

    @staticmethod
    def _summaries_list(summaries: Any) -> list[SessionSummary]:
        return [summary for summary in (summaries or []) if summary is not None]

    def _emit_success(self, summaries: list[SessionSummary], generation: int) -> None:
        # RuntimeError means the widget was destroyed while a background load was in flight.
        with contextlib.suppress(RuntimeError):
            self._load_completed.emit(summaries, generation)

    def _emit_failure(self, exc: BaseException, generation: int) -> None:
        message = str(exc) or exc.__class__.__name__
        with contextlib.suppress(RuntimeError):
            self._load_failed.emit(message, generation)

    def _on_load_completed(self, summaries: object, generation: int) -> None:
        if generation != self._generation:
            return
        items = list(summaries) if isinstance(summaries, (list, tuple)) else []
        self._render_summaries(items)

    def _on_load_failed(self, message: str, generation: int) -> None:
        if generation != self._generation:
            return
        self._render_error(message)

    # --- rendering -----------------------------------------------------------------------------------------
    def _render_summaries(self, summaries: list[SessionSummary]) -> None:
        self._summaries = summaries
        self._clear_cards()
        if not summaries:
            self.status_label.setText("0 investigations")
            self._add_empty_state(
                _EMPTY_TEXT, "Start a local or AI investigation to create the first session."
            )
            return
        finished = sum(1 for s in summaries if str(s.status) == "completed")
        self.status_label.setText(
            f"{len(summaries)} investigation{'s' if len(summaries) != 1 else ''} - {finished} completed"
        )
        for summary in summaries:
            card = SessionCard(summary)
            card.open_requested.connect(self._on_card_open_requested)
            card.delete_requested.connect(self._on_delete_requested)
            self._cards.append(card)
            self.cards_layout.addWidget(card)
        self.cards_layout.addStretch(1)

    def _render_empty(self, text: str) -> None:
        self._clear_cards()
        self.status_label.setText("")
        self._add_empty_state(text, "")

    def _render_error(self, message: str) -> None:
        self._clear_cards()
        self.status_label.setText("Could not load session history.")
        host = QFrame()
        host.setObjectName("sessionHistoryCard")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        error = QLabel(f"Could not load session history: {message}")
        error.setObjectName("sessionsError")
        error.setWordWrap(True)
        layout.addWidget(error)
        retry = QPushButton("Retry")
        retry.setProperty("class", "sessionAction")
        retry.setCursor(Qt.CursorShape.PointingHandCursor)
        retry.clicked.connect(self.refresh)
        layout.addWidget(retry, 0, Qt.AlignmentFlag.AlignLeft)
        self.cards_layout.addWidget(host)
        self.cards_layout.addStretch(1)

    def _add_empty_state(self, title: str, hint: str) -> None:
        host = QFrame()
        host.setObjectName("sessionHistoryCard")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(20, 22, 20, 22)
        layout.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("sessionsEmptyTitle")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("sessionsEmptyHint")
            hint_label.setWordWrap(True)
            layout.addWidget(hint_label)
        self.cards_layout.addWidget(host)
        self.cards_layout.addStretch(1)

    def _clear_cards(self) -> None:
        self._cards.clear()
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    # --- actions -------------------------------------------------------------------------------------------
    def _summary_for_id(self, investigation_id: str) -> SessionSummary | None:
        for summary in self._summaries:
            if summary.investigation_id == investigation_id:
                return summary
        return None

    def _on_card_open_requested(self, investigation_id: str) -> None:
        self.session_open_requested.emit(investigation_id)

    def _on_delete_requested(self, investigation_id: str) -> None:
        summary = self._summary_for_id(investigation_id)
        target = summary.target if summary is not None else investigation_id
        answer = QMessageBox.question(
            self,
            "Delete investigation",
            f"Delete the investigation of {target}?\n\n"
            "This permanently removes its reports, evidence, logs and history rows.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._schedule_delete(investigation_id)

    def _schedule_delete(self, investigation_id: str) -> None:
        controller = self._controller
        delete_method = getattr(controller, "delete_session", None)
        if delete_method is None:
            self._render_error("The controller does not expose session deletion.")
            return
        generation = self._generation
        self.status_label.setText("Deleting investigation...")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            self._delete_task = asyncio.ensure_future(self._delete_async(investigation_id, generation))
        else:
            threading.Thread(
                target=self._delete_in_thread, args=(investigation_id, generation), daemon=True
            ).start()

    async def _delete_async(self, investigation_id: str, generation: int) -> None:
        try:
            await self._controller.delete_session(investigation_id)
        except Exception as exc:
            self._emit_delete_failure(exc, generation)
            return
        self._emit_delete_success(investigation_id, generation)

    def _delete_in_thread(self, investigation_id: str, generation: int) -> None:
        try:
            asyncio.run(self._controller.delete_session(investigation_id))
        except Exception as exc:
            self._emit_delete_failure(exc, generation)
            return
        self._emit_delete_success(investigation_id, generation)

    def _emit_delete_success(self, investigation_id: str, generation: int) -> None:
        with contextlib.suppress(RuntimeError):
            self._delete_completed.emit(investigation_id, generation)

    def _emit_delete_failure(self, exc: BaseException, generation: int) -> None:
        message = str(exc) or exc.__class__.__name__
        with contextlib.suppress(RuntimeError):
            self._delete_failed.emit(message, generation)

    def _on_delete_completed(self, investigation_id: str, generation: int) -> None:
        if generation != self._generation:
            return
        self.status_label.setText(f"Deleted investigation {investigation_id[:8]}.")
        self.refresh()

    def _on_delete_failed(self, message: str, generation: int) -> None:
        if generation != self._generation:
            return
        self.status_label.setText(f"Delete failed: {message}")
        self._render_error(message)
