"""FindingsDialog: browse real Finding entities recorded for an investigation.

The dialog shows every finding the engines persisted (no demo rows), supports text search plus the
"potential" / "confirmed" triage filters used by the Investigation card badges, and renders a detail pane
with severity, confidence, verification status, asset, summary and remediation guidance.

Triage vocabulary (INTERFACES.md):
* confirmed = verification_status is VERIFIED
* potential = everything that is not VERIFIED (detected / needs triage / under investigation / ...)
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bletiner.models.entities import Finding, VerificationStatus

_SEVERITY_COLORS: dict[str, str] = {
    "CRITICAL": "#ef4444",
    "HIGH": "#f87171",
    "MEDIUM": "#f59e0b",
    "LOW": "#38bdf8",
    "INFO": "#8892b0",
}

_STATUS_COLORS: dict[str, str] = {
    "VERIFIED": "#10b981",
    "DETECTED": "#f59e0b",
    "NEEDS_TRIAGE": "#f59e0b",
    "UNDER_INVESTIGATION": "#38bdf8",
    "INCONCLUSIVE": "#8892b0",
    "FALSE_POSITIVE": "#64748b",
    "ACCEPTED_RISK": "#a78bfa",
}

_CONFIRMED_FILTER = "confirmed"
_POTENTIAL_FILTER = "potential"

_DIALOG_QSS = """
QDialog { background-color: #0a0c10; }
QLabel { background: transparent; color: #f3f4f6; }
QFrame#findingsHeader { background-color: #12151e; border: 1px solid #1c2130; border-radius: 10px; }
QLabel#findingsTitle { color: #ffffff; font-size: 16px; font-weight: 800; }
QLabel#findingsCounts { color: #8892b0; font-size: 12px; }
QLabel#detailTitle { color: #f3f4f6; font-size: 13px; font-weight: 700; }
QLabel#detailMeta { color: #8892b0; font-size: 11px; }
QLabel#detailSection { color: #8892b0; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; }
QLineEdit#findingsSearch {
    background-color: #0d0f16; border: 1px solid #1a1e2b; border-radius: 8px;
    color: #f3f4f6; padding: 7px 10px; font-size: 12px;
}
QLineEdit#findingsSearch:focus { border: 1px solid #38bdf8; }
QPushButton.filterBtn {
    background-color: #12151e; border: 1px solid #1c2130; border-radius: 8px;
    color: #8892b0; font-size: 11px; font-weight: 600; padding: 6px 14px;
}
QPushButton.filterBtn:hover { border-color: #38bdf8; color: #f3f4f6; }
QPushButton.filterBtn:checked {
    background-color: rgba(56, 189, 248, 0.14); border-color: #38bdf8; color: #38bdf8;
}
QPushButton#findingsClose {
    background-color: #1a1e28; border: 1px solid #2a3142; border-radius: 8px;
    color: #8892b0; font-size: 12px; font-weight: 600; padding: 7px 20px;
}
QPushButton#findingsClose:hover { background-color: #222838; color: #f3f4f6; }
QTreeWidget#findingsTable {
    background-color: #0d0f16; border: 1px solid #1a1e2b; border-radius: 8px;
    color: #f3f4f6; outline: 0; font-size: 12px;
}
QTreeWidget#findingsTable::item { padding: 5px 6px; border-bottom: 1px solid #141722; }
QTreeWidget#findingsTable::item:selected { background-color: #1a2030; }
QHeaderView::section {
    background-color: #12151e; color: #8892b0; border: none;
    border-bottom: 1px solid #1c2130; padding: 6px; font-size: 10px; font-weight: 700;
}
QTextBrowser#detailBody {
    background-color: #0d0f16; border: 1px solid #1a1e2b; border-radius: 8px;
    color: #cbd5e1; font-size: 12px; padding: 8px;
}
QFrame#detailPane { background-color: #12151e; border: 1px solid #1c2130; border-radius: 10px; }
"""


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _finding_is_confirmed(finding: Finding) -> bool:
    return finding.verification_status is VerificationStatus.VERIFIED


class FindingsDialog(QDialog):
    """Dark-themed findings browser with search plus potential/confirmed triage filters."""

    def __init__(
        self,
        findings: list[Finding],
        *,
        filter_state: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Investigation Findings")
        self.resize(1180, 760)
        self.setMinimumSize(1024, 640)
        self.setStyleSheet(_DIALOG_QSS)
        self._findings: list[Finding] = list(findings)
        self._filter_state: str | None = (
            filter_state
            if filter_state
            in (
                _POTENTIAL_FILTER,
                _CONFIRMED_FILTER,
            )
            else None
        )
        self._visible: list[Finding] = []
        self._build_ui()
        self._apply_filters()

    # --- construction --------------------------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        header = QFrame()
        header.setObjectName("findingsHeader")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(16, 12, 16, 12)
        header_row.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("Findings")
        title.setObjectName("findingsTitle")
        self.counts = QLabel("")
        self.counts.setObjectName("findingsCounts")
        title_box.addWidget(title)
        title_box.addWidget(self.counts)
        header_row.addLayout(title_box, 1)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("findingsClose")
        close_btn.clicked.connect(self.accept)
        header_row.addWidget(close_btn)
        root.addWidget(header)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("findingsSearch")
        self.search.setPlaceholderText("Search title, asset, summary or severity...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self._apply_filters())
        filter_row.addWidget(self.search, 1)
        self.btn_all = self._filter_button("All", None)
        self.btn_potential = self._filter_button("Potential", _POTENTIAL_FILTER)
        self.btn_confirmed = self._filter_button("Confirmed", _CONFIRMED_FILTER)
        for button in (self.btn_all, self.btn_potential, self.btn_confirmed):
            filter_row.addWidget(button)
        root.addLayout(filter_row)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.table = QTreeWidget()
        self.table.setObjectName("findingsTable")
        self.table.setHeaderLabels(["Severity", "Confidence", "Status", "Asset", "Finding"])
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setUniformRowHeights(True)
        header_view = self.table.header()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 82)
        self.table.setColumnWidth(1, 96)
        self.table.setColumnWidth(2, 116)
        self.table.setColumnWidth(3, 180)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.currentItemChanged.connect(lambda current, _previous: self._show_detail(current))
        body.addWidget(self.table, 3)

        self.detail = QFrame()
        self.detail.setObjectName("detailPane")
        self.detail.setMinimumWidth(300)
        detail_col = QVBoxLayout(self.detail)
        detail_col.setContentsMargins(14, 12, 14, 12)
        detail_col.setSpacing(8)
        self.detail_title = QLabel("Select a finding")
        self.detail_title.setObjectName("detailTitle")
        self.detail_title.setWordWrap(True)
        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("detailMeta")
        self.detail_meta.setWordWrap(True)
        detail_col.addWidget(self.detail_title)
        detail_col.addWidget(self.detail_meta)
        self.detail_body = QTextBrowser()
        self.detail_body.setObjectName("detailBody")
        self.detail_body.setOpenExternalLinks(False)
        detail_col.addWidget(self.detail_body, 1)
        body.addWidget(self.detail, 2)
        root.addLayout(body, 1)

    def _filter_button(self, text: str, state: str | None) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("class", "filterBtn")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setChecked(self._filter_state == state)
        button.clicked.connect(lambda _checked=False, value=state: self._set_filter(value))
        return button

    # --- public surface ------------------------------------------------------------------------------------
    @property
    def findings(self) -> list[Finding]:
        return list(self._findings)

    @property
    def filter_state(self) -> str | None:
        return self._filter_state

    @property
    def visible_findings(self) -> list[Finding]:
        return list(self._visible)

    def set_findings(self, findings: list[Finding]) -> None:
        self._findings = list(findings)
        self._apply_filters()

    def set_filter_state(self, state: str | None) -> None:
        self._set_filter(state if state in (_POTENTIAL_FILTER, _CONFIRMED_FILTER) else None)

    # --- filtering -----------------------------------------------------------------------------------------
    def _set_filter(self, state: str | None) -> None:
        self._filter_state = state
        self.btn_all.setChecked(state is None)
        self.btn_potential.setChecked(state == _POTENTIAL_FILTER)
        self.btn_confirmed.setChecked(state == _CONFIRMED_FILTER)
        self._apply_filters()

    def _apply_filters(self) -> None:
        query = self.search.text().strip().lower()
        visible: list[Finding] = []
        for finding in self._findings:
            if self._filter_state == _CONFIRMED_FILTER and not _finding_is_confirmed(finding):
                continue
            if self._filter_state == _POTENTIAL_FILTER and _finding_is_confirmed(finding):
                continue
            if query and not self._matches_query(finding, query):
                continue
            visible.append(finding)
        self._visible = visible
        self._fill_table()
        confirmed = sum(1 for f in self._findings if _finding_is_confirmed(f))
        potential = len(self._findings) - confirmed
        shown = len(visible)
        total = len(self._findings)
        suffix = "" if shown == total else f" - {shown} shown"
        self.counts.setText(
            f"{total} finding{'s' if total != 1 else ''} - {potential} potential - {confirmed} confirmed{suffix}"
        )

    @staticmethod
    def _matches_query(finding: Finding, query: str) -> bool:
        haystack = " ".join(
            [
                finding.title,
                finding.summary,
                finding.affected_asset,
                finding.category,
                _enum_text(finding.severity),
                _enum_text(finding.confidence),
                _enum_text(finding.verification_status),
            ]
        ).lower()
        return query in haystack

    def _fill_table(self) -> None:
        self.table.clear()
        if not self._visible:
            placeholder = QTreeWidgetItem(["", "", "", "", self._empty_message()])
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            grey = QBrush(QColor("#64748b"))
            placeholder.setForeground(4, grey)
            self.table.addTopLevelItem(placeholder)
            self._show_detail(None)
            return
        for finding in self._visible:
            severity = _enum_text(finding.severity)
            item = QTreeWidgetItem(
                [
                    severity,
                    _enum_text(finding.confidence),
                    _enum_text(finding.verification_status),
                    finding.affected_asset,
                    finding.title,
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, finding.finding_id)
            item.setForeground(0, QBrush(QColor(_SEVERITY_COLORS.get(severity, "#f3f4f6"))))
            item.setForeground(
                2, QBrush(QColor(_STATUS_COLORS.get(_enum_text(finding.verification_status), "#8892b0")))
            )
            item.setFont(4, _bold_font())
            item.setToolTip(4, finding.summary)
            self.table.addTopLevelItem(item)
        if self.table.topLevelItemCount():
            self.table.setCurrentItem(self.table.topLevelItem(0))

    def _empty_message(self) -> str:
        if not self._findings:
            return "No findings recorded yet. Run an investigation to collect real findings."
        if self._filter_state == _CONFIRMED_FILTER:
            return "No confirmed findings yet. Confirmed findings are verified during the investigation."
        if self._filter_state == _POTENTIAL_FILTER:
            return "No potential findings. Every recorded finding is already verified."
        return "No findings match the current search."

    # --- detail pane ---------------------------------------------------------------------------------------
    def _show_detail(self, item: QTreeWidgetItem | None) -> None:
        finding = self._finding_for_item(item)
        if finding is None:
            self.detail_title.setText("Select a finding")
            self.detail_meta.setText("")
            self.detail_body.setHtml(
                "<p style='color:#64748b'>No finding selected. Findings appear here as the investigation "
                "records them.</p>"
            )
            return
        severity = _enum_text(finding.severity)
        self.detail_title.setText(finding.title)
        self.detail_meta.setText(
            f"Severity: {severity} - Confidence: {_enum_text(finding.confidence)} - "
            f"Status: {_enum_text(finding.verification_status)}\n"
            f"Asset: {finding.affected_asset}"
            + (f":{finding.affected_port}" if finding.affected_port else "")
            + f" - Category: {finding.category}"
            + f" - Detected by: {finding.detection_method}"
        )
        sections = [
            ("SUMMARY", finding.summary),
            ("REMEDIATION", finding.remediation_guidance or "No remediation guidance recorded."),
            ("IMPACT", finding.impact_statement or "No impact statement recorded."),
        ]
        html_parts: list[str] = []
        for heading, body in sections:
            safe_body = _escape(str(body))
            html_parts.append(
                f"<p style='color:#8892b0;font-size:10px;font-weight:700;letter-spacing:0.6px;"
                f"margin:6px 0 2px 0;'>{heading}</p>"
                f"<p style='color:#cbd5e1;font-size:12px;margin:0 0 8px 0;'>{safe_body}</p>"
            )
        self.detail_body.setHtml("".join(html_parts))

    def _finding_for_item(self, item: QTreeWidgetItem | None) -> Finding | None:
        if item is None:
            return None
        finding_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not finding_id:
            return None
        for finding in self._visible:
            if finding.finding_id == finding_id:
                return finding
        return None


def _bold_font() -> QFont:
    font = QFont()
    font.setBold(True)
    return font


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "<br>")
    )
