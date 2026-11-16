"""Target Scope & Authorization Panel for Bletiner AI desktop."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bletiner.ui.settings_manager import get_settings_manager


class TargetsPanel(QWidget):
    """Full-page view to configure target scopes, CIDR rules, and authorization boundaries."""

    target_selected = Signal(str, str)  # (target, mode)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mgr = get_settings_manager()

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # Header
        header_box = QVBoxLayout()
        header_box.setSpacing(4)
        title = QLabel("🎯  Target Scope & Authorization")
        title.setStyleSheet("font-size: 20px; font-weight: 800; color: #ffffff;")
        subtitle = QLabel(
            "Configure target domain/IP boundaries. Bletiner AI's fail-closed gateway strictly enforces scope rules."
        )
        subtitle.setStyleSheet("font-size: 12px; color: #8892b0;")
        header_box.addWidget(title)
        header_box.addWidget(subtitle)
        root.addLayout(header_box)

        # Main Card
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background-color: #12151e; border: 1px solid #1c2130; border-radius: 12px; padding: 18px; }"
        )
        layout = QVBoxLayout(card)
        layout.setSpacing(14)

        # Target URL / Domain
        t_box = QVBoxLayout()
        t_box.setSpacing(6)
        t_lbl = QLabel("Primary Target Specification (URL, FQDN, or IPv4):")
        t_lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: #f3f4f6;")
        self.target_input = QLineEdit(self.mgr.settings.default_target)
        self.target_input.setPlaceholderText("https://example.com or 192.168.1.10")
        self.target_input.setStyleSheet(
            "background: #08090d; border: 1px solid #1e2434; border-radius: 8px; color: #f3f4f6; padding: 10px 14px; font-size: 14px;"
        )
        t_box.addWidget(t_lbl)
        t_box.addWidget(self.target_input)
        layout.addLayout(t_box)

        # Investigation Mode
        m_box = QVBoxLayout()
        m_box.setSpacing(6)
        m_lbl = QLabel("Investigation Discovery Mode:")
        m_lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: #f3f4f6;")
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Deep Investigation (Full service fingerprinting, web crawler, CVE checks)", "deep")
        self.mode_combo.addItem("Fast Discovery (Port scan, HTTP headers, banner grab)", "fast")
        self.mode_combo.setStyleSheet(
            "background: #08090d; border: 1px solid #1e2434; border-radius: 8px; color: #f3f4f6; padding: 8px 12px;"
        )
        m_box.addWidget(m_lbl)
        m_box.addWidget(self.mode_combo)
        layout.addLayout(m_box)

        # Scope list
        s_box = QVBoxLayout()
        s_box.setSpacing(6)
        s_lbl = QLabel("Authorized In-Scope Domains and Subnets:")
        s_lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: #f3f4f6;")
        self.scope_list = QListWidget()
        self.scope_list.setStyleSheet(
            "background: #08090d; border: 1px solid #1e2434; border-radius: 8px; color: #f3f4f6; padding: 6px;"
        )
        for s in self.mgr.settings.authorized_scope:
            self.scope_list.addItem(QListWidgetItem(f"✓  {s}"))
        self.scope_list.setFixedHeight(110)
        s_box.addWidget(s_lbl)
        s_box.addWidget(self.scope_list)

        # Add scope input
        add_row = QHBoxLayout()
        self.new_scope_input = QLineEdit()
        self.new_scope_input.setPlaceholderText("Add sub-domain or CIDR (e.g. api.example.com or 10.0.0.0/24)")
        self.new_scope_input.setStyleSheet(
            "background: #08090d; border: 1px solid #1e2434; border-radius: 8px; color: #f3f4f6; padding: 8px 12px;"
        )
        btn_add = QPushButton("+ Add Scope")
        btn_add.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_add.setStyleSheet(
            "QPushButton { background: #1a1e28; border: 1px solid #2a3142; border-radius: 8px; color: #f3f4f6; font-weight: 600; padding: 8px 16px; }"
            "QPushButton:hover { background: #222838; border-color: #38bdf8; }"
        )
        btn_add.clicked.connect(self._add_scope)
        add_row.addWidget(self.new_scope_input, 1)
        add_row.addWidget(btn_add)
        s_box.addLayout(add_row)
        layout.addLayout(s_box)

        # Mandatory authorization checkbox
        self.auth_check = QCheckBox("I verify that I have explicit authorization to perform security testing on this target.")
        self.auth_check.setChecked(True)
        self.auth_check.setStyleSheet("color: #10b981; font-weight: 600; font-size: 12px; margin-top: 6px;")
        layout.addWidget(self.auth_check)

        # Set Target and Launch Button
        btn_set = QPushButton("🚀  Set as Active Target & Launch Investigation")
        btn_set.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_set.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10b981, stop:1 #059669); border: 1px solid #10b981; border-radius: 8px; color: #ffffff; font-weight: 700; font-size: 13px; padding: 10px 24px; }"
            "QPushButton:hover { background: #059669; }"
        )
        btn_set.clicked.connect(self._apply_target)
        layout.addWidget(btn_set)

        root.addWidget(card)
        root.addStretch(1)

    def _add_scope(self) -> None:
        val = self.new_scope_input.text().strip()
        if val:
            self.scope_list.addItem(QListWidgetItem(f"✓  {val}"))
            self.mgr.settings.authorized_scope.append(val)
            self.mgr.save(self.mgr.settings)
            self.new_scope_input.clear()

    def _apply_target(self) -> None:
        target = self.target_input.text().strip()
        mode = self.mode_combo.currentData() or "deep"
        if target:
            self.mgr.settings.default_target = target
            self.mgr.settings.default_mode = mode
            self.mgr.save(self.mgr.settings)
            self.target_selected.emit(target, mode)
