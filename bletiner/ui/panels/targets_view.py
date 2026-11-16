"""Target scope and authorization panel.

Everything on this page is real: validation is syntactic (domain/IP/URL), the authorized-scope list
is persisted through :class:`~bletiner.ui.settings_manager.SettingsManager`, and the enforcement
status is read from the actual Gate 3 live-certification report instead of claiming a gateway that
is not running. No demo targets are seeded.
"""

from __future__ import annotations

import logging
from typing import Any

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

from bletiner.agent.gate import DEFAULT_EVIDENCE_DIR, is_gate3_certified, latest_report
from bletiner.models.errors import ConfigurationError
from bletiner.ui import theme
from bletiner.ui.panels.validation import (
    target_in_authorized_scope,
    validate_scope_entry,
    validate_target,
)
from bletiner.ui.settings_manager import get_settings_manager

logger = logging.getLogger(__name__)

__all__ = ["TargetsPanel"]

_INPUT_QSS = (
    f"QLineEdit, QComboBox {{ background-color: {theme.BG_TERMINAL}; border: 1px solid "
    f"{theme.BORDER_CARD}; border-radius: 8px; color: {theme.TEXT_PRIMARY}; padding: 8px 12px; "
    "font-size: 13px; }"
    f"QLineEdit:focus, QComboBox:focus {{ border: 1px solid {theme.BORDER_FOCUS}; }}"
    "QComboBox::drop-down { border: none; width: 18px; }"
    f"QComboBox QAbstractItemView {{ background-color: {theme.BG_CARD_INNER}; border: 1px solid "
    f"{theme.BORDER_CARD}; color: {theme.TEXT_PRIMARY}; selection-background-color: {theme.BORDER_CARD}; }}"
)
_CARD_QSS = (
    f"QFrame#panelCard {{ background-color: {theme.BG_CARD}; border: 1px solid {theme.BORDER_CARD}; "
    "border-radius: 12px; }"
)
_PRIMARY_BTN_QSS = (
    "QPushButton { background-color: #059669; border: 1px solid #10b981; border-radius: 8px; "
    "color: #ffffff; font-weight: 700; font-size: 13px; padding: 9px 18px; }"
    "QPushButton:hover { background-color: #047857; }"
    "QPushButton:disabled { background-color: #1c2130; border-color: #2a3142; color: #64748b; }"
)
_SECONDARY_BTN_QSS = (
    f"QPushButton {{ background-color: #1a1e28; border: 1px solid #2a3142; border-radius: 8px; "
    f"color: {theme.TEXT_PRIMARY}; font-weight: 600; font-size: 12px; padding: 8px 14px; }}"
    "QPushButton:hover { background-color: #222838; border-color: #38bdf8; }"
    "QPushButton:disabled { color: #64748b; border-color: #1c2130; }"
)
_SCOPE_LIST_QSS = (
    f"QListWidget {{ background-color: {theme.BG_CARD_INNER}; border: 1px solid {theme.BORDER_CARD}; "
    f"border-radius: 8px; color: {theme.TEXT_PRIMARY}; padding: 6px; outline: 0; }}"
    f"QListWidget::item {{ padding: 4px 6px; border-radius: 4px; }}"
    f"QListWidget::item:selected {{ background-color: {theme.BORDER_CARD}; }}"
)


def _muted(text: str, *, size: int = 12) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: {size}px;")
    return label


class TargetsPanel(QWidget):
    """Configure the target specification, mode and persisted authorized-scope list."""

    target_selected = Signal(str, str)  # (cleaned target, mode)

    def __init__(self, parent: QWidget | None = None, controller: Any = None) -> None:
        super().__init__(parent)
        self.mgr = get_settings_manager()
        self._controller = controller
        self._scope: list[str] = list(self.mgr.settings.authorized_scope)
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # -- header ---------------------------------------------------------------------------------
        header = QHBoxLayout()
        title = QLabel("Targets & Authorization")
        title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 20px; font-weight: 800;")
        header.addWidget(title)
        header.addStretch(1)
        self.gate_pill = QLabel("Scope enforcement: checking")
        self.gate_pill.setStyleSheet(theme.pill_qss("gray"))
        header.addWidget(self.gate_pill)
        root.addLayout(header)

        root.addWidget(
            _muted(
                "Define the asset under test and the scope this workstation is authorized to touch. "
                "The scope list is persisted locally; enforcement status below reflects the real gate "
                "certification report, never a marketing claim.",
                size=12,
            )
        )

        root.addWidget(self._build_target_card())
        root.addWidget(self._build_scope_card())
        root.addStretch(1)

        self.refresh()

    # -- construction helpers -----------------------------------------------------------------------
    @staticmethod
    def _card() -> QFrame:
        card = QFrame()
        card.setObjectName("panelCard")
        card.setStyleSheet(_CARD_QSS)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        return card

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 14px; font-weight: 700;")
        return label

    def _build_target_card(self) -> QFrame:
        card = self._card()
        layout = card.layout()
        layout.addWidget(self._section_label("Target specification"))

        row = QHBoxLayout()
        row.setSpacing(10)
        self.target_input = QLineEdit()
        self.target_input.setPlaceholderText("app.internal.corp, 10.0.0.5 or https://app.internal.corp/path")
        self.target_input.setStyleSheet(_INPUT_QSS)
        self.target_input.setMinimumWidth(360)
        row.addWidget(self.target_input, 3)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Deep investigation", "deep")
        self.mode_combo.addItem("Fast discovery", "fast")
        self.mode_combo.setStyleSheet(_INPUT_QSS)
        self.mode_combo.setMinimumWidth(170)
        row.addWidget(self.mode_combo, 1)
        layout.addLayout(row)

        self.validation_lbl = QLabel("Enter a target to validate it.")
        self.validation_lbl.setWordWrap(True)
        self.validation_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(self.validation_lbl)

        self.auth_check = QCheckBox(
            "I confirm I have explicit written authorization to test this target and that it is in scope."
        )
        self.auth_check.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 12px;")
        layout.addWidget(self.auth_check)

        self.target_input.textChanged.connect(self._validate)
        self.mode_combo.currentIndexChanged.connect(self._validate)
        self.auth_check.toggled.connect(self._validate)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.btn_save = QPushButton("Save target")
        self.btn_save.setStyleSheet(_SECONDARY_BTN_QSS)
        self.btn_save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_save.clicked.connect(lambda: self._save_target(start=False))
        buttons.addWidget(self.btn_save)

        self.btn_start = QPushButton("Save and start investigation")
        self.btn_start.setStyleSheet(_PRIMARY_BTN_QSS)
        self.btn_start.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_start.clicked.connect(lambda: self._save_target(start=True))
        buttons.addWidget(self.btn_start)

        buttons.addStretch(1)
        self.action_status_lbl = QLabel("")
        self.action_status_lbl.setWordWrap(True)
        self.action_status_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        buttons.addWidget(self.action_status_lbl, 2)
        layout.addLayout(buttons)
        return card

    def _build_scope_card(self) -> QFrame:
        card = self._card()
        layout = card.layout()
        layout.addWidget(self._section_label("Authorized scope"))
        layout.addWidget(
            _muted(
                "Only targets listed here are authorized for this workstation. The list is stored in "
                "~/.bletiner/settings.json and survives restarts.",
                size=12,
            )
        )

        self.scope_list = QListWidget()
        self.scope_list.setStyleSheet(_SCOPE_LIST_QSS)
        self.scope_list.setFixedHeight(118)
        self.scope_list.itemSelectionChanged.connect(self._sync_scope_buttons)
        layout.addWidget(self.scope_list)

        add_row = QHBoxLayout()
        add_row.setSpacing(10)
        self.new_scope_input = QLineEdit()
        self.new_scope_input.setPlaceholderText(
            "Add a domain, IP address or CIDR (e.g. api.internal.corp or 10.0.0.0/24)"
        )
        self.new_scope_input.setStyleSheet(_INPUT_QSS)
        self.new_scope_input.returnPressed.connect(self._add_scope)
        add_row.addWidget(self.new_scope_input, 1)

        self.btn_add = QPushButton("Add")
        self.btn_add.setStyleSheet(_SECONDARY_BTN_QSS)
        self.btn_add.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_add.clicked.connect(self._add_scope)
        add_row.addWidget(self.btn_add)

        self.btn_remove = QPushButton("Remove selected")
        self.btn_remove.setStyleSheet(_SECONDARY_BTN_QSS)
        self.btn_remove.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_remove.clicked.connect(self._remove_scope)
        add_row.addWidget(self.btn_remove)
        layout.addLayout(add_row)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.scope_count_lbl = QLabel("")
        self.scope_count_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        footer.addWidget(self.scope_count_lbl)
        self.scope_status_lbl = QLabel("")
        self.scope_status_lbl.setWordWrap(True)
        self.scope_status_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        footer.addWidget(self.scope_status_lbl, 1)
        layout.addLayout(footer)

        self.gate_detail_lbl = QLabel("")
        self.gate_detail_lbl.setWordWrap(True)
        self.gate_detail_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(self.gate_detail_lbl)
        return card

    # -- validation / state -------------------------------------------------------------------------
    def _validate_target_spec(self, raw: str) -> tuple[str | None, str | None]:
        validator = (
            getattr(self._controller, "validate_target", None) if self._controller is not None else None
        )
        if callable(validator):
            try:
                cleaned, error = validator(raw)
            except Exception as exc:
                return None, f"Target validation failed: {exc}"
            cleaned_text = cleaned if isinstance(cleaned, str) and cleaned else None
            error_text = error if isinstance(error, str) and error else None
            if cleaned_text is None and error_text is None:
                error_text = "Invalid target."
            return cleaned_text, error_text
        return validate_target(raw)

    def _in_authorized_scope(self, cleaned: str) -> bool:
        checker = (
            getattr(self._controller, "target_in_authorized_scope", None)
            if self._controller is not None
            else None
        )
        if callable(checker):
            try:
                return bool(checker(cleaned, list(self._scope)))
            except Exception:
                logger.debug("controller scope check failed; using local scope check", exc_info=True)
        return target_in_authorized_scope(cleaned, self._scope)

    def _set_label(self, label: QLabel, text: str, color: str) -> None:
        label.setText(text)
        label.setStyleSheet(f"color: {color}; font-size: 12px;")

    def _validate(self) -> None:
        cleaned, error = self._validate_target_spec(self.target_input.text())
        if error is not None or cleaned is None:
            self._set_label(self.validation_lbl, f"✕ {error or 'Invalid target.'}", theme.RED_FG)
            in_scope = False
        else:
            in_scope = self._in_authorized_scope(cleaned)
            if not self._scope:
                self._set_label(
                    self.validation_lbl,
                    f"✓ Valid target: {cleaned}. The authorized scope list is empty - add it below before starting.",
                    theme.YELLOW_FG,
                )
            elif not in_scope:
                self._set_label(
                    self.validation_lbl,
                    f"✓ Valid target: {cleaned} - but it is NOT in the authorized scope list. Add it below; "
                    "Save & start stays disabled.",
                    theme.YELLOW_FG,
                )
            else:
                self._set_label(
                    self.validation_lbl,
                    f"✓ Valid target: {cleaned} (covered by the authorized scope)",
                    theme.GREEN_FG,
                )

        authorized = self.auth_check.isChecked()
        self.btn_save.setEnabled(bool(cleaned) and not self._busy)
        start_enabled = bool(cleaned) and in_scope and authorized and not self._busy
        self.btn_start.setEnabled(start_enabled)
        if cleaned and in_scope and not authorized:
            self._set_label(
                self.action_status_lbl,
                "Confirm the authorization checkbox to enable Save & start.",
                theme.YELLOW_FG,
            )
        elif not self._busy and self.action_status_lbl.text().startswith("Confirm the authorization"):
            self.action_status_lbl.setText("")

    # -- scope editing ------------------------------------------------------------------------------
    def _render_scope(self) -> None:
        self.scope_list.clear()
        for entry in self._scope:
            self.scope_list.addItem(QListWidgetItem(entry))
        if self._scope:
            self.scope_count_lbl.setText(
                f"{len(self._scope)} authorized entr" + ("y" if len(self._scope) == 1 else "ies")
            )
        else:
            self.scope_count_lbl.setText("No authorized entries")
        self._sync_scope_buttons()

    def _sync_scope_buttons(self) -> None:
        has_selection = bool(self.scope_list.selectedItems())
        self.btn_remove.setEnabled(has_selection and not self._busy)

    def _persist_scope(self) -> bool:
        self.mgr.settings.authorized_scope = list(self._scope)
        try:
            self.mgr.save(self.mgr.settings)
        except ConfigurationError as exc:
            self._set_label(self.scope_status_lbl, f"✕ {exc}", theme.RED_FG)
            return False
        return True

    def _add_scope(self) -> None:
        cleaned, error = validate_scope_entry(self.new_scope_input.text())
        if cleaned is None:
            self._set_label(self.scope_status_lbl, f"✕ {error or 'Invalid scope entry.'}", theme.RED_FG)
            return
        if cleaned.lower() in {existing.lower() for existing in self._scope}:
            self._set_label(
                self.scope_status_lbl, f"{cleaned} is already in the authorized scope.", theme.YELLOW_FG
            )
            return
        self._scope.append(cleaned)
        if not self._persist_scope():
            self._scope.remove(cleaned)
            return
        self.new_scope_input.clear()
        self._render_scope()
        self._set_label(self.scope_status_lbl, f"Added {cleaned} to the authorized scope.", theme.GREEN_FG)
        self._validate()

    def _remove_scope(self) -> None:
        selected = [item.text() for item in self.scope_list.selectedItems()]
        if not selected:
            self._set_label(self.scope_status_lbl, "Select a scope entry first.", theme.YELLOW_FG)
            return
        removed: list[str] = []
        for value in selected:
            for index, existing in enumerate(self._scope):
                if existing == value:
                    removed.append(self._scope.pop(index))
                    break
        if not self._persist_scope():
            self._scope.extend(removed)
            self._render_scope()
            return
        self._render_scope()
        self._set_label(
            self.scope_status_lbl,
            "Removed " + ", ".join(removed) + " from the authorized scope.",
            theme.GREEN_FG,
        )
        self._validate()

    # -- gate enforcement status ---------------------------------------------------------------------
    def _render_gate_status(self) -> None:
        certified = is_gate3_certified()
        report = latest_report()
        if certified:
            self.gate_pill.setText("Enforcement active")
            self.gate_pill.setStyleSheet(theme.pill_qss("green"))
            generated = str((report or {}).get("generated_at") or "")[:19].replace("T", " ")
            totals = (report or {}).get("totals") or {}
            passed = totals.get("pass") if isinstance(totals, dict) else None
            total = totals.get("total") if isinstance(totals, dict) else None
            self.gate_detail_lbl.setText(
                f"Gate 3 egress gateway: certified by a live report generated {generated} UTC "
                f"({passed}/{total} scenarios passed). The authorized scope is enforced by the certified gateway."
            )
            self.gate_detail_lbl.setStyleSheet(f"color: {theme.GREEN_FG}; font-size: 12px;")
            return
        self.gate_pill.setText("Enforcement advisory")
        self.gate_pill.setStyleSheet(theme.pill_qss("yellow"))
        evidence = DEFAULT_EVIDENCE_DIR
        if report is None:
            detail = f"no gate report found in {evidence}"
        else:
            detail = (
                f"latest report is mode={report.get('mode')!r}, gate_passed={report.get('gate_passed')!r} "
                f"(generated {str(report.get('generated_at') or 'unknown')[:19]})"
            )
        self.gate_detail_lbl.setText(
            f"Gate 3 egress gateway: NOT certified - {detail}. The authorized scope list is advisory "
            "until a live gate report passes; it is not enforced by a certified egress gateway."
        )
        self.gate_detail_lbl.setStyleSheet(f"color: {theme.YELLOW_FG}; font-size: 12px;")

    # -- actions ------------------------------------------------------------------------------------
    def _save_target(self, *, start: bool) -> None:
        if self._busy:
            return
        cleaned, error = self._validate_target_spec(self.target_input.text())
        if error is not None or cleaned is None:
            self._set_label(self.action_status_lbl, f"✕ {error or 'Invalid target.'}", theme.RED_FG)
            return
        if start:
            if not self.auth_check.isChecked():
                self._set_label(
                    self.action_status_lbl,
                    "✕ Confirm the authorization checkbox before starting.",
                    theme.RED_FG,
                )
                return
            if not self._in_authorized_scope(cleaned):
                self._set_label(
                    self.action_status_lbl,
                    f"✕ {cleaned} is not in the authorized scope list - add it first.",
                    theme.RED_FG,
                )
                return
        mode = str(self.mode_combo.currentData() or "deep")
        self.mgr.settings.default_target = cleaned
        self.mgr.settings.default_mode = mode
        try:
            self.mgr.save(self.mgr.settings)
        except ConfigurationError as exc:
            self._set_label(self.action_status_lbl, f"✕ {exc}", theme.RED_FG)
            return
        if start:
            self._set_label(
                self.action_status_lbl, f"✓ Starting {mode} investigation for {cleaned}.", theme.GREEN_FG
            )
            self.target_selected.emit(cleaned, mode)
        else:
            self._set_label(
                self.action_status_lbl,
                f"✓ Saved target {cleaned} ({mode}). Use 'Save and start investigation' to launch it.",
                theme.GREEN_FG,
            )

    # -- public surface (INTERFACES) -----------------------------------------------------------------
    def refresh(self) -> None:
        """Reload persisted settings/scope and the real gate certification status."""
        settings = self.mgr.settings
        self._scope = list(settings.authorized_scope)
        if not self.target_input.hasFocus():
            self.target_input.setText(settings.default_target)
        index = self.mode_combo.findData(settings.default_mode)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)
        self._render_scope()
        self._render_gate_status()
        self._validate()

    def focus_target_input(self) -> None:
        self.target_input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.target_input.selectAll()

    def set_busy(self, busy: bool) -> None:
        """Lock the target controls while an investigation is running."""
        self._busy = busy
        self.target_input.setEnabled(not busy)
        self.mode_combo.setEnabled(not busy)
        self.new_scope_input.setEnabled(not busy)
        self.scope_list.setEnabled(not busy)
        self.auth_check.setEnabled(not busy)
        self.btn_add.setEnabled(not busy)
        self.btn_save.setEnabled(not busy and bool(self._validate_target_spec(self.target_input.text())[0]))
        if busy:
            self.btn_start.setEnabled(False)
            self.btn_remove.setEnabled(False)
            self._set_label(
                self.action_status_lbl, "Investigation running - target controls are locked.", theme.YELLOW_FG
            )
        else:
            self._validate()
