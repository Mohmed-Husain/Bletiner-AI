"""Settings and Diagnostics Panel for Bletiner AI desktop."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
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

import docker
from bletiner.agent.gate import is_gate3_certified
from bletiner.ui.controller import is_docker_available
from bletiner.ui.settings_manager import get_settings_manager


class SettingsPanel(QWidget):
    """Full-page settings configuration for API keys, Docker daemon, and system diagnostics."""

    settings_updated = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mgr = get_settings_manager()

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # Header
        header_box = QVBoxLayout()
        header_box.setSpacing(4)
        title = QLabel("⚙  Workstation Settings & Diagnostics")
        title.setStyleSheet("font-size: 20px; font-weight: 800; color: #ffffff;")
        subtitle = QLabel("Configure provider API keys, inspect Docker sandbox status, and verify security gates.")
        subtitle.setStyleSheet("font-size: 12px; color: #8892b0;")
        header_box.addWidget(title)
        header_box.addWidget(subtitle)
        root.addLayout(header_box)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        c_layout = QVBoxLayout(content)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(14)

        # Section 1: API Keys
        c_layout.addWidget(self._build_api_keys_section())

        # Section 2: Docker & Gateway Sandbox Diagnostics
        c_layout.addWidget(self._build_docker_diagnostics_section())

        c_layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

    def _build_api_keys_section(self) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background-color: #12151e; border: 1px solid #1c2130; border-radius: 12px; padding: 16px; }"
        )
        layout = QVBoxLayout(card)
        layout.setSpacing(12)

        sec_title = QLabel("🔑  Provider API Credentials")
        sec_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff;")
        layout.addWidget(sec_title)

        desc = QLabel(
            "API keys are stored securely in your OS keyring and are never written plaintext to disk or logs."
        )
        desc.setStyleSheet("font-size: 12px; color: #8892b0;")
        layout.addWidget(desc)

        # Anthropic
        self.input_anthropic = self._make_key_row(
            layout, "Anthropic API Key (Claude 3.5 Sonnet / Opus):", "sk-ant-api03-...", "anthropic"
        )

        # DeepSeek
        self.input_deepseek = self._make_key_row(
            layout, "DeepSeek API Key (DeepSeek-V3 / DeepSeek-R1):", "sk-...", "deepseek"
        )

        # OpenAI
        self.input_openai = self._make_key_row(
            layout, "OpenAI API Key (GPT-4o / GPT-4o-mini):", "sk-proj-...", "openai"
        )

        # Gemini
        self.input_gemini = self._make_key_row(layout, "Google Gemini API Key:", "AIzaSy...", "gemini")

        # Ollama URL
        ollama_row = QVBoxLayout()
        ollama_lbl = QLabel("Ollama / Local LLM Base URL:")
        ollama_lbl.setStyleSheet("font-size: 12px; color: #f3f4f6; font-weight: 600;")
        self.input_ollama = QLineEdit(self.mgr.settings.ollama_base_url)
        self.input_ollama.setStyleSheet(
            "background: #08090d; border: 1px solid #1e2434; border-radius: 8px; color: #f3f4f6; padding: 8px 12px;"
        )
        ollama_row.addWidget(ollama_lbl)
        ollama_row.addWidget(self.input_ollama)
        layout.addLayout(ollama_row)

        # Save Button
        btn_save = QPushButton("💾  Save API Credentials")
        btn_save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_save.setStyleSheet(
            "QPushButton { background: #2563eb; border: 1px solid #3b82f6; border-radius: 8px; color: #ffffff; font-weight: 700; padding: 9px 20px; font-size: 13px; }"
            "QPushButton:hover { background: #1d4ed8; }"
        )
        btn_save.clicked.connect(self._save_credentials)
        self.save_status = QLabel("")
        self.save_status.setStyleSheet("font-size: 12px; color: #10b981; font-weight: 600;")

        btn_row = QHBoxLayout()
        btn_row.addWidget(btn_save)
        btn_row.addWidget(self.save_status)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        return card

    def _make_key_row(self, parent_layout: QVBoxLayout, label_text: str, placeholder: str, provider: str) -> QLineEdit:
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel(label_text)
        lbl.setStyleSheet("font-size: 12px; color: #f3f4f6; font-weight: 600;")
        col.addWidget(lbl)

        h_row = QHBoxLayout()
        h_row.setSpacing(8)
        existing = self.mgr.get_api_key(provider)
        inp = QLineEdit(existing)
        inp.setEchoMode(QLineEdit.EchoMode.Password)
        inp.setPlaceholderText(placeholder)
        inp.setStyleSheet(
            "background: #08090d; border: 1px solid #1e2434; border-radius: 8px; color: #f3f4f6; padding: 8px 12px;"
        )

        btn_toggle = QPushButton("👁")
        btn_toggle.setFixedSize(36, 36)
        btn_toggle.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_toggle.setStyleSheet(
            "QPushButton { background: #1a1e28; border: 1px solid #2a3142; border-radius: 8px; color: #8892b0; }"
            "QPushButton:hover { color: #f3f4f6; background: #222838; }"
        )
        btn_toggle.clicked.connect(lambda: self._toggle_echo(inp, btn_toggle))

        h_row.addWidget(inp, 1)
        h_row.addWidget(btn_toggle)
        col.addLayout(h_row)
        parent_layout.addLayout(col)
        return inp

    def _toggle_echo(self, inp: QLineEdit, btn: QPushButton) -> None:
        if inp.echoMode() == QLineEdit.EchoMode.Password:
            inp.setEchoMode(QLineEdit.EchoMode.Normal)
            btn.setText("🔒")
        else:
            inp.setEchoMode(QLineEdit.EchoMode.Password)
            btn.setText("👁")

    def _save_credentials(self) -> None:
        ant = self.input_anthropic.text().strip()
        dps = self.input_deepseek.text().strip()
        opn = self.input_openai.text().strip()
        gem = self.input_gemini.text().strip()
        if ant:
            self.mgr.set_api_key("anthropic", ant)
        if dps:
            self.mgr.set_api_key("deepseek", dps)
        if opn:
            self.mgr.set_api_key("openai", opn)
        if gem:
            self.mgr.set_api_key("gemini", gem)
        self.mgr.settings.ollama_base_url = self.input_ollama.text().strip()
        self.mgr.save(self.mgr.settings)
        self.save_status.setText("✓ Credentials saved securely to OS keyring!")
        self.settings_updated.emit()

    def _build_docker_diagnostics_section(self) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background-color: #12151e; border: 1px solid #1c2130; border-radius: 12px; padding: 16px; }"
        )
        layout = QVBoxLayout(card)
        layout.setSpacing(12)

        sec_title = QLabel("🐳  Docker Sandbox & Gateway Diagnostics")
        sec_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff;")
        layout.addWidget(sec_title)

        self.diag_box = QVBoxLayout()
        self.diag_box.setSpacing(6)
        layout.addLayout(self.diag_box)

        # Refresh button
        btn_refresh = QPushButton("🔄  Re-test Docker & Container Diagnostics")
        btn_refresh.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_refresh.setStyleSheet(
            "QPushButton { background: #1a1e28; border: 1px solid #2a3142; border-radius: 8px; color: #38bdf8; font-weight: 600; padding: 8px 18px; }"
            "QPushButton:hover { background: #222838; border-color: #38bdf8; }"
        )
        btn_refresh.clicked.connect(self.refresh_diagnostics)
        layout.addWidget(btn_refresh)

        self.refresh_diagnostics()
        return card

    def refresh_diagnostics(self) -> None:
        # Clear diag box
        while self.diag_box.count():
            item = self.diag_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Check Docker
        docker_ok = is_docker_available()
        if docker_ok:
            try:
                client = docker.from_env()
                ver = client.version().get("Version", "Unknown")
                d_msg = f"<span style='color:#10b981; font-weight:700;'>● Connected</span> (Docker Desktop Engine v{ver})"
            except Exception as exc:
                d_msg = f"<span style='color:#ef4444;'>● Error connecting: {exc}</span>"
        else:
            d_msg = "<span style='color:#ef4444; font-weight:700;'>○ Offline</span> (Start Docker Desktop to enable Kali sandbox)"

        self._add_diag_row("Docker Daemon:", d_msg)

        # Check Kali image
        kali_found = False
        gateway_found = False
        if docker_ok:
            try:
                client = docker.from_env()
                tags = [t for img in client.images.list() for t in (img.tags or [])]
                kali_found = any("kali" in t for t in tags)
                gateway_found = any("gateway" in t for t in tags)
            except Exception:
                pass

        k_msg = (
            "<span style='color:#10b981;'>● Installed</span> (bletiner-kali / kali-rolling ready)"
            if kali_found
            else "<span style='color:#f59e0b;'>⚠️ Kali Image not found</span>"
        )
        self._add_diag_row("Hardened Kali Sandbox Image:", k_msg)

        g_msg = (
            "<span style='color:#10b981;'>● Installed</span> (bletiner-gateway ready)"
            if gateway_found
            else "<span style='color:#f59e0b;'>⚠️ Gateway Image not found</span>"
        )
        self._add_diag_row("Egress Gateway Image:", g_msg)

        # Gate 3 certification
        gate_ok = is_gate3_certified()
        gate_msg = (
            "<span style='color:#10b981; font-weight:700;'>● Tier C Certified (23/23 live scenarios PASSED)</span>"
            if gate_ok
            else "<span style='color:#f59e0b;'>⚠️ Gate 3 Uncertified</span>"
        )
        self._add_diag_row("Fail-Closed Egress Gate:", gate_msg)

    def refresh(self) -> None:
        """Refresh diagnostics and saved key representations."""
        self.refresh_diagnostics()

    def _add_diag_row(self, title: str, value_html: str) -> None:
        row = QHBoxLayout()
        row.setSpacing(12)
        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("font-size: 13px; color: #8892b0; font-weight: 500;")
        v_lbl = QLabel(value_html)
        v_lbl.setStyleSheet("font-size: 13px;")
        row.addWidget(t_lbl)
        row.addWidget(v_lbl, 1)
        self.diag_box.addLayout(row)
