"""Settings and configuration persistence for Bletiner AI desktop UI."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bletiner.services.secrets import SecretStore

SETTINGS_FILE = Path.home() / ".bletiner" / "settings.json"


@dataclass
class AppSettings:
    active_model: str = "claude-3-5-sonnet"  # "claude-3-5-sonnet", "gpt-4o", "gemini-2.0-flash", "ollama", "fake"
    active_provider: str = "anthropic"  # "anthropic", "openai", "gemini", "ollama", "fake"
    default_target: str = "https://mohmed-husain.in/"
    default_mode: str = "deep"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "llama3"
    use_docker: bool = True
    authorized_scope: list[str] = field(default_factory=lambda: ["mohmed-husain.in", "127.0.0.1", "localhost"])


class SettingsManager:
    """Manages persistent configuration and secure API key storage."""

    def __init__(self, settings_path: Path = SETTINGS_FILE) -> None:
        self.settings_path = settings_path
        self.secrets = SecretStore()
        self.settings = self.load()

    def load(self) -> AppSettings:
        if self.settings_path.exists():
            try:
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
                return AppSettings(
                    active_model=data.get("active_model", "claude-3-5-sonnet"),
                    active_provider=data.get("active_provider", "anthropic"),
                    default_target=data.get("default_target", "https://mohmed-husain.in/"),
                    default_mode=data.get("default_mode", "deep"),
                    ollama_base_url=data.get("ollama_base_url", "http://localhost:11434/v1"),
                    ollama_model=data.get("ollama_model", "llama3"),
                    use_docker=data.get("use_docker", True),
                    authorized_scope=data.get(
                        "authorized_scope", ["mohmed-husain.in", "127.0.0.1", "localhost"]
                    ),
                )
            except Exception:
                pass
        return AppSettings()

    def save(self, settings: AppSettings) -> None:
        self.settings = settings
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")

    def get_api_key(self, provider: str) -> str:
        """Retrieve API key from SecretStore, falling back to environment variable."""
        prov_lower = provider.lower()
        if "anthropic" in prov_lower or "claude" in prov_lower:
            return self.secrets.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        if "deepseek" in prov_lower:
            return self.secrets.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
        if "openai" in prov_lower or "gpt" in prov_lower:
            return self.secrets.get("openai_api_key") or os.environ.get("OPENAI_API_KEY", "")
        if "gemini" in prov_lower:
            return self.secrets.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
        return ""

    def set_api_key(self, provider: str, key: str) -> None:
        """Store API key in SecretStore."""
        prov_lower = provider.lower()
        if "anthropic" in prov_lower or "claude" in prov_lower:
            self.secrets.set("anthropic_api_key", key)
        elif "deepseek" in prov_lower:
            self.secrets.set("deepseek_api_key", key)
        elif "openai" in prov_lower or "gpt" in prov_lower:
            self.secrets.set("openai_api_key", key)
        elif "gemini" in prov_lower:
            self.secrets.set("gemini_api_key", key)


_global_settings_manager: SettingsManager | None = None


def get_settings_manager() -> SettingsManager:
    global _global_settings_manager
    if _global_settings_manager is None:
        _global_settings_manager = SettingsManager()
    return _global_settings_manager
