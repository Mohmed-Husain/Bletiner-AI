"""Investigation Sessions and Audit History Panel for Bletiner AI desktop."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class SessionCard(QFrame):
    """Card representing a past or current investigation session with report links."""

    def __init__(self, session_dir: Path) -> None:
        super().__init__()
        self.session_dir = session_dir
        self.setStyleSheet(
            "QFrame { background-color: #12151e; border: 1px solid #1c2130; border-radius: 12px; padding: 14px; }"
            "QFrame:hover { border-color: #2a3346; }"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Session data
        session_file = session_dir / "session.json"
        findings_file = session_dir / "evidence" / "findings.json"
        reports_dir = session_dir / "reports"

        target_name = session_dir.name
        timestamp_str = "Recent"
        findings_count = 0
        status_text = "Completed"

        if session_file.exists():
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                target_name = data.get("target", target_name)
                timestamp_str = data.get("completed_at", data.get("created_at", timestamp_str))
                status_text = data.get("stop_reason", status_text)
            except Exception:
                pass

        if findings_file.exists():
            try:
                findings_data = json.loads(findings_file.read_text(encoding="utf-8"))
                findings_count = len(findings_data) if isinstance(findings_data, list) else 0
            except Exception:
                pass

        # Top row: Target & Status
        top_row = QHBoxLayout()
        t_lbl = QLabel(f"🌐  {target_name}")
        t_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #38bdf8;")
        top_row.addWidget(t_lbl)
        top_row.addStretch(1)

        f_pill = QLabel(f"★ {findings_count} Findings")
        f_pill.setStyleSheet(
            "background: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid #f59e0b; border-radius: 10px; padding: 2px 10px; font-size: 11px; font-weight: 600;"
        )
        top_row.addWidget(f_pill)

        layout.addLayout(top_row)

        # Info line
        info_lbl = QLabel(f"Session: {session_dir.name[:8]}...  |  Status: {status_text}  |  Date: {timestamp_str}")
        info_lbl.setStyleSheet("font-size: 11px; color: #8892b0;")
        layout.addWidget(info_lbl)

        # Action buttons row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        html_path = reports_dir / "report.html"
        if html_path.exists():
            btn_html = QPushButton("🌐  Open HTML Report")
            btn_html.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_html.setStyleSheet(
                "QPushButton { background: rgba(56, 189, 248, 0.12); border: 1px solid #38bdf8; border-radius: 6px; color: #38bdf8; font-weight: 600; padding: 6px 14px; font-size: 12px; }"
                "QPushButton:hover { background: rgba(56, 189, 248, 0.22); }"
            )
            btn_html.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(html_path))))
            btn_row.addWidget(btn_html)

        md_path = reports_dir / "report.md"
        if md_path.exists():
            btn_md = QPushButton("📄  Markdown")
            btn_md.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_md.setStyleSheet(
                "QPushButton { background: #1a1e28; border: 1px solid #2a3142; border-radius: 6px; color: #f3f4f6; font-weight: 600; padding: 6px 12px; font-size: 12px; }"
                "QPushButton:hover { background: #222838; }"
            )
            btn_md.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(md_path))))
            btn_row.addWidget(btn_md)

        btn_folder = QPushButton("📁  Evidence Folder")
        btn_folder.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_folder.setStyleSheet(
            "QPushButton { background: #1a1e28; border: 1px solid #2a3142; border-radius: 6px; color: #8892b0; font-weight: 600; padding: 6px 12px; font-size: 12px; }"
            "QPushButton:hover { background: #222838; color: #f3f4f6; }"
        )
        btn_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(session_dir))))
        btn_row.addWidget(btn_folder)

        btn_row.addStretch(1)
        layout.addLayout(btn_row)


class SessionsPanel(QWidget):
    """Full-page view listing all past investigation sessions and report artifacts."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.investigations_dir = Path.home() / ".bletiner" / "investigations"

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # Header
        header_box = QVBoxLayout()
        header_box.setSpacing(4)
        title = QLabel("📁  Investigation Sessions & Reports")
        title.setStyleSheet("font-size: 20px; font-weight: 800; color: #ffffff;")
        subtitle = QLabel("Browse historical scan records, inspect verified findings, and launch generated HTML reports.")
        subtitle.setStyleSheet("font-size: 12px; color: #8892b0;")
        header_box.addWidget(title)
        header_box.addWidget(subtitle)
        root.addLayout(header_box)

        # Scroll area for session cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet("background: transparent;")
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(12)

        scroll.setWidget(self.cards_container)
        root.addWidget(scroll, 1)

        self.refresh_sessions()

    def refresh(self) -> None:
        self.refresh_sessions()

    def refresh_sessions(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.investigations_dir.exists():
            empty = QLabel("No past investigations found. Run a scan to generate session reports.")
            empty.setStyleSheet("color: #64748b; font-size: 13px; padding: 20px;")
            self.cards_layout.addWidget(empty)
            return

        sessions = sorted(self.investigations_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        if not sessions:
            empty = QLabel("No past investigations found. Run a scan to generate session reports.")
            empty.setStyleSheet("color: #64748b; font-size: 13px; padding: 20px;")
            self.cards_layout.addWidget(empty)
            return

        for sdir in sessions:
            if sdir.is_dir():
                card = SessionCard(sdir)
                self.cards_layout.addWidget(card)

        self.cards_layout.addStretch(1)
