"""Dialogs for Bletiner AI desktop UI."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class NewInvestigationDialog(QDialog):
    """Clean dark-themed modal to configure and launch a new investigation."""

    def __init__(
        self,
        parent: QWidget | None = None,
        initial_target: str = "example.com",
        initial_mode: str = "deep",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Investigation")
        self.setMinimumWidth(440)
        self.setStyleSheet(
            """
            QDialog {
                background-color: #12151e;
                border: 1px solid #1c2130;
                border-radius: 12px;
            }
            QLabel {
                color: #f3f4f6;
                background: transparent;
            }
            QLineEdit, QComboBox {
                background-color: #08090d;
                border: 1px solid #1e2434;
                border-radius: 8px;
                color: #f3f4f6;
                padding: 8px 12px;
                font-size: 13px;
                selection-background-color: #2563eb;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #38bdf8;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #0d0f16;
                border: 1px solid #1c2130;
                color: #f3f4f6;
                selection-background-color: #1a2030;
            }
            QPushButton#btnCancel {
                background-color: #1a1e28;
                border: 1px solid #2a3142;
                border-radius: 8px;
                color: #8892b0;
                font-weight: 600;
                font-size: 12px;
                padding: 8px 18px;
            }
            QPushButton#btnCancel:hover {
                background-color: #222838;
                color: #f3f4f6;
            }
            QPushButton#btnStart {
                background-color: #2563eb;
                border: 1px solid #3b82f6;
                border-radius: 8px;
                color: #ffffff;
                font-weight: 700;
                font-size: 12px;
                padding: 8px 20px;
            }
            QPushButton#btnStart:hover {
                background-color: #1d4ed8;
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # Header
        header_box = QVBoxLayout()
        header_box.setSpacing(4)
        title = QLabel("\U0001f6e1  New Investigation")
        title.setStyleSheet("font-size: 16px; font-weight: 800; color: #ffffff;")
        subtitle = QLabel("Set target URL/domain and discovery mode.")
        subtitle.setStyleSheet("font-size: 11px; color: #8892b0;")
        header_box.addWidget(title)
        header_box.addWidget(subtitle)
        layout.addLayout(header_box)

        # Separator line
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #1c2130; background: #1c2130; max-height: 1px;")
        layout.addWidget(sep)

        # Target input
        t_box = QVBoxLayout()
        t_box.setSpacing(6)
        t_lbl = QLabel("Target Asset (Domain / URL / IP)")
        t_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #94a3b8;")
        self.target_input = QLineEdit()
        self.target_input.setObjectName("inputTarget")
        self.target_input.setPlaceholderText("e.g. example.com or 192.168.1.100")
        self.target_input.setText(initial_target)
        t_box.addWidget(t_lbl)
        t_box.addWidget(self.target_input)
        layout.addLayout(t_box)

        # Mode selection
        m_box = QVBoxLayout()
        m_box.setSpacing(6)
        m_lbl = QLabel("Investigation Mode")
        m_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #94a3b8;")
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("comboMode")
        self.mode_combo.addItem("Deep — Full autonomous investigation & verification", "deep")
        self.mode_combo.addItem("Fast — Rapid recon & service discovery", "fast")
        if initial_mode.lower() == "fast":
            self.mode_combo.setCurrentIndex(1)
        else:
            self.mode_combo.setCurrentIndex(0)
        m_box.addWidget(m_lbl)
        m_box.addWidget(self.mode_combo)
        layout.addLayout(m_box)

        layout.addSpacing(8)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch(1)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("btnCancel")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_start = QPushButton("\u25b6  Start Investigation")
        self.btn_start.setObjectName("btnStart")
        self.btn_start.clicked.connect(self.accept)

        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_start)
        layout.addLayout(btn_row)

    @property
    def target(self) -> str:
        return self.target_input.text().strip()

    @property
    def mode(self) -> str:
        return str(self.mode_combo.currentData())
