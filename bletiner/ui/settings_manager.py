"""Settings and configuration persistence for the Bletiner AI desktop UI.

Honest-by-construction:

* provider/model selections are only persisted when they correspond to a provider this build really
  has an adapter for (the legacy "fake"/simulation and "gemini" choices are dropped on load);
* API keys live in :class:`~bletiner.services.secrets.SecretStore` and the active backend
  ("OS keyring" / "Windows DPAPI" / "Memory only") is exposed to the UI verbatim;
* :meth:`SettingsManager.validate_api_key` performs a real, minimal provider request instead of
  claiming success.

Legacy settings files are tolerated: unknown keys are ignored, invalid values fall back to safe
defaults, and nothing is silently invented.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bletiner.models.errors import ConfigurationError, ProviderError
from bletiner.providers.http_transport import HttpTransport
from bletiner.providers.model_discovery import (
    PROVIDERS,
    default_probe_model,
    normalize_ollama_base,
    normalize_provider,
    probe_model,
)
from bletiner.services.secrets import SecretStorageError, SecretStore

SETTINGS_FILE = Path.home() / ".bletiner" / "settings.json"

SUPPORTED_PROVIDERS: tuple[str, ...] = tuple(PROVIDERS)
LOCAL_PROVIDERS = frozenset({"ollama"})
DEFAULT_AUTHORIZED_SCOPE = ("localhost", "127.0.0.1")

_ENV_KEYS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "ollama": "OLLAMA_API_KEY",
    "custom": "CUSTOM_API_KEY",
}
_LEGACY_PROVIDERS = frozenset({"fake", "simulation", "simulated", "simulation-lab", "gemini"})
_LEGACY_MODELS = frozenset({"fake", "simulation-lab", "simulated", "deterministic-simulation-lab"})
_MODES = ("deep", "fast")


@dataclass
class AppSettings:
    """Persisted user settings. Defaults are honest: no provider/model is chosen until the user does."""

    active_model: str = ""
    active_provider: str = ""
    default_target: str = ""
    default_mode: str = "deep"
    ollama_base_url: str = "http://localhost:11434"
    custom_base_url: str = ""
    use_docker: bool = True
    authorized_scope: list[str] = field(default_factory=lambda: list(DEFAULT_AUTHORIZED_SCOPE))


def _text(data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        return default
    return value.strip()[:2048]


def _clean_scope(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return list(DEFAULT_AUTHORIZED_SCOPE)
    cleaned: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, str):
            continue
        value = entry.strip()
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        cleaned.append(value)
    return cleaned


_KNOWN_FIELDS = (
    "active_model",
    "active_provider",
    "default_target",
    "default_mode",
    "ollama_base_url",
    "custom_base_url",
    "use_docker",
    "authorized_scope",
)


def _coerce(data: dict[str, Any], defaults: AppSettings) -> AppSettings:
    provider = _text(data, "active_provider", defaults.active_provider).lower()
    if provider in _LEGACY_PROVIDERS or provider not in SUPPORTED_PROVIDERS:
        provider = ""
    model = _text(data, "active_model", defaults.active_model)
    if not provider or model.lower() in _LEGACY_MODELS:
        model = ""
    mode = _text(data, "default_mode", defaults.default_mode).lower()
    if mode not in _MODES:
        mode = "deep"
    use_docker = data.get("use_docker", defaults.use_docker)
    return AppSettings(
        active_model=model,
        active_provider=provider,
        default_target=_text(data, "default_target", defaults.default_target),
        default_mode=mode,
        ollama_base_url=_text(data, "ollama_base_url", defaults.ollama_base_url) or defaults.ollama_base_url,
        custom_base_url=_text(data, "custom_base_url", defaults.custom_base_url),
        use_docker=use_docker if isinstance(use_docker, bool) else defaults.use_docker,
        authorized_scope=_clean_scope(data.get("authorized_scope", defaults.authorized_scope)),
    )


def _needs_persist(raw: dict[str, Any], normalized: AppSettings) -> bool:
    payload = asdict(normalized)
    if set(raw) - set(_KNOWN_FIELDS):
        return True
    return any(raw.get(key) != payload[key] for key in _KNOWN_FIELDS)


class SettingsManager:
    """Manages persistent configuration and secure API key storage."""

    def __init__(
        self,
        settings_path: Path | None = None,
        *,
        secret_store: SecretStore | None = None,
    ) -> None:
        self.settings_path = (
            Path(settings_path) if settings_path is not None else Path.home() / ".bletiner" / "settings.json"
        )
        self.secrets = secret_store if secret_store is not None else SecretStore()
        self.migration_note: str | None = None
        self.settings = self.load()
        self._persist_migration_if_needed()

    # -- persistence ---------------------------------------------------------------------------------
    def _read_raw(self) -> dict[str, Any] | None:
        if not self.settings_path.exists():
            return None
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def load(self) -> AppSettings:
        raw = self._read_raw()
        if raw is None:
            return AppSettings()
        return _coerce(raw, AppSettings())

    def _persist_migration_if_needed(self) -> None:
        """One-time normalization of a legacy settings file (never leaves unsupported values on disk)."""
        raw = self._read_raw()
        if raw is None:
            return
        normalized = _coerce(raw, AppSettings())
        legacy_provider = str(raw.get("active_provider") or "").strip().lower()
        legacy_model = str(raw.get("active_model") or "").strip().lower()
        if not _needs_persist(raw, normalized):
            self.settings = normalized
            return
        try:
            self.save(normalized)
        except ConfigurationError as exc:
            self.settings = normalized
            self.migration_note = f"normalized settings could not be persisted: {exc}"
            return
        self.settings = normalized
        if legacy_provider in _LEGACY_PROVIDERS or legacy_model in _LEGACY_MODELS:
            self.migration_note = (
                f"removed unsupported provider/model selection ({legacy_provider!r}/{legacy_model!r}); "
                "no model is selected until one is discovered."
            )
        else:
            self.migration_note = "normalized legacy settings values."

    def save(self, settings: AppSettings) -> None:
        """Atomically persist settings; raises :class:`ConfigurationError` when the file cannot be written.

        The payload is normalized first, so unsupported (legacy 'fake'/'gemini') selections can never be
        written back to the user's settings file.
        """
        sanitized = _coerce(asdict(settings), AppSettings())
        payload = json.dumps(asdict(sanitized), indent=2)
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.settings_path.with_name(self.settings_path.name + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.settings_path)
        except OSError as exc:
            raise ConfigurationError(f"Cannot save settings to {self.settings_path}: {exc}") from exc
        self.settings = sanitized

    # -- credentials ---------------------------------------------------------------------------------
    @staticmethod
    def _secret_name(prov: str) -> str:
        return f"{prov}_api_key"

    def get_api_key(self, provider: str) -> str:
        """Stored key first, then the provider's environment variable. Never raises for a missing key."""
        try:
            prov = normalize_provider(provider)
        except ProviderError:
            return ""
        try:
            stored = self.secrets.get(self._secret_name(prov))
        except SecretStorageError:
            stored = None
        if stored:
            return stored.strip()
        env_name = _ENV_KEYS.get(prov)
        if not env_name:
            return ""
        return (os.environ.get(env_name) or "").strip()

    def set_api_key(self, provider: str, key: str) -> None:
        """Store (or, for an empty key, delete) a provider key. Raises on unrecoverable storage errors."""
        prov = normalize_provider(provider)
        cleaned = (key or "").strip()
        if cleaned:
            self.secrets.set(self._secret_name(prov), cleaned)
        else:
            self.secrets.delete(self._secret_name(prov))

    def clear_api_key(self, provider: str) -> None:
        """Remove a stored provider key (stored secrets only; environment variables are untouched)."""
        prov = normalize_provider(provider)
        self.secrets.delete(self._secret_name(prov))

    def api_key_status(self, provider: str) -> tuple[bool, str]:
        """(configured, honest human description) for UI key rows."""
        try:
            prov = normalize_provider(provider)
        except ProviderError as exc:
            return False, str(exc)
        stored = self.secrets.get(self._secret_name(prov))
        if stored:
            return True, f"Configured - stored via {self.secrets.backend}"
        if self.secrets.last_error:
            return False, f"Secret storage error: {self.secrets.last_error}"
        env_name = _ENV_KEYS.get(prov)
        if env_name and (os.environ.get(env_name) or "").strip():
            return True, f"Configured - from environment variable {env_name}"
        if prov in LOCAL_PROVIDERS:
            return False, "No API key required (local daemon)"
        return False, "No key configured"

    # -- readiness / validation ----------------------------------------------------------------------
    def ai_ready(self) -> bool:
        """True when the persisted provider/model selection can really drive the AI engine."""
        provider = (self.settings.active_provider or "").strip().lower()
        model = (self.settings.active_model or "").strip()
        try:
            prov = normalize_provider(provider)
        except ProviderError:
            return False
        if not model:
            return False
        if prov in LOCAL_PROVIDERS:
            return True
        if prov == "custom":
            return bool(self.settings.custom_base_url.strip())
        try:
            return bool(self.get_api_key(prov))
        except SecretStorageError:
            return False

    def provider_model_ready(self, provider: str, model: str) -> tuple[bool, str]:
        """Check a *specific* provider/model pair (used by the New Investigation dialog)."""
        try:
            prov = normalize_provider(provider)
        except ProviderError as exc:
            return False, str(exc)
        if not (model or "").strip():
            return False, f"No model selected for {PROVIDERS[prov]}."
        if prov in LOCAL_PROVIDERS:
            return True, f"{PROVIDERS[prov]} needs no API key."
        if prov == "custom":
            if not self.settings.custom_base_url.strip():
                return False, "The custom provider needs a base URL in Models."
            return True, "Custom endpoint configured."
        try:
            key = self.get_api_key(prov)
        except SecretStorageError as exc:
            return False, f"Secret storage error: {exc}"
        if not key:
            return False, f"No API key configured for {PROVIDERS[prov]}."
        return True, f"{PROVIDERS[prov]} key configured."

    def _probe_model_for(self, prov: str) -> str:
        active_provider = (self.settings.active_provider or "").strip().lower()
        if active_provider == prov and self.settings.active_model.strip():
            return self.settings.active_model.strip()
        return default_probe_model(prov)

    def validate_api_key(self, provider: str, key: str) -> tuple[bool, str]:
        """Perform a real minimal request and return ``(ok, message)``; never raises.

        Cloud providers answer one 8-token chat completion (validates auth *and* model access) with a
        short 8s timeout. Ollama is validated with its ``/api/tags`` daemon endpoint instead.
        """
        try:
            prov = normalize_provider(provider)
        except ProviderError as exc:
            return False, str(exc)
        cleaned = (key or "").strip()
        if prov in LOCAL_PROVIDERS:
            return self._validate_ollama()
        if not cleaned:
            return False, f"No API key to validate for {PROVIDERS[prov]}. Enter a key first."
        model = self._probe_model_for(prov)
        if not model:
            return False, (
                f"Select a model for {PROVIDERS[prov]} on the Models page first; "
                "the key is validated with a minimal inference request against that model."
            )
        base_url = self.settings.custom_base_url.strip() or None if prov == "custom" else None
        transport = HttpTransport(timeout_s=8.0)
        ok, message = probe_model(prov, model, api_key=cleaned, base_url=base_url, transport=transport)
        if ok:
            return True, f"Key accepted - {message}"
        return False, message

    def _validate_ollama(self) -> tuple[bool, str]:
        base = normalize_ollama_base(self.settings.ollama_base_url)
        transport = HttpTransport(timeout_s=8.0)
        started = time.perf_counter()
        try:
            payload = transport.get(f"{base}/api/tags", {"Accept": "application/json"})
        except ProviderError as exc:
            return False, f"Ollama is not reachable at {base}: {exc}. Start it with 'ollama serve'."
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        models = payload.get("models") if isinstance(payload, dict) else None
        count = len(models) if isinstance(models, list) else 0
        if count == 0:
            return True, (
                f"Ollama daemon reachable at {base} in {elapsed_ms:.0f} ms, but no models are pulled. "
                "Run 'ollama pull <model>' and rediscover."
            )
        return True, f"Ollama daemon reachable at {base} in {elapsed_ms:.0f} ms ({count} models available)."


_global_settings_manager: SettingsManager | None = None


def get_settings_manager() -> SettingsManager:
    global _global_settings_manager
    if _global_settings_manager is None:
        _global_settings_manager = SettingsManager()
    return _global_settings_manager


def reset_settings_manager() -> None:
    """Drop the process-wide manager (tests and explicit settings reloads)."""
    global _global_settings_manager
    _global_settings_manager = None
