"""Settings credential Test must validate AND persist the key (auto-save on success)."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeSecrets:
    backend = "OS keyring"
    last_error = None

    @staticmethod
    def describe() -> str:
        return "in-memory fake vault"


class FakeManager:
    def __init__(self, *, validation: tuple[bool, str] = (True, "Key accepted - test prompt ok")) -> None:
        self.settings = SimpleNamespace(
            ollama_base_url="http://localhost:11434",
            custom_base_url="",
            active_provider="",
            active_model="",
        )
        self.secrets = FakeSecrets()
        self._validation = validation
        self.keys: dict[str, str] = {}
        self.set_calls: list[tuple[str, str]] = []
        self.save_calls = 0

    def api_key_status(self, provider: str) -> tuple[bool, str]:
        if provider in self.keys:
            return True, "Configured - stored via OS keyring"
        return False, "No key configured"

    def get_api_key(self, provider: str) -> str:
        return self.keys.get(provider, "")

    def set_api_key(self, provider: str, key: str) -> None:
        self.set_calls.append((provider, key))
        self.keys[provider] = key

    def clear_api_key(self, provider: str) -> None:
        self.keys.pop(provider, None)

    def save(self, settings: Any) -> None:
        self.save_calls += 1
        self.settings = settings

    def validate_api_key(self, provider: str, key: str) -> tuple[bool, str]:
        return self._validation


def _qapp() -> Any:
    from PySide6.QtWidgets import QApplication

    from bletiner.ui import theme

    app = QApplication.instance() or QApplication([])
    if app.styleSheet() != theme.QSS:
        theme.apply_dark_theme(app)
    return app


@pytest.fixture()
def settings_panel(monkeypatch: pytest.MonkeyPatch):
    _qapp()
    from bletiner.ui.panels import settings_view as sv

    manager = FakeManager()
    monkeypatch.setattr(sv, "get_settings_manager", lambda: manager)
    # Execute the worker + done callback synchronously in the test.
    monkeypatch.setattr(sv, "run_async", lambda _panel, work, done: done(work(), None))
    panel = sv.SettingsPanel()
    yield panel, manager
    panel.deleteLater()


def test_test_and_save_persists_a_validated_key(settings_panel) -> None:
    panel, manager = settings_panel
    row = panel._key_rows["deepseek"]
    row["input"].setText("sk-real-valid-key")
    panel._test_key("deepseek")

    assert manager.set_calls == [("deepseek", "sk-real-valid-key")]
    assert manager.save_calls == 1
    assert row["input"].text() == ""
    status = row["status"].text().lower()
    assert "saved" in status
    assert "✕" not in status


def test_failed_validation_does_not_store_the_key(settings_panel) -> None:
    panel, manager = settings_panel
    manager._validation = (False, "Authentication failed (HTTP 401)")
    row = panel._key_rows["deepseek"]
    row["input"].setText("sk-bad-key")
    panel._test_key("deepseek")

    assert manager.set_calls == []
    assert manager.save_calls == 0
    assert "not saved" in panel.save_status_lbl.text().lower()
    assert row["input"].text() == "sk-bad-key"  # keep it so the user can correct it
