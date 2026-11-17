"""Models and providers panel: real discovery, honest key status, real connection/probe tests.

There are no hardcoded model cards here. The list is populated exclusively from the provider's own
model-list endpoint (see :mod:`bletiner.providers.model_discovery`); a curated fallback list is shown
only after a failed discovery and is labeled as unverified. Network calls run off the GUI thread.
"""

from __future__ import annotations

import html
import logging
import time
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
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

from bletiner.models.errors import ConfigurationError, ProviderError
from bletiner.providers.model_discovery import (
    BASE_URLS,
    PROVIDERS,
    DiscoveredModel,
    discover_models,
    models_url,
    normalize_provider,
    probe_model,
)
from bletiner.services.secrets import BACKEND_MEMORY
from bletiner.ui import theme
from bletiner.ui.panels.jobs import run_async
from bletiner.ui.settings_manager import get_settings_manager

logger = logging.getLogger(__name__)

__all__ = ["ModelsPanel"]

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
_SECONDARY_BTN_QSS = (
    f"QPushButton {{ background-color: #1a1e28; border: 1px solid #2a3142; border-radius: 8px; "
    f"color: {theme.ACCENT_BLUE}; font-weight: 600; font-size: 12px; padding: 8px 14px; }}"
    "QPushButton:hover { background-color: #222838; border-color: #38bdf8; }"
    "QPushButton:disabled { color: #64748b; border-color: #1c2130; }"
)
_PRIMARY_BTN_QSS = (
    "QPushButton { background-color: #2563eb; border: 1px solid #3b82f6; border-radius: 8px; "
    "color: #ffffff; font-weight: 700; font-size: 12px; padding: 8px 16px; }"
    "QPushButton:hover { background-color: #1d4ed8; }"
    "QPushButton:disabled { background-color: #1c2130; border-color: #2a3142; color: #64748b; }"
)
_LIST_QSS = (
    f"QListWidget {{ background-color: {theme.BG_CARD_INNER}; border: 1px solid {theme.BORDER_CARD}; "
    f"border-radius: 8px; color: {theme.TEXT_PRIMARY}; padding: 4px; outline: 0; }}"
    "QListWidget::item { padding: 6px 8px; border-bottom: 1px solid #141722; border-radius: 4px; }"
    "QListWidget::item:hover { background-color: #141824; }"
    f"QListWidget::item:selected {{ background-color: {theme.BORDER_CARD}; }}"
)

_ROLE_MODEL = int(Qt.ItemDataRole.UserRole)
_ROLE_PROVIDER = _ROLE_MODEL + 1
_ROLE_CURATED = _ROLE_MODEL + 2


def _muted(text: str, *, size: int = 12) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: {size}px;")
    return label


class ModelsPanel(QWidget):
    """Select a provider, discover its real models, and persist the active model choice."""

    model_changed = Signal(str, str)  # (model_id, provider)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mgr = get_settings_manager()
        self._models: list[Any] = []
        self._verified: set[str] = set()
        self._fallback = False
        self._busy = False
        self._probe_armed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Models & Providers")
        title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 20px; font-weight: 800;")
        header.addWidget(title)
        header.addStretch(1)
        self.backend_lbl = QLabel("")
        self.backend_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        header.addWidget(self.backend_lbl)
        root.addLayout(header)
        root.addWidget(
            _muted(
                "Discover the models a provider endpoint actually reports, then choose the one the AI "
                "engine should use. Nothing here is invented: an empty list means nothing has been "
                "discovered yet.",
            )
        )

        root.addWidget(self._build_provider_card())
        root.addWidget(self._build_list_card(), 1)

        self.refresh()

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

    def _build_provider_card(self) -> QFrame:
        card = self._card()
        layout = card.layout()

        row = QHBoxLayout()
        row.setSpacing(10)
        provider_label = QLabel("Provider")
        provider_label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 12px; font-weight: 600;")
        row.addWidget(provider_label)
        self.provider_combo = QComboBox()
        for provider, label in PROVIDERS.items():
            self.provider_combo.addItem(label, provider)
        self.provider_combo.setStyleSheet(_INPUT_QSS)
        self.provider_combo.setMinimumWidth(240)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        row.addWidget(self.provider_combo)

        base_label = QLabel("Endpoint base URL")
        base_label.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 12px; font-weight: 600;")
        row.addWidget(base_label)
        self.base_url_input = QLineEdit()
        self.base_url_input.setStyleSheet(_INPUT_QSS)
        self.base_url_input.setPlaceholderText("")
        row.addWidget(self.base_url_input, 1)
        layout.addLayout(row)

        self.key_status_lbl = QLabel("")
        self.key_status_lbl.setWordWrap(True)
        layout.addWidget(self.key_status_lbl)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.btn_discover = QPushButton("Discover models")
        self.btn_discover.setStyleSheet(_PRIMARY_BTN_QSS)
        self.btn_discover.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_discover.clicked.connect(lambda: self._run_discovery("discover"))
        buttons.addWidget(self.btn_discover)

        self.btn_test = QPushButton("Test connection")
        self.btn_test.setStyleSheet(_SECONDARY_BTN_QSS)
        self.btn_test.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_test.clicked.connect(lambda: self._run_discovery("test"))
        buttons.addWidget(self.btn_test)

        self.btn_probe = QPushButton("Verify capabilities")
        self.btn_probe.setStyleSheet(_SECONDARY_BTN_QSS)
        self.btn_probe.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_probe.clicked.connect(self._on_probe_clicked)
        buttons.addWidget(self.btn_probe)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.status_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(self.status_lbl)
        return card

    def _build_list_card(self) -> QFrame:
        card = self._card()
        layout = card.layout()

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search discovered models by id or name")
        self.search_input.setStyleSheet(_INPUT_QSS)
        self.search_input.textChanged.connect(self._apply_filter)
        search_row.addWidget(self.search_input, 1)
        self.filter_count_lbl = QLabel("0 models loaded")
        self.filter_count_lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        search_row.addWidget(self.filter_count_lbl)
        layout.addLayout(search_row)

        manual_row = QHBoxLayout()
        manual_row.setSpacing(10)
        self.manual_input = QLineEdit()
        self.manual_input.setPlaceholderText(
            "Optional: exact model id when discovery is unavailable (e.g. deepseek-chat)"
        )
        self.manual_input.setStyleSheet(_INPUT_QSS)
        self.manual_input.returnPressed.connect(self._use_manual_model)
        manual_row.addWidget(self.manual_input, 1)
        self.btn_manual = QPushButton("Use this model id")
        self.btn_manual.setStyleSheet(_SECONDARY_BTN_QSS)
        self.btn_manual.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_manual.setToolTip("Persist an exact provider model id without discovery (unverified)")
        self.btn_manual.clicked.connect(self._use_manual_model)
        manual_row.addWidget(self.btn_manual)
        layout.addLayout(manual_row)

        self.model_list = QListWidget()
        self.model_list.setStyleSheet(_LIST_QSS)
        self.model_list.itemSelectionChanged.connect(self._update_action_buttons)
        self.model_list.itemDoubleClicked.connect(lambda _item: self._use_selected())
        layout.addWidget(self.model_list, 1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.active_lbl = QLabel("")
        self.active_lbl.setWordWrap(True)
        self.active_lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        footer.addWidget(self.active_lbl, 1)
        self.btn_use = QPushButton("Use selected model")
        self.btn_use.setStyleSheet(_PRIMARY_BTN_QSS)
        self.btn_use.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_use.clicked.connect(self._use_selected)
        footer.addWidget(self.btn_use)
        layout.addLayout(footer)
        return card

    # -- helpers ------------------------------------------------------------------------------------
    def _current_provider(self) -> str:
        data = self.provider_combo.currentData()
        return str(data) if data else ""

    def _effective_base_url(self, provider: str) -> str | None:
        """Base URL for *provider*: the field value when relevant, otherwise the stored/default one."""
        typed = self.base_url_input.text().strip()
        if provider == "custom":
            return typed or self.mgr.settings.custom_base_url.strip() or None
        if provider == "ollama":
            return typed or self.mgr.settings.ollama_base_url.strip() or None
        return typed or None

    def _set_status(self, markup: str) -> None:
        self.status_lbl.setText(markup)

    def _show_info(self, text: str) -> None:
        self._set_status(f"<span style='color:{theme.ACCENT_BLUE};'>{html.escape(text)}</span>")

    def _show_success(self, text: str) -> None:
        self._set_status(f"<span style='color:{theme.GREEN_FG};'>{html.escape(text)}</span>")

    def _show_warning(self, text: str) -> None:
        self._set_status(f"<span style='color:{theme.YELLOW_FG};'>{html.escape(text)}</span>")

    def _show_error(self, text: str) -> None:
        self._set_status(f"<span style='color:{theme.RED_FG};'>{html.escape(text)}</span>")

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        self._busy = busy
        self.provider_combo.setEnabled(not busy)
        self.base_url_input.setEnabled(not busy)
        self.search_input.setEnabled(not busy)
        self.btn_discover.setEnabled(not busy)
        self.btn_test.setEnabled(not busy)
        self._update_action_buttons()
        if message:
            self._show_info(message)

    def _update_action_buttons(self) -> None:
        has_selection = self._selected_item() is not None
        self.btn_probe.setEnabled(not self._busy and has_selection)
        self.btn_use.setEnabled(not self._busy and has_selection)

    def _selected_item(self) -> QListWidgetItem | None:
        items = self.model_list.selectedItems()
        if not items:
            return None
        item = items[0]
        return item if item.data(_ROLE_MODEL) else None

    def _persist_settings(self) -> bool:
        try:
            self.mgr.save(self.mgr.settings)
        except ConfigurationError as exc:
            self._show_error(f"Cannot save settings: {exc}")
            return False
        return True

    def _persist_base_url(self, provider: str) -> None:
        typed = self.base_url_input.text().strip()
        if provider == "custom":
            self.mgr.settings.custom_base_url = typed
        elif provider == "ollama" and typed:
            self.mgr.settings.ollama_base_url = typed
        self._persist_settings()

    def _reset_probe_confirmation(self) -> None:
        self._probe_armed = False
        if hasattr(self, "btn_probe"):
            self.btn_probe.setText("Verify capabilities")

    # -- provider / key state -----------------------------------------------------------------------
    def _on_provider_changed(self) -> None:
        provider = self._current_provider()
        self._reset_probe_confirmation()
        if provider == "custom":
            self.base_url_input.setPlaceholderText("Required, e.g. http://localhost:8080/v1")
            self.base_url_input.setText(self.mgr.settings.custom_base_url)
        elif provider == "ollama":
            self.base_url_input.setPlaceholderText(BASE_URLS["ollama"])
            self.base_url_input.setText(self.mgr.settings.ollama_base_url)
        else:
            self.base_url_input.setPlaceholderText(f"Default: {BASE_URLS.get(provider, '')}")
            self.base_url_input.clear()
        self._models = []
        self._fallback = False
        self._populate()
        self._update_key_status()
        self._update_active_label()
        self._set_status("")
        self._update_action_buttons()

    def _update_key_status(self) -> None:
        provider = self._current_provider()
        if not provider:
            self.key_status_lbl.setText("")
            return
        lines: list[str] = []
        if provider == "ollama":
            base = self._effective_base_url(provider) or BASE_URLS["ollama"]
            lines.append(
                f"<span style='color:{theme.ACCENT_BLUE};'>No API key required</span> "
                f"<span style='color:{theme.TEXT_SECONDARY};'>- local daemon at {html.escape(base)}.</span>"
            )
        else:
            configured, description = self.mgr.api_key_status(provider)
            if configured:
                lines.append(
                    f"<span style='color:{theme.GREEN_FG};'>API key: {html.escape(description)}</span>"
                )
            elif provider == "custom":
                lines.append(
                    f"<span style='color:{theme.TEXT_SECONDARY};'>API key optional for custom endpoints; "
                    "add one in Settings if this endpoint requires authentication.</span>"
                )
            else:
                lines.append(
                    f"<span style='color:{theme.YELLOW_FG};'>No API key configured for "
                    f"{html.escape(PROVIDERS[provider])}</span> "
                    f"<span style='color:{theme.TEXT_SECONDARY};'>- add it in Settings -> Providers.</span>"
                )
        if self.mgr.secrets.backend == BACKEND_MEMORY:
            lines.append(
                f"<span style='color:{theme.YELLOW_FG};'>Secret backend is Memory only: keys will not "
                "persist after the app exits.</span>"
            )
        self.key_status_lbl.setText(" ".join(lines))
        self.backend_lbl.setText(f"Secrets backend: {self.mgr.secrets.backend}")

    def _update_active_label(self) -> None:
        model = self.mgr.settings.active_model.strip()
        provider = self.mgr.settings.active_provider.strip().lower()
        if not model:
            self.active_lbl.setText("Active model: none - discover and select a model.")
            return
        provider_label = PROVIDERS.get(provider, provider or "unknown")
        ready, hint = self.mgr.provider_model_ready(provider, model) if provider else (False, "")
        if ready:
            marker = f"<span style='color:{theme.GREEN_FG};'>●</span>"
            suffix = f"<span style='color:{theme.TEXT_SECONDARY};'> - {html.escape(hint)}</span>"
        else:
            marker = f"<span style='color:{theme.YELLOW_FG};'>●</span>"
            suffix = f"<span style='color:{theme.YELLOW_FG};'> - {html.escape(hint)}</span>"
        self.active_lbl.setText(
            f"{marker} Active model: <b>{html.escape(model)}</b> ({html.escape(provider_label)}){suffix}"
        )

    # -- discovery / probing ------------------------------------------------------------------------
    def _run_discovery(self, kind: str) -> None:
        if self._busy:
            return
        provider = self._current_provider()
        if not provider:
            self._show_error("Select a provider first.")
            return
        base_url = self._effective_base_url(provider)
        try:
            endpoint = models_url(provider, base_url)
        except ProviderError as exc:
            self._show_error(str(exc))
            return
        key = self.mgr.get_api_key(provider) or None
        self._persist_base_url(provider)
        self._set_busy(True, f"Querying {endpoint} ...")
        started = time.perf_counter()

        def work() -> tuple[list[Any], float]:
            models = discover_models(provider, api_key=key, base_url=base_url)
            return models, (time.perf_counter() - started) * 1000.0

        run_async(
            self,
            work,
            lambda result, error: self._on_discovery_done(kind, provider, endpoint, result, error),
        )

    def _on_discovery_done(
        self,
        kind: str,
        provider: str,
        endpoint: str,
        result: tuple[list[Any], float] | None,
        error: Exception | None,
    ) -> None:
        self._set_busy(False)
        if error is not None:
            self._models = []
            self._fallback = False
            self._populate()
            message = f"{'Connection test' if kind == 'test' else 'Discovery'} failed: {error}"
            if "401" in str(error) or "auth" in str(error).lower():
                message += (
                    " The provider rejected the API key, so nothing was listed. In Settings -> Providers, "
                    "paste the key again and press 'Test & save' (that stores it), then retry discovery."
                )
            else:
                message += (
                    " Nothing was listed because the provider was not reached. Fix the error and retry, or "
                    "persist an exact model id manually below."
                )
            self._show_error(message)
            return
        if result is None:
            self._show_error("Discovery returned no result.")
            return
        models, elapsed_ms = result
        self._models = list(models)
        self._fallback = False
        self._populate()
        verb = "Connection OK" if kind == "test" else "Discovered"
        self._show_success(f"{verb}: {len(models)} models via {endpoint} in {elapsed_ms:.0f} ms.")

    def _probe_warning(self, provider: str, model_id: str) -> str:
        if provider == "ollama":
            return f"Verifying '{model_id}' sends one request to your local Ollama daemon; no provider cost."
        return (
            f"Verifying '{model_id}' sends exactly ONE minimal inference request (max 8 output tokens) "
            f"to {PROVIDERS[provider]}. Your provider may bill a small amount. Click "
            "'Confirm probe (1 request)' to authorize it."
        )

    def _on_probe_clicked(self) -> None:
        if self._busy:
            return
        item = self._selected_item()
        if item is None:
            self._show_error("Select a discovered model first.")
            return
        model_id = str(item.data(_ROLE_MODEL))
        provider = str(item.data(_ROLE_PROVIDER))
        if not self._probe_armed:
            self._probe_armed = True
            self.btn_probe.setText("Confirm probe (1 request)")
            self._show_warning(self._probe_warning(provider, model_id))
            return
        self._reset_probe_confirmation()
        key = self.mgr.get_api_key(provider) or None
        base_url = self._effective_base_url(provider)
        self._set_busy(True, f"Sending one minimal request to {PROVIDERS[provider]} ({model_id}) ...")
        run_async(
            self,
            lambda: probe_model(provider, model_id, api_key=key, base_url=base_url),
            lambda result, error: self._on_probe_done(provider, model_id, result, error),
        )

    def _on_probe_done(
        self,
        provider: str,
        model_id: str,
        result: tuple[bool, str] | None,
        error: Exception | None,
    ) -> None:
        self._set_busy(False)
        if error is not None:
            self._show_error(f"Probe failed: {error}")
            return
        if result is None:
            self._show_error("Probe returned no result.")
            return
        ok, message = result
        if ok:
            self._verified.add(f"{provider}:{model_id}")
            self._populate()
            self._show_success(f"Verified: {message}")
        else:
            self._show_error(message)

    # -- list rendering -----------------------------------------------------------------------------
    def _populate(self) -> None:
        self.model_list.clear()
        if not self._models:
            placeholder = QListWidgetItem(
                "No models loaded. Choose a provider and press 'Discover models' - the empty list is honest, "
                "nothing is preselected."
            )
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            placeholder.setForeground(Qt.GlobalColor.gray)
            self.model_list.addItem(placeholder)
            self.filter_count_lbl.setText("0 models loaded")
            self._update_action_buttons()
            return
        active_model = self.mgr.settings.active_model.strip()
        active_provider = self.mgr.settings.active_provider.strip().lower()
        for model in self._models:
            is_active = model.model_id == active_model and model.provider == active_provider
            marker = "●" if is_active else "○"
            if model.display_name and model.display_name != model.model_id:
                text = f"{marker}  {model.display_name}  ({model.model_id})"
            else:
                text = f"{marker}  {model.model_id}"
            if self._fallback:
                text += "   [curated fallback - unverified]"
            if f"{model.provider}:{model.model_id}" in self._verified:
                text += "   [verified]"
            item = QListWidgetItem(text)
            item.setData(_ROLE_MODEL, model.model_id)
            item.setData(_ROLE_PROVIDER, model.provider)
            item.setData(_ROLE_CURATED, self._fallback)
            tooltip_lines = [f"provider: {model.provider}", f"model id: {model.model_id}"]
            if model.context_window:
                tooltip_lines.append(f"context window: {model.context_window}")
            if self._fallback:
                tooltip_lines.append("CURATED FALLBACK - not returned by the provider; verify before use.")
            else:
                tooltip_lines.append("Reported by the provider model-list endpoint.")
            item.setToolTip("\n".join(tooltip_lines))
            if self._fallback:
                item.setForeground(Qt.GlobalColor.darkYellow)
            self.model_list.addItem(item)
        self._apply_filter()
        self._update_action_buttons()

    def _apply_filter(self) -> None:
        if not self._models:
            self.filter_count_lbl.setText("0 models loaded")
            return
        needle = self.search_input.text().strip().lower()
        visible = 0
        for row in range(self.model_list.count()):
            item = self.model_list.item(row)
            model_id = str(item.data(_ROLE_MODEL) or "")
            text = item.text().lower()
            matches = not needle or needle in model_id.lower() or needle in text
            item.setHidden(not matches)
            visible += int(matches)
        if visible:
            self.filter_count_lbl.setText(f"{visible} of {len(self._models)} models shown")
        else:
            self.filter_count_lbl.setText(f"No models match '{needle}'")

    # -- actions ------------------------------------------------------------------------------------
    def _use_selected(self) -> None:
        if self._busy:
            return
        item = self._selected_item()
        if item is None:
            self._show_error("Select a model from the list first.")
            return
        model_id = str(item.data(_ROLE_MODEL))
        provider = str(item.data(_ROLE_PROVIDER))
        curated = bool(item.data(_ROLE_CURATED))
        self._persist_base_url(provider)
        self.mgr.settings.active_provider = provider
        self.mgr.settings.active_model = model_id
        if not self._persist_settings():
            return
        self._populate()
        self._update_active_label()
        message = f"Active model set to '{model_id}' ({PROVIDERS.get(provider, provider)})."
        if curated:
            message += " This id comes from the curated fallback list - verify it exists before starting."
        self._show_success(message)
        self.model_changed.emit(model_id, provider)

    def _use_manual_model(self) -> None:
        """Persist an exact model id typed by the operator (used when discovery cannot list models)."""
        if self._busy:
            return
        model_id = self.manual_input.text().strip()
        provider = self._current_provider()
        if not provider:
            self._show_error("Select a provider first.")
            return
        if not model_id:
            self._show_error("Enter an exact model id (e.g. deepseek-chat) first.")
            return
        self._persist_base_url(provider)
        self.mgr.settings.active_provider = provider
        self.mgr.settings.active_model = model_id
        if not self._persist_settings():
            return
        self.manual_input.clear()
        self._models = [DiscoveredModel(provider=provider, model_id=model_id, display_name=model_id)]
        self._fallback = False
        self._populate()
        self._update_active_label()
        self._show_warning(
            f"Active model set to '{model_id}' from manual entry. It was NOT confirmed by the provider's "
            "model list; use 'Test connection' to validate the key/model before starting an AI run."
        )
        self.model_changed.emit(model_id, provider)

    # -- public surface (INTERFACES) -----------------------------------------------------------------
    def get_active_model_name(self) -> str:
        """Persisted model id, or "" when no model has been configured (never an invented default)."""
        return self.mgr.settings.active_model.strip()

    def refresh(self) -> None:
        """Reload persisted selection/key state (no network requests)."""
        settings = self.mgr.settings
        provider = settings.active_provider.strip().lower()
        if provider in PROVIDERS:
            index = self.provider_combo.findData(provider)
            if index >= 0 and index != self.provider_combo.currentIndex():
                self.provider_combo.setCurrentIndex(index)
        self._update_key_status()
        self._update_active_label()
        self._update_action_buttons()
        if not self._models:
            self._populate()

    def focus_provider(self, provider: str) -> None:
        """Select *provider* in the drop-down (used by the New Investigation flow)."""
        try:
            normalized = normalize_provider(provider)
        except ProviderError:
            return
        index = self.provider_combo.findData(normalized)
        if index >= 0:
            self.provider_combo.setCurrentIndex(index)
        self.provider_combo.setFocus(Qt.FocusReason.ShortcutFocusReason)
