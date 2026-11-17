"""Dialogs for the Bletiner AI desktop UI.

:class:`NewInvestigationDialog` collects the same real inputs the investigation actually uses:
target, mode, engine (local or AI), provider/model for the AI engine, and an explicit authorization
confirmation. It refuses to enable Start when the selected engine cannot really run (no API key, no
model, invalid target or out-of-scope target), instead of pretending to start.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QCheckBox,
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

from bletiner.providers.model_discovery import PROVIDERS
from bletiner.ui import theme
from bletiner.ui.panels.validation import validate_target
from bletiner.ui.settings_manager import get_settings_manager

__all__ = ["NewInvestigationDialog"]

_INPUT_QSS = (
    f"QLineEdit, QComboBox {{ background-color: {theme.BG_TERMINAL}; border: 1px solid "
    f"{theme.BORDER_CARD}; border-radius: 8px; color: {theme.TEXT_PRIMARY}; padding: 8px 12px; "
    "font-size: 13px; selection-background-color: #2563eb; }"
    f"QLineEdit:focus, QComboBox:focus {{ border: 1px solid {theme.BORDER_FOCUS}; }}"
    "QComboBox::drop-down { border: none; width: 18px; }"
    f"QComboBox QAbstractItemView {{ background-color: {theme.BG_CARD_INNER}; border: 1px solid "
    f"{theme.BORDER_CARD}; color: {theme.TEXT_PRIMARY}; selection-background-color: {theme.BORDER_CARD}; }}"
)
_DIALOG_QSS = (
    f"QDialog {{ background-color: {theme.BG_WINDOW}; }}"
    f"QLabel {{ color: {theme.TEXT_PRIMARY}; background: transparent; }}"
    f"QFrame#dialogCard {{ background-color: {theme.BG_CARD}; border: 1px solid {theme.BORDER_CARD}; "
    "border-radius: 12px; }"
)
_CANCEL_BTN_QSS = (
    f"QPushButton {{ background-color: #1a1e28; border: 1px solid #2a3142; border-radius: 8px; "
    f"color: {theme.TEXT_SECONDARY}; font-weight: 600; font-size: 12px; padding: 8px 18px; }}"
    "QPushButton:hover { background-color: #222838; color: #f3f4f6; }"
)
_START_BTN_QSS = (
    "QPushButton { background-color: #059669; border: 1px solid #10b981; border-radius: 8px; "
    "color: #ffffff; font-weight: 700; font-size: 12px; padding: 9px 20px; }"
    "QPushButton:hover { background-color: #047857; }"
    "QPushButton:disabled { background-color: #1c2130; border-color: #2a3142; color: #64748b; }"
)


class NewInvestigationDialog(QDialog):
    """Modal used by the top bar / Ctrl+N to configure a real investigation."""

    def __init__(
        self,
        target: str = "",
        mode: str = "deep",
        engine: str = "local",
        provider: str = "",
        model: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.mgr = get_settings_manager()
        self.setWindowTitle("New investigation")
        self.setMinimumWidth(600)
        self.setModal(True)
        self.setStyleSheet(_DIALOG_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)

        title = QLabel("New investigation")
        title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 18px; font-weight: 800;")
        layout.addWidget(title)
        subtitle = QLabel(
            "Configure the target and engine. Local runs real recon tools inside the Kali sandbox; "
            "the AI engine additionally needs a configured provider, model and API key."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(subtitle)

        card = QFrame()
        card.setObjectName("dialogCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(10)
        layout.addWidget(card)

        # Target
        card_layout.addWidget(self._field_label("Target domain / IP / URL"))
        self.target_input = QLineEdit()
        self.target_input.setStyleSheet(_INPUT_QSS)
        self.target_input.setPlaceholderText("app.internal.corp, 10.0.0.5 or https://app.internal.corp/path")
        self.target_input.setText(target)
        self.target_input.textChanged.connect(self._refresh_state)
        card_layout.addWidget(self.target_input)
        self.target_error_lbl = QLabel("")
        self.target_error_lbl.setWordWrap(True)
        self.target_error_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        card_layout.addWidget(self.target_error_lbl)

        # Mode + engine row
        mode_row = QHBoxLayout()
        mode_row.setSpacing(12)
        mode_col = QVBoxLayout()
        mode_col.setSpacing(4)
        mode_col.addWidget(self._field_label("Mode"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Deep investigation", "deep")
        self.mode_combo.addItem("Fast discovery", "fast")
        self.mode_combo.setStyleSheet(_INPUT_QSS)
        index = self.mode_combo.findData(mode)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)
        self.mode_combo.currentIndexChanged.connect(self._refresh_state)
        mode_col.addWidget(self.mode_combo)
        mode_row.addLayout(mode_col, 1)

        engine_col = QVBoxLayout()
        engine_col.setSpacing(4)
        engine_col.addWidget(self._field_label("Engine"))
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("Local - deterministic recon tools in Kali (no API key)", "local")
        self.engine_combo.addItem("AI - autonomous agent via provider", "ai")
        self.engine_combo.setStyleSheet(_INPUT_QSS)
        engine_index = self.engine_combo.findData(engine)
        if engine_index >= 0:
            self.engine_combo.setCurrentIndex(engine_index)
        self.engine_combo.currentIndexChanged.connect(self._refresh_state)
        engine_col.addWidget(self.engine_combo)
        mode_row.addLayout(engine_col, 1)
        card_layout.addLayout(mode_row)

        # AI options (provider + model)
        self.ai_box = QFrame()
        self.ai_box.setStyleSheet("QFrame { background: transparent; }")
        ai_layout = QVBoxLayout(self.ai_box)
        ai_layout.setContentsMargins(0, 6, 0, 0)
        ai_layout.setSpacing(6)
        ai_row = QHBoxLayout()
        ai_row.setSpacing(12)
        provider_col = QVBoxLayout()
        provider_col.setSpacing(4)
        provider_col.addWidget(self._field_label("Provider"))
        self.provider_combo = QComboBox()
        for key, label in PROVIDERS.items():
            self.provider_combo.addItem(label, key)
        self.provider_combo.setStyleSheet(_INPUT_QSS)
        selected_provider = (provider or self.mgr.settings.active_provider).strip().lower()
        provider_index = self.provider_combo.findData(selected_provider)
        if provider_index >= 0:
            self.provider_combo.setCurrentIndex(provider_index)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_col.addWidget(self.provider_combo)
        ai_row.addLayout(provider_col, 1)

        model_col = QVBoxLayout()
        model_col.setSpacing(4)
        model_col.addWidget(self._field_label("Model id"))
        self.model_input = QLineEdit()
        self.model_input.setStyleSheet(_INPUT_QSS)
        self.model_input.setPlaceholderText("Discovered model id, e.g. deepseek-chat")
        self.model_input.setText(model or "")
        self.model_input.textChanged.connect(self._refresh_state)
        model_col.addWidget(self.model_input)
        ai_row.addLayout(model_col, 1)
        ai_layout.addLayout(ai_row)

        self.ai_status_lbl = QLabel("")
        self.ai_status_lbl.setWordWrap(True)
        self.ai_status_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        ai_layout.addWidget(self.ai_status_lbl)
        card_layout.addWidget(self.ai_box)

        self.local_hint_lbl = QLabel(
            "Local engine: real nmap/nikto/whatweb tooling runs inside the 'my-kali' container. "
            "No AI provider or API key is used."
        )
        self.local_hint_lbl.setWordWrap(True)
        self.local_hint_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        card_layout.addWidget(self.local_hint_lbl)

        # Authorization
        self.auth_check = QCheckBox(
            "I confirm I have explicit written authorization to test this target and that it is in scope."
        )
        self.auth_check.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 12px;")
        self.auth_check.toggled.connect(self._refresh_state)
        card_layout.addWidget(self.auth_check)

        self.feedback_lbl = QLabel("")
        self.feedback_lbl.setWordWrap(True)
        self.feedback_lbl.setStyleSheet(f"color: {theme.YELLOW_FG}; font-size: 12px;")
        card_layout.addWidget(self.feedback_lbl)

        # Buttons pinned to the bottom; the stretch absorbs spare dialog height.
        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet(_CANCEL_BTN_QSS)
        self.btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_cancel)
        self.btn_start = QPushButton("Start investigation")
        self.btn_start.setStyleSheet(_START_BTN_QSS)
        self.btn_start.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_start.clicked.connect(self._on_start_clicked)
        buttons.addWidget(self.btn_start)
        layout.addLayout(buttons)

        self._apply_engine_visibility()
        self._refresh_state()
        self.target_input.setFocus()

    # -- construction helpers -----------------------------------------------------------------------
    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 11px; font-weight: 600;")
        return label

    def _apply_engine_visibility(self) -> None:
        is_ai = self.engine() == "ai"
        self.ai_box.setVisible(is_ai)
        self.local_hint_lbl.setVisible(not is_ai)

    def _on_provider_changed(self) -> None:
        provider = self.provider()
        if not self.model_input.text().strip() and self.mgr.settings.active_provider == provider:
            self.model_input.setText(self.mgr.settings.active_model)
        self._refresh_state()

    # -- readiness ----------------------------------------------------------------------------------
    def _validate_target(self) -> tuple[str | None, str | None]:
        return validate_target(self.target_input.text())

    def _refresh_state(self) -> None:
        self._apply_engine_visibility()
        cleaned, error = self._validate_target()
        if error is not None or cleaned is None:
            self.target_error_lbl.setText(f"✕ {error or 'Invalid target.'}")
            self.target_error_lbl.setStyleSheet(f"color: {theme.RED_FG}; font-size: 12px;")
        else:
            self.target_error_lbl.setText(f"✓ {cleaned}")
            self.target_error_lbl.setStyleSheet(f"color: {theme.GREEN_FG}; font-size: 12px;")

        engine = self.engine()
        ai_ready = True
        if engine == "ai":
            provider = self.provider()
            model = self.model_input.text().strip()
            ai_ready, hint = self.mgr.provider_model_ready(provider, model)
            color = theme.GREEN_FG if ai_ready else theme.YELLOW_FG
            self.ai_status_lbl.setText(hint)
            self.ai_status_lbl.setStyleSheet(f"color: {color}; font-size: 12px;")

        target_ok = cleaned is not None and error is None
        authorized = self.auth_check.isChecked()
        self.btn_start.setEnabled(target_ok and authorized and ai_ready)
        if not target_ok:
            self.feedback_lbl.setText("Enter a valid target to continue.")
        elif not authorized:
            self.feedback_lbl.setText("Confirm the authorization checkbox to enable Start.")
        elif not ai_ready:
            self.feedback_lbl.setText(
                "AI engine is not ready: configure the provider key and model in Models/Settings, "
                "or switch to the Local engine."
            )
        else:
            self.feedback_lbl.setText("")

    # -- actions ------------------------------------------------------------------------------------
    def _on_start_clicked(self) -> None:
        cleaned, error = self._validate_target()
        if error is not None or cleaned is None:
            self.target_error_lbl.setText(f"✕ {error or 'Invalid target.'}")
            return
        if not self.auth_check.isChecked():
            self.feedback_lbl.setText("Confirm the authorization checkbox before starting.")
            return
        if self.engine() == "ai":
            ready, hint = self.mgr.provider_model_ready(self.provider(), self.model())
            if not ready:
                self.ai_status_lbl.setText(hint)
                return
        self.accept()

    # -- accessors (INTERFACES) ---------------------------------------------------------------------
    def target(self) -> str:
        return self.target_input.text().strip()

    def mode(self) -> str:
        return str(self.mode_combo.currentData() or "deep")

    def engine(self) -> str:
        return str(self.engine_combo.currentData() or "local")

    def provider(self) -> str:
        if self.engine() != "ai":
            return ""
        return str(self.provider_combo.currentData() or "")

    def model(self) -> str:
        if self.engine() != "ai":
            return ""
        return self.model_input.text().strip()
