"""Settings and diagnostics panel: honest credential storage + real Docker/gate diagnostics.

The panel never claims an "OS keyring" unless that is the backend actually in use: the name comes
from :class:`~bletiner.services.secrets.SecretStore.backend` ("OS keyring", "Windows DPAPI" or
"Memory only"). Docker and gate checks run off the GUI thread and every failure is shown inline.
"""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bletiner.agent.gate import DEFAULT_EVIDENCE_DIR, is_gate3_certified, latest_report
from bletiner.models.errors import ConfigurationError, ProviderError
from bletiner.providers.model_discovery import PROVIDERS
from bletiner.services.secrets import (
    BACKEND_DPAPI,
    BACKEND_KEYRING,
    BACKEND_MEMORY,
    SecretStorageError,
)
from bletiner.ui import theme
from bletiner.ui.panels.jobs import run_async
from bletiner.ui.settings_manager import get_settings_manager

logger = logging.getLogger(__name__)

__all__ = ["SettingsPanel"]

_CREDENTIAL_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("openai", "OpenAI"),
    ("deepseek", "DeepSeek"),
    ("anthropic", "Anthropic"),
    ("custom", "Custom endpoint (optional key)"),
)
_KEY_PLACEHOLDERS = {
    "openai": "Paste an OpenAI API key (sk-...) to store it",
    "deepseek": "Paste a DeepSeek API key (sk-...) to store it",
    "anthropic": "Paste an Anthropic API key (sk-ant-...) to store it",
    "custom": "Optional API key for the custom endpoint",
}

_INPUT_QSS = (
    f"QLineEdit {{ background-color: {theme.BG_TERMINAL}; border: 1px solid {theme.BORDER_CARD}; "
    f"border-radius: 8px; color: {theme.TEXT_PRIMARY}; padding: 8px 12px; font-size: 12px; }}"
    f"QLineEdit:focus {{ border: 1px solid {theme.BORDER_FOCUS}; }}"
)
_CARD_QSS = (
    f"QFrame#panelCard {{ background-color: {theme.BG_CARD}; border: 1px solid {theme.BORDER_CARD}; "
    "border-radius: 12px; }"
)
_SECONDARY_BTN_QSS = (
    f"QPushButton {{ background-color: #1a1e28; border: 1px solid #2a3142; border-radius: 8px; "
    f"color: {theme.ACCENT_BLUE}; font-weight: 600; font-size: 12px; padding: 7px 14px; }}"
    "QPushButton:hover { background-color: #222838; border-color: #38bdf8; }"
    "QPushButton:disabled { color: #64748b; border-color: #1c2130; }"
)
_PRIMARY_BTN_QSS = (
    "QPushButton { background-color: #2563eb; border: 1px solid #3b82f6; border-radius: 8px; "
    "color: #ffffff; font-weight: 700; font-size: 12px; padding: 8px 18px; }"
    "QPushButton:hover { background-color: #1d4ed8; }"
    "QPushButton:disabled { background-color: #1c2130; border-color: #2a3142; color: #64748b; }"
)


def _muted(text: str, *, size: int = 12) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: {size}px;")
    return label


class SettingsPanel(QWidget):
    """Credentials, secret-backend honesty and live workstation diagnostics."""

    settings_updated = Signal()

    def __init__(self, parent: QWidget | None = None, controller: Any = None) -> None:
        super().__init__(parent)
        self.mgr = get_settings_manager()
        self._controller = controller
        self._key_rows: dict[str, dict[str, Any]] = {}
        self._diag_labels: dict[str, QLabel] = {}
        self._diag_busy = False
        self._diagnostics_checked = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel("Settings & Diagnostics")
        title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 20px; font-weight: 800;")
        root.addWidget(title)
        root.addWidget(
            _muted(
                "Provider credentials, the actual secret-storage backend in use, and the real runtime "
                "state of the Docker sandbox and Gate 3 certification.",
            )
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)
        content_layout.addWidget(self._build_credentials_card())
        content_layout.addWidget(self._build_diagnostics_card())
        content_layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # Static state is honest without any I/O; the Docker check only runs when the page is really
        # shown (showEvent) or refreshed, so constructing a window in tests never touches Docker.
        self._refresh_key_statuses()
        self._refresh_base_url_fields()
        self._render_gate()

    # -- construction -------------------------------------------------------------------------------
    @staticmethod
    def _card() -> QFrame:
        card = QFrame()
        card.setObjectName("panelCard")
        card.setStyleSheet(_CARD_QSS)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        return card

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 14px; font-weight: 700;")
        return label

    def _build_credentials_card(self) -> QFrame:
        card = self._card()
        layout = card.layout()
        layout.addWidget(self._section_label("Provider API credentials"))
        self.storage_lbl = QLabel("")
        self.storage_lbl.setWordWrap(True)
        self.storage_lbl.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.storage_lbl)

        for provider, label in _CREDENTIAL_PROVIDERS:
            self._build_key_row(layout, provider, label)
            if provider == "custom":
                self.custom_url_input = self._build_url_row(
                    layout,
                    "Custom endpoint base URL (OpenAI-compatible, e.g. http://localhost:8080/v1)",
                )

        layout.addWidget(self._section_label("Local model provider"))
        self.ollama_url_input = self._build_url_row(
            layout, "Ollama base URL (local daemon; no API key required)"
        )
        ollama_row = QHBoxLayout()
        self.btn_test_ollama = QPushButton("Test Ollama daemon")
        self.btn_test_ollama.setStyleSheet(_SECONDARY_BTN_QSS)
        self.btn_test_ollama.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_test_ollama.clicked.connect(self._test_ollama)
        ollama_row.addWidget(self.btn_test_ollama)
        self.ollama_status_lbl = QLabel("")
        self.ollama_status_lbl.setWordWrap(True)
        self.ollama_status_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        ollama_row.addWidget(self.ollama_status_lbl, 1)
        layout.addLayout(ollama_row)

        save_row = QHBoxLayout()
        self.btn_save = QPushButton("Save credentials")
        self.btn_save.setStyleSheet(_PRIMARY_BTN_QSS)
        self.btn_save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_save.clicked.connect(self._save_credentials)
        save_row.addWidget(self.btn_save)
        self.save_status_lbl = QLabel("")
        self.save_status_lbl.setWordWrap(True)
        self.save_status_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        save_row.addWidget(self.save_status_lbl, 1)
        layout.addLayout(save_row)
        return card

    def _build_key_row(self, layout: QVBoxLayout, provider: str, label: str) -> None:
        card = QFrame()
        card.setStyleSheet("QFrame { background: transparent; }")
        box = QVBoxLayout(card)
        box.setContentsMargins(0, 4, 0, 4)
        box.setSpacing(4)

        top = QHBoxLayout()
        name_lbl = QLabel(label)
        name_lbl.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 12px; font-weight: 600;")
        top.addWidget(name_lbl)
        status_lbl = QLabel("")
        status_lbl.setWordWrap(True)
        status_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 11px;")
        top.addWidget(status_lbl, 1)
        box.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(8)
        key_input = QLineEdit()
        key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_input.setPlaceholderText(_KEY_PLACEHOLDERS[provider])
        key_input.setStyleSheet(_INPUT_QSS)
        row.addWidget(key_input, 1)
        btn_test = QPushButton("Test")
        btn_test.setStyleSheet(_SECONDARY_BTN_QSS)
        btn_test.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_test.clicked.connect(lambda _checked=False, p=provider: self._test_key(p))
        row.addWidget(btn_test)
        btn_clear = QPushButton("Clear")
        btn_clear.setStyleSheet(_SECONDARY_BTN_QSS)
        btn_clear.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_clear.clicked.connect(lambda _checked=False, p=provider: self._clear_key(p))
        row.addWidget(btn_clear)
        box.addLayout(row)

        layout.addWidget(card)
        self._key_rows[provider] = {
            "input": key_input,
            "status": status_lbl,
            "test": btn_test,
            "clear": btn_clear,
        }

    def _build_url_row(self, layout: QVBoxLayout, label: str) -> QLineEdit:
        box = QVBoxLayout()
        box.setSpacing(4)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 12px; font-weight: 600;")
        box.addWidget(lbl)
        field = QLineEdit()
        field.setStyleSheet(_INPUT_QSS)
        box.addWidget(field)
        layout.addLayout(box)
        return field

    def _build_diagnostics_card(self) -> QFrame:
        card = self._card()
        layout = card.layout()
        layout.addWidget(self._section_label("Runtime diagnostics"))

        for key, label in (
            ("daemon", "Docker daemon"),
            ("container", "Kali sandbox container"),
            ("gate", "Gate 3 egress certification"),
            ("evidence", "Gate evidence directory"),
        ):
            row = QHBoxLayout()
            row.setSpacing(12)
            title = QLabel(label)
            title.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px; font-weight: 600;")
            title.setMinimumWidth(200)
            row.addWidget(title)
            value = QLabel("Not checked yet - press 'Re-run diagnostics'.")
            value.setWordWrap(True)
            value.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
            row.addWidget(value, 1)
            layout.addLayout(row)
            self._diag_labels[key] = value

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.btn_refresh_diag = QPushButton("Re-run diagnostics")
        self.btn_refresh_diag.setStyleSheet(_SECONDARY_BTN_QSS)
        self.btn_refresh_diag.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_refresh_diag.clicked.connect(self.refresh_diagnostics)
        buttons.addWidget(self.btn_refresh_diag)

        self.btn_open_data = QPushButton("Open data folder")
        self.btn_open_data.setStyleSheet(_SECONDARY_BTN_QSS)
        self.btn_open_data.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_open_data.clicked.connect(self._open_data_folder)
        buttons.addWidget(self.btn_open_data)
        buttons.addStretch(1)
        self.diag_status_lbl = QLabel("")
        self.diag_status_lbl.setWordWrap(True)
        self.diag_status_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        buttons.addWidget(self.diag_status_lbl, 1)
        layout.addLayout(buttons)
        return card

    # -- credential status --------------------------------------------------------------------------
    def _set_key_status(self, provider: str, text: str, color: str) -> None:
        row = self._key_rows.get(provider)
        if row is None:
            return
        row["status"].setText(text)
        row["status"].setStyleSheet(f"color: {color}; font-size: 11px;")

    def _refresh_key_statuses(self) -> None:
        for provider in self._key_rows:
            configured, description = self.mgr.api_key_status(provider)
            if configured:
                self._set_key_status(provider, f"● {description}", theme.GREEN_FG)
            elif provider == "custom":
                self._set_key_status(provider, f"○ optional - {description}", theme.TEXT_MUTED)
            else:
                self._set_key_status(provider, f"○ {description}", theme.YELLOW_FG)
        backend = self.mgr.secrets.backend
        if backend == BACKEND_KEYRING:
            color = theme.GREEN_FG
        elif backend == BACKEND_DPAPI:
            color = theme.ACCENT_BLUE
        else:
            color = theme.YELLOW_FG
        self.storage_lbl.setText(
            f"<span style='color:{color}; font-weight:700;'>Secret storage backend: {html.escape(backend)}</span>"
            f" <span style='color:{theme.TEXT_SECONDARY};'>- {html.escape(self.mgr.secrets.describe())}</span>"
        )
        if backend == BACKEND_MEMORY:
            self.storage_lbl.setText(
                f"<span style='color:{theme.YELLOW_FG}; font-weight:700;'>Secret storage backend: "
                f"{html.escape(backend)}</span> <span style='color:{theme.YELLOW_FG};'>- keys are kept in "
                "memory only and are lost when the app exits. No persistent OS secret backend is available "
                "on this runtime.</span>"
            )

    def _save_credentials(self) -> None:
        errors: list[str] = []
        saved = 0
        for provider, row in self._key_rows.items():
            key = row["input"].text().strip()
            if not key:
                continue
            try:
                self.mgr.set_api_key(provider, key)
            except (ProviderError, SecretStorageError) as exc:
                errors.append(f"{PROVIDERS.get(provider, provider)}: {exc}")
                continue
            row["input"].clear()
            saved += 1
        self.mgr.settings.ollama_base_url = (
            self.ollama_url_input.text().strip() or self.mgr.settings.ollama_base_url
        )
        self.mgr.settings.custom_base_url = self.custom_url_input.text().strip()
        try:
            self.mgr.save(self.mgr.settings)
        except ConfigurationError as exc:
            errors.append(str(exc))
        self._refresh_key_statuses()
        self._refresh_base_url_fields()
        if errors:
            self._set_save_status("✕ " + " | ".join(errors), theme.RED_FG)
            return
        self._set_save_status(
            f"✓ Saved settings. New keys: {saved}. Secret backend: {self.mgr.secrets.backend}.",
            theme.GREEN_FG,
        )
        self.settings_updated.emit()

    def _set_save_status(self, text: str, color: str) -> None:
        self.save_status_lbl.setText(text)
        self.save_status_lbl.setStyleSheet(f"color: {color}; font-size: 12px;")

    def _test_key(self, provider: str) -> None:
        row = self._key_rows.get(provider)
        if row is None:
            return
        typed = row["input"].text().strip()
        key = typed or self.mgr.get_api_key(provider)
        if not key:
            self._set_key_status(provider, "○ no key to test - enter or save a key first.", theme.YELLOW_FG)
            return
        row["test"].setEnabled(False)
        row["test"].setText("Testing...")
        self._set_key_status(provider, "◌ sending a real minimal request...", theme.ACCENT_BLUE)

        def done(result: tuple[bool, str] | None, error: Exception | None) -> None:
            row["test"].setEnabled(True)
            row["test"].setText("Test")
            if error is not None:
                self._set_key_status(provider, f"✕ Test failed: {error}", theme.RED_FG)
                return
            if result is None:
                self._set_key_status(provider, "✕ Test returned no result.", theme.RED_FG)
                return
            ok, message = result
            if ok:
                self._set_key_status(provider, f"✓ {message}", theme.GREEN_FG)
            else:
                self._set_key_status(provider, f"✕ {message}", theme.RED_FG)

        run_async(self, lambda: self.mgr.validate_api_key(provider, key), done)

    def _clear_key(self, provider: str) -> None:
        try:
            self.mgr.clear_api_key(provider)
        except ProviderError as exc:
            self._set_key_status(provider, f"✕ {exc}", theme.RED_FG)
            return
        self._key_rows[provider]["input"].clear()
        self._refresh_key_statuses()
        self._set_save_status(
            f"Removed the stored key for {PROVIDERS.get(provider, provider)}.", theme.TEXT_SECONDARY
        )
        self.settings_updated.emit()

    def _test_ollama(self) -> None:
        self.btn_test_ollama.setEnabled(False)
        self.btn_test_ollama.setText("Testing...")
        self.ollama_status_lbl.setText("◌ checking the local Ollama daemon...")
        self.ollama_status_lbl.setStyleSheet(f"color: {theme.ACCENT_BLUE}; font-size: 12px;")

        def done(result: tuple[bool, str] | None, error: Exception | None) -> None:
            self.btn_test_ollama.setEnabled(True)
            self.btn_test_ollama.setText("Test Ollama daemon")
            if error is not None:
                self.ollama_status_lbl.setText(f"✕ Test failed: {error}")
                self.ollama_status_lbl.setStyleSheet(f"color: {theme.RED_FG}; font-size: 12px;")
                return
            if result is None:
                self.ollama_status_lbl.setText("✕ Test returned no result.")
                self.ollama_status_lbl.setStyleSheet(f"color: {theme.RED_FG}; font-size: 12px;")
                return
            ok, message = result
            color = theme.GREEN_FG if ok else theme.RED_FG
            self.ollama_status_lbl.setText(("✓ " if ok else "✕ ") + message)
            self.ollama_status_lbl.setStyleSheet(f"color: {color}; font-size: 12px;")

        run_async(self, lambda: self.mgr.validate_api_key("ollama", ""), done)

    # -- diagnostics --------------------------------------------------------------------------------
    def _set_diag(self, key: str, markup: str) -> None:
        label = self._diag_labels.get(key)
        if label is not None:
            label.setText(markup)

    def _render_gate(self) -> None:
        certified = is_gate3_certified()
        report = latest_report()
        if certified:
            generated = str((report or {}).get("generated_at") or "")[:19].replace("T", " ")
            totals = (report or {}).get("totals") or {}
            passed = totals.get("pass") if isinstance(totals, dict) else None
            total = totals.get("total") if isinstance(totals, dict) else None
            self._set_diag(
                "gate",
                f"<span style='color:{theme.GREEN_FG};'>● Certified</span> live report {html.escape(generated)} "
                f"UTC ({passed}/{total} scenarios passed)",
            )
        else:
            detail = "no gate report found"
            if report is not None:
                detail = (
                    f"latest report mode={report.get('mode')!r}, gate_passed={report.get('gate_passed')!r}"
                )
            self._set_diag(
                "gate",
                f"<span style='color:{theme.YELLOW_FG};'>○ Not certified</span> - {html.escape(detail)}",
            )
        evidence = DEFAULT_EVIDENCE_DIR
        exists = evidence.is_dir()
        self._set_diag(
            "evidence",
            f"{html.escape(str(evidence.resolve() if exists else evidence))} "
            f"<span style='color:{theme.TEXT_MUTED};'>({'exists' if exists else 'missing'})</span>",
        )

    def _collect_diagnostics(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "daemon": False,
            "version": "",
            "error": "",
            "container": "missing",
            "container_name": "",
        }
        try:
            import docker

            client = docker.from_env(timeout=3)
            info = client.version()
            result["daemon"] = True
            result["version"] = str(info.get("Version") or "")
            containers = client.containers.list(all=True)
            sandbox = next((c for c in containers if c.name == "my-kali"), None)
            if sandbox is None:
                sandbox = next(
                    (c for c in containers if "kali" in c.name or "bletiner-kali" in c.name),
                    None,
                )
            if sandbox is not None:
                result["container_name"] = sandbox.name
                result["container"] = "running" if getattr(sandbox, "status", "") == "running" else "stopped"
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def refresh_diagnostics(self) -> None:
        """Run the Docker/container checks in the background and update the rows when done."""
        if self._diag_busy:
            return
        self._diag_busy = True
        self.btn_refresh_diag.setEnabled(False)
        self.btn_refresh_diag.setText("Checking...")
        self.diag_status_lbl.setText("Checking Docker daemon and Kali container...")
        self.diag_status_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        self._set_diag("daemon", f"<span style='color:{theme.ACCENT_BLUE};'>◌ checking...</span>")
        self._set_diag("container", f"<span style='color:{theme.ACCENT_BLUE};'>◌ checking...</span>")
        run_async(self, self._collect_diagnostics, self._on_diagnostics_done)

    def _on_diagnostics_done(self, result: dict[str, Any] | None, error: Exception | None) -> None:
        self._diag_busy = False
        self._diagnostics_checked = True
        self.btn_refresh_diag.setEnabled(True)
        self.btn_refresh_diag.setText("Re-run diagnostics")
        if error is not None or result is None:
            message = str(error) if error is not None else "diagnostics returned no result"
            self._set_diag("daemon", f"<span style='color:{theme.RED_FG};'>✕ {html.escape(message)}</span>")
            self._set_diag("container", f"<span style='color:{theme.TEXT_MUTED};'>unknown</span>")
            self.diag_status_lbl.setText(f"✕ Diagnostics failed: {message}")
            self.diag_status_lbl.setStyleSheet(f"color: {theme.RED_FG}; font-size: 12px;")
            return
        if result.get("daemon"):
            self._set_diag(
                "daemon",
                f"<span style='color:{theme.GREEN_FG};'>● Connected</span> "
                f"<span style='color:{theme.TEXT_SECONDARY};'>Docker Engine v"
                f"{html.escape(str(result.get('version') or 'unknown'))}</span>",
            )
        else:
            self._set_diag(
                "daemon",
                f"<span style='color:{theme.RED_FG};'>○ Offline</span> "
                f"<span style='color:{theme.TEXT_SECONDARY};'>- {html.escape(str(result.get('error'))[:200])}</span>",
            )
        container_state = str(result.get("container") or "missing")
        container_name = html.escape(str(result.get("container_name") or "my-kali"))
        if container_state == "running":
            self._set_diag(
                "container",
                f"<span style='color:{theme.GREEN_FG};'>● running</span> ({container_name})",
            )
        elif container_state == "stopped":
            self._set_diag(
                "container",
                f"<span style='color:{theme.YELLOW_FG};'>● stopped</span> ({container_name}) - "
                "start it to enable real local tool execution",
            )
        else:
            self._set_diag(
                "container",
                f"<span style='color:{theme.RED_FG};'>○ not found</span> - expected container 'my-kali'",
            )
        self._render_gate()
        self.diag_status_lbl.setText("Diagnostics updated.")
        self.diag_status_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")

    def _open_data_folder(self) -> None:
        data_dir = Path.home() / ".bletiner"
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.diag_status_lbl.setText(f"✕ Cannot create {data_dir}: {exc}")
            self.diag_status_lbl.setStyleSheet(f"color: {theme.RED_FG}; font-size: 12px;")
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir)))
        if opened:
            self.diag_status_lbl.setText(f"Opened {data_dir}")
            self.diag_status_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        else:
            self.diag_status_lbl.setText(f"Could not open {data_dir} (no desktop shell available).")
            self.diag_status_lbl.setStyleSheet(f"color: {theme.RED_FG}; font-size: 12px;")

    # -- public surface (INTERFACES) -----------------------------------------------------------------
    def _refresh_base_url_fields(self) -> None:
        if hasattr(self, "ollama_url_input"):
            self.ollama_url_input.setText(self.mgr.settings.ollama_base_url)
        if hasattr(self, "custom_url_input"):
            self.custom_url_input.setText(self.mgr.settings.custom_base_url)

    def refresh(self) -> None:
        """Reload credential status and the real gate/backend state; run diagnostics on first show."""
        self._refresh_key_statuses()
        self._refresh_base_url_fields()
        self._render_gate()
        if self.isVisible() and not self._diagnostics_checked and not self._diag_busy:
            self.refresh_diagnostics()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if not self._diagnostics_checked and not self._diag_busy:
            QTimer.singleShot(0, self.refresh_diagnostics)
