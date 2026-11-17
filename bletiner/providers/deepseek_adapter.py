"""DeepSeek LLM API adapter (DOC-06 / DeepSeek V3 & R1).

Targets https://api.deepseek.com using the OpenAI-compatible chat completions wire protocol.
Supports both DeepSeek-V3 (`deepseek-chat`) and DeepSeek-R1 reasoning models (`deepseek-reasoner`).
"""

from __future__ import annotations

from bletiner.providers.base import ModelProfile, Transport
from bletiner.providers.openai_adapter import OpenAICompatibleAdapter

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekAdapter(OpenAICompatibleAdapter):
    name = "deepseek"

    def __init__(self, transport: Transport, api_key: str | None = None) -> None:
        super().__init__(transport, api_key=api_key)

    def _url(self, profile: ModelProfile) -> str:
        base = (profile.base_url or DEFAULT_DEEPSEEK_BASE_URL).rstrip("/")
        return f"{base}/chat/completions"
