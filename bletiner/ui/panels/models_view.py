"""AI Model Selection Panel for Bletiner AI desktop."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bletiner.providers.http_transport import HttpTransport
from bletiner.ui.settings_manager import get_settings_manager

AVAILABLE_MODELS = [
    {
        "id": "claude-3-5-sonnet",
        "provider": "anthropic",
        "name": "Claude 3.5 Sonnet",
        "badge": "Recommended",
        "badge_color": "green",
        "desc": "Industry-leading tool use, autonomous reasoning, and fast execution for security tasks.",
        "model_str": "claude-3-5-sonnet-20241022",
    },
    {
        "id": "claude-opus",
        "provider": "anthropic",
        "name": "Claude Opus 4.6",
        "badge": "Deep Reasoning",
        "badge_color": "blue",
        "desc": "Maximum depth reasoning for complex multi-stage attack paths and subtle vulnerabilities.",
        "model_str": "claude-3-opus-20240229",
    },
    {
        "id": "deepseek-v3",
        "provider": "deepseek",
        "name": "DeepSeek V3",
        "badge": "MoE Reasoning",
        "badge_color": "blue",
        "desc": "High efficiency 671B MoE architecture with advanced code understanding and tool planning.",
        "model_str": "deepseek-chat",
    },
    {
        "id": "deepseek-r1",
        "provider": "deepseek",
        "name": "DeepSeek R1",
        "badge": "Deep Reasoning",
        "badge_color": "yellow",
        "desc": "State-of-the-art reasoning model for chain-of-thought exploit analysis and vulnerability verification.",
        "model_str": "deepseek-reasoner",
    },
    {
        "id": "gpt-4o",
        "provider": "openai",
        "name": "OpenAI GPT-4o",
        "badge": "High Speed",
        "badge_color": "blue",
        "desc": "Fast, highly capable multimodal reasoning with structured tool outputs.",
        "model_str": "gpt-4o",
    },
    {
        "id": "ollama",
        "provider": "ollama",
        "name": "Ollama Local (Offline)",
        "badge": "100% Local / Free",
        "badge_color": "green",
        "desc": "Runs completely locally on your hardware (Llama 3 / Qwen 2.5). Zero API tokens or cost.",
        "model_str": "llama3",
    },
    {
        "id": "fake",
        "provider": "fake",
        "name": "Deterministic Simulation Lab",
        "badge": "Offline Demo",
        "badge_color": "gray",
        "desc": "Built-in deterministic test lab for testing the UI and tools without API keys.",
        "model_str": "simulation-lab",
    },
]


class ModelCard(QFrame):
    """Clickable card representing an AI model choice."""

    selected = Signal(str, str)

    def __init__(self, info: dict[str, Any], is_active: bool = False) -> None:
        super().__init__()
        self.info = info
        self.model_id = info["id"]
        self.provider = info["provider"]
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.set_active_style(is_active)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)

        self.radio = QRadioButton()
        self.radio.setChecked(is_active)
        self.radio.toggled.connect(self._on_radio_toggled)
        layout.addWidget(self.radio)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        name_lbl = QLabel(info["name"])
        name_lbl.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        badge_lbl = QLabel(info["badge"])
        badge_color = info["badge_color"]
        badge_style = {
            "green": "background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid #10b981;",
            "blue": "background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid #38bdf8;",
            "yellow": "background: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid #f59e0b;",
            "gray": "background: rgba(148, 163, 184, 0.12); color: #94a3b8; border: 1px solid #334155;",
        }.get(badge_color, "color: #94a3b8;")
        badge_lbl.setStyleSheet(f"{badge_style} border-radius: 10px; padding: 2px 10px; font-size: 11px; font-weight: 600;")
        header_row.addWidget(name_lbl)
        header_row.addWidget(badge_lbl)
        header_row.addStretch(1)
        text_col.addLayout(header_row)

        desc_lbl = QLabel(info["desc"])
        desc_lbl.setStyleSheet("font-size: 12px; color: #8892b0;")
        desc_lbl.setWordWrap(True)
        text_col.addWidget(desc_lbl)

        # Key status
        mgr = get_settings_manager()
        has_key = bool(mgr.get_api_key(self.provider))
        if self.provider == "fake":
            key_status = "<span style='color:#10b981;'>✓ No key needed (Offline Lab)</span>"
        elif self.provider == "ollama":
            key_status = "<span style='color:#38bdf8;'>ℹ Connects to localhost:11434</span>"
        elif has_key:
            key_status = "<span style='color:#10b981;'>✓ API Key configured</span>"
        else:
            key_status = "<span style='color:#f59e0b;'>⚠️ API Key not set (Configure in Settings)</span>"

        status_lbl = QLabel(key_status)
        status_lbl.setStyleSheet("font-size: 11px; margin-top: 2px;")
        text_col.addWidget(status_lbl)

        layout.addLayout(text_col, 1)

    def set_active_style(self, active: bool) -> None:
        if active:
            self.setStyleSheet(
                "QFrame { background-color: rgba(37, 99, 235, 0.12); border: 2px solid #2563eb; border-radius: 12px; }"
            )
        else:
            self.setStyleSheet(
                "QFrame { background-color: #12151e; border: 1px solid #1c2130; border-radius: 12px; }"
                "QFrame:hover { background-color: #161a26; border-color: #2a3346; }"
            )

    def mousePressEvent(self, event: Any) -> None:
        self.radio.setChecked(True)
        super().mousePressEvent(event)

    def _on_radio_toggled(self, checked: bool) -> None:
        self.set_active_style(checked)
        if checked:
            self.selected.emit(self.model_id, self.provider)


class ModelsPanel(QWidget):
    """Full-page view to select and test the active AI model."""

    model_changed = Signal(str, str)  # (model_id, provider)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mgr = get_settings_manager()
        self.cards: list[ModelCard] = []

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(24, 20, 24, 20)
        root_layout.setSpacing(16)

        # Header
        header_box = QVBoxLayout()
        header_box.setSpacing(4)
        title = QLabel("🧠  AI Model Selection")
        title.setStyleSheet("font-size: 20px; font-weight: 800; color: #ffffff;")
        subtitle = QLabel(
            "Select the reasoning engine for autonomous tool dispatch, observation analysis, and report generation."
        )
        subtitle.setStyleSheet("font-size: 12px; color: #8892b0;")
        header_box.addWidget(title)
        header_box.addWidget(subtitle)
        root_layout.addLayout(header_box)

        # Scrollable cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        cards_widget = QWidget()
        cards_widget.setStyleSheet("background: transparent;")
        cards_layout = QVBoxLayout(cards_widget)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(10)

        active_id = self.mgr.settings.active_model
        for info in AVAILABLE_MODELS:
            is_active = info["id"] == active_id
            card = ModelCard(info, is_active=is_active)
            card.selected.connect(self._on_model_selected)
            self.cards.append(card)
            cards_layout.addWidget(card)

        cards_layout.addStretch(1)
        scroll.setWidget(cards_widget)
        root_layout.addWidget(scroll, 1)

        # Bottom actions
        action_bar = QFrame()
        action_bar.setStyleSheet(
            "background-color: #12151e; border: 1px solid #1c2130; border-radius: 10px; padding: 10px;"
        )
        act_layout = QHBoxLayout(action_bar)
        act_layout.setContentsMargins(12, 6, 12, 6)

        self.status_lbl = QLabel(f"Active Model: <b style='color:#38bdf8;'>{self.get_active_model_name()}</b>")
        self.status_lbl.setStyleSheet("font-size: 13px; color: #f3f4f6;")
        act_layout.addWidget(self.status_lbl)
        act_layout.addStretch(1)

        self.btn_test = QPushButton("⚡  Test Connection")
        self.btn_test.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_test.setStyleSheet(
            "QPushButton { background: #1a1e28; border: 1px solid #2a3142; border-radius: 8px; color: #38bdf8; font-weight: 600; padding: 8px 16px; }"
            "QPushButton:hover { background: #222838; border-color: #38bdf8; }"
        )
        self.btn_test.clicked.connect(self._test_connection)
        act_layout.addWidget(self.btn_test)

        root_layout.addWidget(action_bar)

    def get_active_model_name(self) -> str:
        active_id = self.mgr.settings.active_model
        for m in AVAILABLE_MODELS:
            if m["id"] == active_id:
                return m["name"]
        return "Claude 3.5 Sonnet"

    def _on_model_selected(self, model_id: str, provider: str) -> None:
        for card in self.cards:
            if card.model_id != model_id:
                card.radio.setChecked(False)
                card.set_active_style(False)
        self.mgr.settings.active_model = model_id
        self.mgr.settings.active_provider = provider
        self.mgr.save(self.mgr.settings)
        self.status_lbl.setText(f"Active Model: <b style='color:#38bdf8;'>{self.get_active_model_name()}</b>")
        self.model_changed.emit(model_id, provider)

    def _test_connection(self) -> None:
        provider = self.mgr.settings.active_provider
        if provider == "fake":
            self.status_lbl.setText("Active Model: <b style='color:#10b981;'>Simulation Lab Ready (Offline)</b>")
            return

        key = self.mgr.get_api_key(provider)
        if not key and provider != "ollama":
            self.status_lbl.setText(
                f"<span style='color:#ef4444;'>❌ Missing API Key for {provider.title()}. Configure in Settings tab!</span>"
            )
            return

        self.status_lbl.setText("Testing connection...")
        try:
            transport = HttpTransport(timeout_s=10.0)
            if provider == "deepseek":
                transport.post(
                    "https://api.deepseek.com/chat/completions",
                    {"Authorization": f"Bearer {key}"},
                    {"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                )
            elif provider == "openai":
                transport.post(
                    "https://api.openai.com/v1/chat/completions",
                    {"Authorization": f"Bearer {key}"},
                    {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                )
            elif provider == "anthropic":
                transport.post(
                    "https://api.anthropic.com/v1/messages",
                    {"x-api-key": key, "anthropic-version": "2023-06-01"},
                    {"model": "claude-3-5-sonnet-20241022", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                )
            self.status_lbl.setText("<span style='color:#10b981;'>✓ Connection verified successfully!</span>")
        except Exception as exc:
            self.status_lbl.setText(f"<span style='color:#ef4444;'>❌ Connection failed: {exc}</span>")
