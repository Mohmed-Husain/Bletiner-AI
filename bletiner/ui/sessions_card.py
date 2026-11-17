"""SessionsFilesCard: live artifact tree for the current investigation plus real session history.

The card is intentionally data-driven: it renders whatever files the running investigation actually wrote
(reports/evidence/logs under InvestigationPaths) and the session summaries the SessionStore returns. There
is no demo/example content anywhere - when there is nothing yet, the empty state explains what to do next.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bletiner.services.session_store import SessionSummary
from bletiner.ui.findings_dialog import FindingsDialog

__all__ = ["FindingsDialog", "SessionsFilesCard"]

_FOLDER_ICON = "\U0001f4c1"
_FILE_ICON = "\U0001f4c4"
_WEB_ICON = "\U0001f310"

_ARTIFACT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("reports", "reports"),
    ("evidence", "evidence"),
    ("logs", "logs"),
)

_STATUS_TINTS: dict[str, str] = {
    "completed": "#10b981",
    "running": "#38bdf8",
    "stopped": "#f59e0b",
    "error": "#ef4444",
    "interrupted": "#f59e0b",
}

_CARD_QSS = """
QLabel[role="cardHeader"] { background: transparent; }
QLabel#sessionsTarget { color: #f3f4f6; font-size: 12px; font-weight: 700; background: transparent; }
QLabel#sessionsMeta { color: #8892b0; font-size: 10px; background: transparent; }
QLabel#sessionsSection { color: #8892b0; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; }
QLabel#sessionsEmpty { color: #64748b; font-size: 11px; background: transparent; }
QPushButton#sessionsRefresh {
    background: transparent; border: 1px solid transparent; border-radius: 6px;
    color: #8892b0; font-size: 13px; padding: 2px 6px;
}
QPushButton#sessionsRefresh:hover { background-color: #1c2130; color: #f3f4f6; }
QPushButton#sessionsRefresh:disabled { color: #3f4a5f; }
QTreeWidget#artifactTree, QListWidget#previousSessions {
    background-color: #0d0f16; border: 1px solid #1a1e2b; border-radius: 8px;
    color: #cbd5e1; outline: 0; font-size: 11px;
}
QTreeWidget#artifactTree::item { padding: 3px 4px; }
QTreeWidget#artifactTree::item:selected { background-color: #1a2030; color: #f3f4f6; }
QListWidget#previousSessions::item { padding: 0px; border-bottom: 1px solid #141722; }
QListWidget#previousSessions::item:hover { background-color: #141824; }
QListWidget#previousSessions::item:selected { background-color: #1a2030; color: #f3f4f6; }
QLabel#previousRowTarget { background: transparent; font-size: 11px; font-weight: 700; }
QLabel#previousRowMeta { background: transparent; color: #8892b0; font-size: 10px; }
QLabel#previousRowEmpty { background: transparent; color: #64748b; font-size: 11px; padding: 4px; }
"""


def format_relative_time(value: datetime | None, *, now: datetime | None = None) -> str:
    """Human-friendly age for a timestamp; empty string when the timestamp is unknown."""
    if value is None:
        return "time unknown"
    reference = now or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    seconds = (reference - value.astimezone(UTC)).total_seconds()
    if seconds < 0:
        return value.astimezone().strftime("%Y-%m-%d %H:%M")
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    if seconds < 86400 * 7:
        return f"{int(seconds // 86400)}d ago"
    return value.astimezone().strftime("%Y-%m-%d")


def _icon_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        return _WEB_ICON
    if suffix in (".png", ".jpg", ".jpeg", ".svg"):
        return "\U0001f5bc"
    return _FILE_ICON


def _summary_status_color(summary: SessionSummary) -> str:
    return _STATUS_TINTS.get(str(summary.status).lower(), "#8892b0")


def _open_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if not resolved.exists():
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(resolved)))


class _PreviousSessionRow(QWidget):
    """Two-line, width-aware row for the previous sessions list, so long targets never overflow the card."""

    def __init__(self, summary: SessionSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Let clicks/double-clicks fall through to the QListWidget (selection, open, context menu).
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._target_text = summary.target or "unknown target"
        findings_word = "finding" if summary.findings_total == 1 else "findings"
        when = summary.completed_at or summary.started_at
        self._meta_text = (
            f"{str(summary.status).upper()}  \u00b7  {summary.findings_total} {findings_word}"
            f"  \u00b7  {summary.engine or 'legacy'}  \u00b7  {format_relative_time(when)}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(1)
        self.target_label = QLabel(self._target_text)
        self.target_label.setObjectName("previousRowTarget")
        tint = _STATUS_TINTS.get(str(summary.status).lower(), "#38bdf8")
        self.target_label.setStyleSheet(f"color: {tint};")
        self.meta_label = QLabel(self._meta_text)
        self.meta_label.setObjectName("previousRowMeta")
        layout.addWidget(self.target_label)
        layout.addWidget(self.meta_label)

    def resizeEvent(self, event: Any) -> None:
        available = max(self.width() - 20, 60)
        target_metrics = QFontMetrics(self.target_label.font())
        self.target_label.setText(
            target_metrics.elidedText(self._target_text, Qt.TextElideMode.ElideMiddle, available)
        )
        meta_metrics = QFontMetrics(self.meta_label.font())
        self.meta_label.setText(
            meta_metrics.elidedText(self._meta_text, Qt.TextElideMode.ElideRight, available)
        )
        super().resizeEvent(event)


class SessionsFilesCard(QFrame):
    """Right-hand card: real artifact tree for the active run plus previous sessions from SessionStore."""

    session_open_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionsFilesCard")
        self.setMinimumWidth(240)
        self.setStyleSheet(_CARD_QSS)
        self._target: str | None = None
        self._paths: Any | None = None
        self._engine = ""
        self._status = ""
        self._findings_total = 0
        self._previous: list[SessionSummary] = []
        self._build_ui()
        self.clear_current()
        self.set_previous([])

    # --- construction --------------------------------------------------------------------------------------
    def _build_ui(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setSpacing(6)
        header = QLabel("\U0001f4c1  SESSIONS & FILES")
        header.setProperty("role", "cardHeader")
        header_row.addWidget(header)
        header_row.addStretch(1)
        self.refresh_btn = QPushButton("\u27f3")
        self.refresh_btn.setObjectName("sessionsRefresh")
        self.refresh_btn.setToolTip("Refresh the artifact tree from disk (F5)")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.refresh_current_tree)
        header_row.addWidget(self.refresh_btn)
        col.addLayout(header_row)

        self.target_label = QLabel("No active investigation")
        self.target_label.setObjectName("sessionsTarget")
        self.target_label.setWordWrap(True)
        self.target_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(self.target_label)
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("sessionsMeta")
        self.meta_label.setWordWrap(True)
        col.addWidget(self.meta_label)

        self.tree = QTreeWidget()
        self.tree.setObjectName("artifactTree")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setMinimumHeight(110)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_tree_double_clicked)
        col.addWidget(self.tree, 3)

        previous_header = QHBoxLayout()
        previous_header.setSpacing(6)
        self.previous_label = QLabel("PREVIOUS SESSIONS")
        self.previous_label.setObjectName("sessionsSection")
        previous_header.addWidget(self.previous_label)
        previous_header.addStretch(1)
        col.addLayout(previous_header)

        self.previous = QListWidget()
        self.previous.setObjectName("previousSessions")
        self.previous.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.previous.setWordWrap(True)
        self.previous.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.previous.setMinimumHeight(80)
        self.previous.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.previous.customContextMenuRequested.connect(self._previous_context_menu)
        self.previous.itemDoubleClicked.connect(self._on_previous_double_clicked)
        col.addWidget(self.previous, 2)

    # --- current investigation -----------------------------------------------------------------------------
    def set_current(
        self,
        target: str | None,
        paths: Any | None,
        *,
        engine: str = "",
        status: str = "",
        findings_total: int = 0,
    ) -> None:
        """Point the tree at the active investigation. None clears the card to its empty state."""
        self._target = target or None
        self._paths = paths
        self._engine = engine or ""
        self._status = status or ""
        self._findings_total = max(int(findings_total or 0), 0)
        self.refresh_btn.setEnabled(self._target is not None and self._paths is not None)
        if self._target is None or self._paths is None:
            self.clear_current()
            return
        self._set_header_visible(True)
        self.target_label.setText(self._target)
        bits = [bit for bit in (self._engine, self._status) if bit]
        finding_word = "finding" if self._findings_total == 1 else "findings"
        bits.append(f"{self._findings_total} {finding_word}")
        self.meta_label.setText("  \u00b7  ".join(bits))
        self.refresh_current_tree()

    def refresh_current_tree(self) -> None:
        """Re-scan the investigation directories and rebuild the artifact tree."""
        self.tree.clear()
        if self._target is None or self._paths is None:
            self._render_empty_tree()
            return
        root = QTreeWidgetItem([f"{self._target}"])
        root.setData(0, Qt.ItemDataRole.UserRole, str(getattr(self._paths, "root", "")))
        root.setToolTip(0, str(getattr(self._paths, "root", "")))
        root.setExpanded(True)
        root.setFont(0, self._bold_font())
        populated_any = False
        for label, attribute in _ARTIFACT_SECTIONS:
            directory = getattr(self._paths, attribute, None)
            if directory is None:
                continue
            folder = QTreeWidgetItem([f"{_FOLDER_ICON} {label}/"])
            folder.setData(0, Qt.ItemDataRole.UserRole, str(directory))
            folder.setToolTip(0, str(directory))
            folder.setExpanded(True)
            if self._populate_dir(folder, Path(directory), depth=0):
                populated_any = True
            root.addChild(folder)
        self.tree.addTopLevelItem(root)
        self.tree.expandAll()
        if not populated_any:
            hint = QTreeWidgetItem(["Artifacts appear here as the investigation writes them."])
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            hint.setForeground(0, QBrush(QColor("#64748b")))
            root.addChild(hint)

    def _populate_dir(self, parent: QTreeWidgetItem, directory: Path, *, depth: int) -> bool:
        if depth > 3:
            return False
        try:
            if not directory.is_dir():
                return False
            entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return False
        populated = False
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                node = QTreeWidgetItem([f"{_FOLDER_ICON} {entry.name}/"])
                node.setData(0, Qt.ItemDataRole.UserRole, str(entry))
                node.setToolTip(0, str(entry))
                parent.addChild(node)
                child_populated = self._populate_dir(node, entry, depth=depth + 1)
                node.setExpanded(depth == 0 or child_populated)
                populated = populated or child_populated or node.childCount() > 0
            elif entry.is_file():
                item = QTreeWidgetItem([f"{_icon_for(entry)} {entry.name}"])
                item.setData(0, Qt.ItemDataRole.UserRole, str(entry))
                item.setToolTip(0, str(entry))
                parent.addChild(item)
                populated = True
        return populated

    def clear_current(self) -> None:
        """Reset the card to its honest empty state (history may still be shown)."""
        self._target = None
        self._paths = None
        self._engine = ""
        self._status = ""
        self._findings_total = 0
        self.target_label.setVisible(False)
        self.meta_label.setVisible(False)
        self.refresh_btn.setEnabled(False)
        self.tree.clear()
        self._render_empty_tree()

    def _set_header_visible(self, visible: bool) -> None:
        self.target_label.setVisible(visible)
        self.meta_label.setVisible(visible)

    def _render_empty_tree(self) -> None:
        # Single empty state: the tree placeholder is the only "nothing yet" message in this card.
        self.tree.setWordWrap(True)
        placeholder = QTreeWidgetItem(["No active investigation. Start one from the Investigation card."])
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        placeholder.setForeground(0, QBrush(QColor("#64748b")))
        self.tree.addTopLevelItem(placeholder)

    # --- previous sessions ---------------------------------------------------------------------------------
    def set_previous(self, sessions: list[SessionSummary]) -> None:
        """Render previous investigations from SessionStore summaries (no placeholders)."""
        self._previous = [summary for summary in sessions if summary is not None]
        self.previous.clear()
        self.previous_label.setText(
            f"PREVIOUS SESSIONS ({len(self._previous)})" if self._previous else "PREVIOUS SESSIONS"
        )
        if not self._previous:
            empty = QListWidgetItem("No previous sessions yet.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setForeground(QBrush(QColor("#64748b")))
            self.previous.addItem(empty)
            return
        for index, summary in enumerate(self._previous):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, summary.investigation_id)
            item.setToolTip(self._summary_tooltip(summary))
            row = _PreviousSessionRow(summary)
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            self.previous.addItem(item)
            self.previous.setItemWidget(item, row)
            if index >= 100:
                break

    @staticmethod
    def _summary_tooltip(summary: SessionSummary) -> str:
        lines = [
            f"Target: {summary.target}",
            f"Status: {summary.status}",
            f"Mode: {summary.mode or 'unknown'}",
        ]
        engine = summary.engine or "local"
        model = summary.model or "n/a"
        lines.append(f"Engine: {engine} (model: {model})" if summary.provider else f"Engine: {engine}")
        lines.append(f"Findings: {summary.findings_total} total, {summary.findings_confirmed} confirmed")
        if summary.started_at:
            lines.append(f"Started: {summary.started_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}")
        if summary.completed_at:
            lines.append(f"Finished: {summary.completed_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}")
        if summary.duration_s is not None:
            lines.append(f"Duration: {summary.duration_s:.1f}s")
        lines.append(f"Tokens: {summary.tokens_used}")
        reports = [
            name for name, path in (("Markdown", summary.report_md), ("HTML", summary.report_html)) if path
        ]
        lines.append(f"Reports: {', '.join(reports) if reports else 'none'}")
        lines.append(f"Folder: {summary.root}")
        return "\n".join(lines)

    def _summary_for_id(self, investigation_id: str) -> SessionSummary | None:
        for summary in self._previous:
            if summary.investigation_id == investigation_id:
                return summary
        return None

    # --- interactions --------------------------------------------------------------------------------------
    def _on_tree_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        raw = item.data(0, Qt.ItemDataRole.UserRole)
        if not raw:
            return
        path = Path(str(raw))
        if path.is_file():
            if not _open_path(path):
                self.meta_label.setText(f"Could not open {path.name} - it may have been moved or deleted.")
        elif path.is_dir():
            item.setExpanded(not item.isExpanded())

    def _tree_context_menu(self, position: QPoint) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        raw = item.data(0, Qt.ItemDataRole.UserRole)
        if not raw:
            return
        path = Path(str(raw))
        if not path.exists():
            return
        menu = QMenu(self)
        if path.is_file():
            menu.addAction("Open file", lambda: _open_path(path))
        menu.addAction("Open containing folder", lambda: _open_path(path.parent))
        menu.addAction("Copy path", lambda: self._copy_path(path))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _previous_context_menu(self, position: QPoint) -> None:
        item = self.previous.itemAt(position)
        if item is None:
            return
        investigation_id = item.data(Qt.ItemDataRole.UserRole)
        if not investigation_id:
            return
        summary = self._summary_for_id(str(investigation_id))
        if summary is None:
            return
        menu = QMenu(self)
        menu.addAction(
            "Open session history",
            lambda: self.session_open_requested.emit(summary.investigation_id),
        )
        menu.addSeparator()
        report_actions: list[Any] = []
        if summary.report_html is not None:
            report_actions.append(menu.addAction("Open HTML report", lambda: _open_path(summary.report_html)))
        if summary.report_md is not None:
            report_actions.append(
                menu.addAction("Open Markdown report", lambda: _open_path(summary.report_md))
            )
        if not report_actions:
            missing = menu.addAction("No report files recorded")
            missing.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Open evidence folder", lambda: _open_path(summary.root))
        menu.exec(self.previous.viewport().mapToGlobal(position))

    @staticmethod
    def _copy_path(path: Path) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(str(path))

    def _on_previous_double_clicked(self, item: QListWidgetItem) -> None:
        investigation_id = item.data(Qt.ItemDataRole.UserRole)
        if investigation_id:
            self.session_open_requested.emit(str(investigation_id))

    @staticmethod
    def _bold_font() -> Any:
        from PySide6.QtGui import QFont

        font = QFont()
        font.setBold(True)
        return font
