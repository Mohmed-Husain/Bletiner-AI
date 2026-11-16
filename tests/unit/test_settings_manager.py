"""Tier B: settings persistence, honest secret backends and real key validation (no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest

from bletiner.models.errors import AuthenticationError, ProviderError
from bletiner.services.secrets import (
    BACKEND_DPAPI,
    BACKEND_KEYRING,
    BACKEND_MEMORY,
    SecretStorageError,
    SecretStore,
    secret_backend_name,
)
from bletiner.ui import settings_manager as settings_module
from bletiner.ui.settings_manager import AppSettings, SettingsManager

ALLOWED_BACKENDS = {BACKEND_KEYRING, BACKEND_DPAPI, BACKEND_MEMORY}


def _memory_store() -> SecretStore:
    """Isolated store for unit tests: never touches the real keyring or the real home directory."""
    return SecretStore(use_keyring=False, allow_dpapi=False)


def _manager(tmp_path: Path) -> SettingsManager:
    return SettingsManager(settings_path=tmp_path / "settings.json", secret_store=_memory_store())


class FakeHttpTransport:
    """Stands in for HttpTransport inside validate_api_key; records calls, returns scripted payloads."""

    instances: ClassVar[list[FakeHttpTransport]] = []
    get_error: Exception | None = None
    post_error: Exception | None = None
    post_payload: Any = None
    get_payload: Any = None

    def __init__(self, timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s
        self.get_calls: list[tuple[str, dict[str, str]]] = []
        self.post_calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []
        FakeHttpTransport.instances.append(self)

    def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        self.get_calls.append((url, dict(headers or {})))
        if FakeHttpTransport.get_error is not None:
            raise FakeHttpTransport.get_error
        return FakeHttpTransport.get_payload

    def post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        self.post_calls.append((url, dict(headers), dict(body)))
        if FakeHttpTransport.post_error is not None:
            raise FakeHttpTransport.post_error
        return FakeHttpTransport.post_payload


@pytest.fixture
def fake_transport(monkeypatch: pytest.MonkeyPatch) -> type[FakeHttpTransport]:
    FakeHttpTransport.instances = []
    FakeHttpTransport.get_error = None
    FakeHttpTransport.post_error = None
    FakeHttpTransport.post_payload = {"choices": [{"message": {"content": "BLETINER_OK"}}]}
    FakeHttpTransport.get_payload = {"models": [{"name": "llama3.2:1b"}]}
    monkeypatch.setattr(settings_module, "HttpTransport", FakeHttpTransport)
    return FakeHttpTransport


class TestPersistence:
    def test_honest_defaults(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        settings = manager.settings
        assert settings.active_provider == ""
        assert settings.active_model == ""
        assert settings.default_target == ""
        assert settings.default_mode == "deep"
        assert settings.custom_base_url == ""
        assert settings.authorized_scope == ["localhost", "127.0.0.1"]
        assert manager.ai_ready() is False

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        manager.settings.active_provider = "deepseek"
        manager.settings.active_model = "deepseek-chat"
        manager.settings.default_target = "app.internal.corp"
        manager.settings.default_mode = "fast"
        manager.settings.custom_base_url = "http://localhost:8080/v1"
        manager.settings.authorized_scope = ["app.internal.corp", "10.0.0.0/24"]
        manager.save(manager.settings)
        assert (tmp_path / "settings.json").exists()
        assert not (tmp_path / "settings.json.tmp").exists()

        reloaded = _manager(tmp_path)
        assert reloaded.settings == manager.settings

    def test_saved_file_is_valid_json(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        manager.save(manager.settings)
        data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert data["active_provider"] == ""
        assert data["authorized_scope"] == ["localhost", "127.0.0.1"]

    def test_legacy_fake_and_gemini_entries_are_dropped(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "active_provider": "fake",
                    "active_model": "simulation-lab",
                    "default_mode": "turbo",
                    "ollama_base_url": "",
                    "authorized_scope": [" A ", "a", "", 3],
                }
            ),
            encoding="utf-8",
        )
        settings = _manager(tmp_path).settings
        assert settings.active_provider == ""
        assert settings.active_model == ""
        assert settings.default_mode == "deep"
        assert settings.ollama_base_url == "http://localhost:11434"
        assert settings.authorized_scope == ["A"]

        path.write_text(
            json.dumps({"active_provider": "gemini", "active_model": "gemini-2.0"}), encoding="utf-8"
        )
        assert _manager(tmp_path).settings.active_provider == ""

    def test_corrupt_settings_file_falls_back_to_defaults(self, tmp_path: Path) -> None:
        (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
        assert _manager(tmp_path).settings == AppSettings()

    def test_fake_selection_is_migrated_and_persisted_once(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "active_provider": "fake",
                    "active_model": "fake",
                    "default_target": "https://mohmed-husain.in/",
                    "default_mode": "fast",
                    "ollama_base_url": "http://localhost:11434/v1",
                    "custom_base_url": "http://localhost:8080/v1",
                    "authorized_scope": ["mohmed-husain.in", "127.0.0.1", "localhost"],
                }
            ),
            encoding="utf-8",
        )
        manager = _manager(tmp_path)
        assert manager.settings.active_provider == ""
        assert manager.settings.active_model == ""
        # The user's real preferences must survive the migration untouched.
        assert manager.settings.default_target == "https://mohmed-husain.in/"
        assert manager.settings.default_mode == "fast"
        assert manager.settings.ollama_base_url == "http://localhost:11434/v1"
        assert manager.settings.custom_base_url == "http://localhost:8080/v1"
        assert manager.settings.authorized_scope == ["mohmed-husain.in", "127.0.0.1", "localhost"]
        assert manager.migration_note is not None
        assert "fake" in manager.migration_note

        persisted = path.read_text(encoding="utf-8")
        assert "fake" not in persisted
        on_disk = json.loads(persisted)
        assert on_disk["active_provider"] == ""
        assert on_disk["active_model"] == ""
        assert on_disk["default_target"] == "https://mohmed-husain.in/"

        # A second load must not rewrite the already-normalized file.
        mtime = path.stat().st_mtime_ns
        second = _manager(tmp_path)
        assert second.settings == manager.settings
        assert path.stat().st_mtime_ns == mtime
        assert second.migration_note is None

    def test_valid_settings_are_not_rewritten(self, tmp_path: Path) -> None:
        writer = _manager(tmp_path)
        writer.settings.active_provider = "deepseek"
        writer.settings.active_model = "deepseek-chat"
        writer.save(writer.settings)
        original = (tmp_path / "settings.json").read_bytes()
        loaded = _manager(tmp_path)
        assert loaded.settings.active_provider == "deepseek"
        assert loaded.settings.active_model == "deepseek-chat"
        assert (tmp_path / "settings.json").read_bytes() == original

    def test_save_never_writes_unsupported_selection(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        manager.save(AppSettings(active_provider="fake", active_model="simulation-lab"))
        persisted = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert persisted["active_provider"] == ""
        assert persisted["active_model"] == ""
        assert "fake" not in (tmp_path / "settings.json").read_text(encoding="utf-8")

    def test_empty_authorized_scope_survives_reload(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        manager.settings.authorized_scope = []
        manager.save(manager.settings)
        assert _manager(tmp_path).settings.authorized_scope == []

    def test_default_path_follows_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        manager = SettingsManager(secret_store=_memory_store())
        assert manager.settings_path == tmp_path / ".bletiner" / "settings.json"
        assert manager.settings == AppSettings()


class TestApiKeys:
    def test_set_get_and_clear(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        manager.set_api_key("deepseek", "  sk-ds-test  ")
        assert manager.get_api_key("deepseek") == "sk-ds-test"
        configured, description = manager.api_key_status("deepseek")
        assert configured is True
        assert BACKEND_MEMORY in description
        manager.set_api_key("deepseek", "")
        assert manager.get_api_key("deepseek") == ""

    def test_environment_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        manager = _manager(tmp_path)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env-deepseek")
        assert manager.get_api_key("deepseek") == "sk-env-deepseek"
        configured, description = manager.api_key_status("deepseek")
        assert configured is True
        assert "DEEPSEEK_API_KEY" in description

    def test_unknown_provider_returns_empty(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        assert manager.get_api_key("gemini") == ""
        with pytest.raises(ProviderError, match="Unsupported provider"):
            manager.set_api_key("gemini", "x")

    def test_storage_error_surfaces_in_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        manager = _manager(tmp_path)

        def broken_get(key: str) -> str | None:
            manager.secrets.last_error = "DPAPI secret is corrupt"
            return None

        monkeypatch.setattr(manager.secrets, "get", broken_get)
        configured, description = manager.api_key_status("openai")
        assert configured is False
        assert "Secret storage error" in description
        assert "corrupt" in description


class TestAiReady:
    def _configure(self, manager: SettingsManager, provider: str, model: str) -> None:
        manager.settings.active_provider = provider
        manager.settings.active_model = model

    def test_false_without_selection(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        assert manager.ai_ready() is False
        self._configure(manager, "openai", "")
        assert manager.ai_ready() is False

    def test_cloud_requires_key(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        self._configure(manager, "openai", "gpt-4o-mini")
        assert manager.ai_ready() is False
        manager.set_api_key("openai", "sk-test")
        assert manager.ai_ready() is True

    def test_ollama_needs_no_key(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        self._configure(manager, "ollama", "llama3.2:1b")
        assert manager.ai_ready() is True

    def test_custom_requires_base_url(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        self._configure(manager, "custom", "local-model")
        assert manager.ai_ready() is False
        manager.settings.custom_base_url = "http://localhost:8080/v1"
        assert manager.ai_ready() is True

    def test_unsupported_provider_never_ready(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        self._configure(manager, "gemini", "gemini-2.0")
        assert manager.ai_ready() is False

    def test_provider_model_ready_helper(self, tmp_path: Path) -> None:
        manager = _manager(tmp_path)
        ready, message = manager.provider_model_ready("openai", "gpt-4o-mini")
        assert ready is False
        assert "No API key" in message
        manager.set_api_key("openai", "sk-test")
        ready, _message = manager.provider_model_ready("openai", "gpt-4o-mini")
        assert ready is True


class TestValidateApiKey:
    def test_requires_a_key(self, tmp_path: Path) -> None:
        ok, message = _manager(tmp_path).validate_api_key("openai", "   ")
        assert ok is False
        assert "No API key" in message

    def test_accepts_valid_key_after_real_request(
        self, tmp_path: Path, fake_transport: type[FakeHttpTransport]
    ) -> None:
        manager = _manager(tmp_path)
        manager.settings.active_provider = "openai"
        manager.settings.active_model = "gpt-4o-mini"
        ok, message = manager.validate_api_key("openai", "sk-valid")
        assert ok is True
        assert "Key accepted" in message
        assert "gpt-4o-mini" in message
        assert fake_transport.instances, "expected a transport instance to be created"
        call = fake_transport.instances[0].post_calls[0]
        assert call[0] == "https://api.openai.com/v1/chat/completions"
        assert call[1]["Authorization"] == "Bearer sk-valid"
        assert call[2]["max_tokens"] <= 8

    def test_rejects_invalid_key_with_honest_failure(
        self, tmp_path: Path, fake_transport: type[FakeHttpTransport]
    ) -> None:
        fake_transport.post_error = AuthenticationError("Authentication failed (HTTP 401)")
        ok, message = _manager(tmp_path).validate_api_key("deepseek", "sk-bad")
        assert ok is False
        assert "401" in message

    def test_requires_active_model_for_probe(
        self, tmp_path: Path, fake_transport: type[FakeHttpTransport]
    ) -> None:
        manager = _manager(tmp_path)
        manager.settings.active_provider = "custom"
        manager.settings.custom_base_url = "http://localhost:8080/v1"
        ok, message = manager.validate_api_key("custom", "sk-x")
        assert ok is False
        assert "model" in message.lower()

    def test_ollama_daemon_validated_with_tags(
        self, tmp_path: Path, fake_transport: type[FakeHttpTransport]
    ) -> None:
        manager = _manager(tmp_path)
        ok, message = manager.validate_api_key("ollama", "")
        assert ok is True
        assert "Ollama daemon reachable" in message
        assert "1 models" in message
        assert fake_transport.instances[0].get_calls[0][0] == "http://localhost:11434/api/tags"

    def test_ollama_offline_is_actionable(
        self, tmp_path: Path, fake_transport: type[FakeHttpTransport]
    ) -> None:
        fake_transport.get_error = ProviderError("Network connection failed calling x: refused")
        ok, message = _manager(tmp_path).validate_api_key("ollama", "")
        assert ok is False
        assert "Ollama is not reachable" in message
        assert "ollama serve" in message


class TestSecretBackends:
    def test_memory_backend_name(self) -> None:
        store = SecretStore(use_keyring=False)
        assert store.backend == BACKEND_MEMORY
        assert secret_backend_name() in ALLOWED_BACKENDS

    def test_keyring_backend_roundtrip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeKeyringModule:
            def __init__(self) -> None:
                self.values: dict[tuple[str, str], str] = {}

            def set_password(self, service: str, name: str, value: str) -> None:
                self.values[(service, name)] = value

            def get_password(self, service: str, name: str) -> str | None:
                return self.values.get((service, name))

            def delete_password(self, service: str, name: str) -> None:
                self.values.pop((service, name), None)

        fake = FakeKeyringModule()
        monkeypatch.setattr("bletiner.services.secrets._load_keyring", lambda: fake)
        store = SecretStore(service="bletiner-test", allow_dpapi=False)
        assert store.backend == BACKEND_KEYRING
        store.set("openai_api_key", "sk-keyring-secret")
        assert store.get("openai_api_key") == "sk-keyring-secret"
        store.delete("openai_api_key")
        assert store.get("openai_api_key") is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI is Windows-only")
    def test_dpapi_fallback_persists_encrypted_blob(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("bletiner.services.secrets._load_keyring", lambda: None)
        secrets_dir = tmp_path / "secrets"
        store = SecretStore(base_dir=secrets_dir)
        assert store.backend == BACKEND_DPAPI

        store.set("openai_api_key", "sk-dpapi-plaintext-marker")
        blob_path = secrets_dir / "openai_api_key.bin"
        assert blob_path.exists()
        assert not blob_path.read_bytes().startswith(b"sk-")
        assert b"sk-dpapi-plaintext-marker" not in blob_path.read_bytes()

        reopened = SecretStore(base_dir=secrets_dir)
        assert reopened.get("openai_api_key") == "sk-dpapi-plaintext-marker"
        assert reopened.get("missing_key") is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI is Windows-only")
    def test_corrupt_dpapi_file_is_reported_not_silently_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("bletiner.services.secrets._load_keyring", lambda: None)
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "openai_api_key.bin").write_bytes(b"not-a-dpapi-blob")
        store = SecretStore(base_dir=secrets_dir)
        assert store.get("openai_api_key") is None
        assert store.last_error is not None
        assert "Bletiner DPAPI" in store.last_error

    def test_settings_manager_uses_injected_store(self, tmp_path: Path) -> None:
        store = SecretStore(use_keyring=False, allow_dpapi=False)
        manager = SettingsManager(settings_path=tmp_path / "settings.json", secret_store=store)
        manager.set_api_key("anthropic", "sk-ant-test")
        assert manager.get_api_key("anthropic") == "sk-ant-test"
        assert store.get("anthropic_api_key") == "sk-ant-test"

    @pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
    def test_settings_never_persist_plaintext_in_settings_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("bletiner.services.secrets._load_keyring", lambda: None)
        store = SecretStore(base_dir=tmp_path / "secrets")
        manager = SettingsManager(settings_path=tmp_path / "settings.json", secret_store=store)
        manager.set_api_key("openai", "sk-never-plaintext")
        manager.save(manager.settings)
        raw = (tmp_path / "settings.json").read_bytes()
        assert b"sk-never-plaintext" not in raw
        for blob in (tmp_path / "secrets").iterdir():
            assert b"sk-never-plaintext" not in blob.read_bytes()

    def test_secret_storage_error_type_exists(self) -> None:
        assert issubclass(SecretStorageError, RuntimeError)
