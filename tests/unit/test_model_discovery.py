"""Tier B: provider model discovery + capability probing (fake transport; zero network)."""

from __future__ import annotations

from typing import Any

import pytest

from bletiner.models.errors import AuthenticationError, ModelUnavailableError, ProviderError
from bletiner.providers.model_discovery import (
    curated_models,
    default_probe_model,
    discover_models,
    models_url,
    probe_model,
)

OPENAI_CHAT_OK = {"choices": [{"message": {"content": "BLETINER_OK"}}]}


class FakeTransport:
    """Records the exact HTTP calls discovery/probing makes and returns scripted payloads."""

    def __init__(
        self,
        *,
        get_payloads: list[Any] | None = None,
        post_payload: Any = None,
        get_error: Exception | None = None,
        post_error: Exception | None = None,
    ) -> None:
        self.get_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []
        self._get_payloads = list(get_payloads or [])
        self._post_payload = post_payload if post_payload is not None else OPENAI_CHAT_OK
        self._get_error = get_error
        self._post_error = post_error

    def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        self.get_calls.append({"url": url, "headers": dict(headers or {})})
        if self._get_error is not None:
            raise self._get_error
        if self._get_payloads:
            return self._get_payloads.pop(0)
        return {}

    def post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
        self.post_calls.append({"url": url, "headers": dict(headers or {}), "body": body})
        if self._post_error is not None:
            raise self._post_error
        return self._post_payload


class TestModelUrls:
    def test_openai_models_endpoint(self) -> None:
        assert models_url("openai") == "https://api.openai.com/v1/models"

    def test_deepseek_models_endpoint(self) -> None:
        assert models_url("deepseek") == "https://api.deepseek.com/models"

    def test_anthropic_models_endpoint(self) -> None:
        assert models_url("anthropic") == "https://api.anthropic.com/v1/models?limit=1000"

    def test_ollama_tags_endpoint(self) -> None:
        assert models_url("ollama") == "http://localhost:11434/api/tags"

    def test_custom_requires_base_url(self) -> None:
        with pytest.raises(ProviderError, match="base URL"):
            models_url("custom")

    def test_custom_uses_given_base_url(self) -> None:
        assert models_url("custom", "http://localhost:8080/v1/") == "http://localhost:8080/v1/models"

    def test_alias_spelling(self) -> None:
        assert models_url("claude") == models_url("anthropic")

    def test_ollama_legacy_v1_suffix_normalized(self) -> None:
        assert models_url("ollama", "http://localhost:11434/v1") == "http://localhost:11434/api/tags"
        assert models_url("ollama", "http://localhost:11434/v1/") == "http://localhost:11434/api/tags"


class TestOpenAILikeDiscovery:
    def test_openai_returns_only_reported_ids(self) -> None:
        transport = FakeTransport(get_payloads=[{"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]}])
        models = discover_models("openai", api_key="sk-test", transport=transport)
        assert [m.model_id for m in models] == ["gpt-4o-mini", "gpt-4o"]
        assert all(m.provider == "openai" for m in models)
        assert all(m.verified is False for m in models)
        assert transport.get_calls[0]["url"] == "https://api.openai.com/v1/models"
        assert transport.get_calls[0]["headers"]["Authorization"] == "Bearer sk-test"

    def test_deepseek_endpoint_and_bearer(self) -> None:
        transport = FakeTransport(get_payloads=[{"data": [{"id": "deepseek-chat"}]}])
        models = discover_models("deepseek", api_key="sk-deepseek", transport=transport)
        assert [m.model_id for m in models] == ["deepseek-chat"]
        assert transport.get_calls[0]["url"] == "https://api.deepseek.com/models"
        assert transport.get_calls[0]["headers"]["Authorization"] == "Bearer sk-deepseek"

    def test_plain_list_payload_supported(self) -> None:
        transport = FakeTransport(get_payloads=[["model-a", "model-b"]])
        models = discover_models("custom", base_url="http://localhost:8080/v1", transport=transport)
        assert [m.model_id for m in models] == ["model-a", "model-b"]

    def test_empty_list_is_an_error(self) -> None:
        transport = FakeTransport(get_payloads=[{"data": []}])
        with pytest.raises(ModelUnavailableError, match="empty"):
            discover_models("openai", api_key="sk-test", transport=transport)

    def test_malformed_payload_is_an_error(self) -> None:
        transport = FakeTransport(get_payloads=[{"unexpected": 1}])
        with pytest.raises(ProviderError, match="Unexpected model-list payload"):
            discover_models("openai", api_key="sk-test", transport=transport)


class TestAnthropicDiscovery:
    def test_headers_and_display_name(self) -> None:
        transport = FakeTransport(
            get_payloads=[
                {
                    "data": [
                        {
                            "id": "claude-sonnet-4-5",
                            "display_name": "Claude Sonnet 4.5",
                            "context_window": 200000,
                        }
                    ],
                    "has_more": False,
                }
            ]
        )
        models = discover_models("anthropic", api_key="sk-ant-test", transport=transport)
        assert [(m.model_id, m.display_name, m.context_window) for m in models] == [
            ("claude-sonnet-4-5", "Claude Sonnet 4.5", 200000)
        ]
        call = transport.get_calls[0]
        assert call["url"] == "https://api.anthropic.com/v1/models?limit=1000"
        assert call["headers"]["x-api-key"] == "sk-ant-test"
        assert call["headers"]["anthropic-version"] == "2023-06-01"

    def test_pagination_followed(self) -> None:
        transport = FakeTransport(
            get_payloads=[
                {"data": [{"id": "model-a"}], "has_more": True, "last_id": "model-a"},
                {"data": [{"id": "model-b"}], "has_more": False},
            ]
        )
        models = discover_models("anthropic", api_key="sk-ant", transport=transport)
        assert [m.model_id for m in models] == ["model-a", "model-b"]
        assert len(transport.get_calls) == 2
        assert "after_id=model-a" in transport.get_calls[1]["url"]


class TestOllamaDiscovery:
    def test_tags_payload_parsed(self) -> None:
        transport = FakeTransport(
            get_payloads=[
                {
                    "models": [
                        {
                            "name": "llama3.2:1b",
                            "model": "llama3.2:1b",
                            "details": {"parameter_size": "1.2B"},
                        },
                        {"name": "qwen2.5:7b", "model": "qwen2.5:7b"},
                    ]
                }
            ]
        )
        models = discover_models("ollama", transport=transport)
        assert [m.model_id for m in models] == ["llama3.2:1b", "qwen2.5:7b"]
        assert models[0].provider == "ollama"
        call = transport.get_calls[0]
        assert call["url"] == "http://localhost:11434/api/tags"
        assert "Authorization" not in call["headers"]


class TestDiscoveryErrors:
    def test_missing_key_is_actionable(self) -> None:
        with pytest.raises(AuthenticationError, match="No API key configured for OpenAI"):
            discover_models("openai", transport=FakeTransport())

    def test_401_becomes_actionable_authentication_error(self) -> None:
        transport = FakeTransport(
            get_error=AuthenticationError("Authentication failed (HTTP 401) for https://x: bad key")
        )
        with pytest.raises(AuthenticationError) as excinfo:
            discover_models("openai", api_key="sk-bad", transport=transport)
        message = str(excinfo.value)
        assert "HTTP 401" in message
        assert "API key" in message or "key" in message
        assert "Settings" in message

    def test_404_points_at_base_url(self) -> None:
        transport = FakeTransport(get_error=ModelUnavailableError("Model or endpoint not found (HTTP 404)"))
        with pytest.raises(ModelUnavailableError) as excinfo:
            discover_models("custom", base_url="http://localhost:9/v1", transport=transport)
        assert "base URL" in str(excinfo.value)

    def test_network_failure_wrapped_with_provider_name(self) -> None:
        transport = FakeTransport(get_error=ProviderError("Network connection failed calling x: refused"))
        with pytest.raises(ProviderError, match="discovery failed for OpenAI"):
            discover_models("openai", api_key="sk-test", transport=transport)

    def test_unsupported_provider(self) -> None:
        with pytest.raises(ProviderError, match="Unsupported provider"):
            discover_models("gemini", transport=FakeTransport())


class TestProbeModel:
    def test_openai_probe_success_and_wire_shape(self) -> None:
        transport = FakeTransport(post_payload=OPENAI_CHAT_OK)
        ok, message = probe_model("openai", "gpt-4o-mini", api_key="sk-test", transport=transport)
        assert ok is True
        assert "gpt-4o-mini" in message
        assert "ms" in message
        call = transport.post_calls[0]
        assert call["url"] == "https://api.openai.com/v1/chat/completions"
        assert call["headers"]["Authorization"] == "Bearer sk-test"
        assert call["body"]["max_tokens"] == 8

    def test_anthropic_probe_wire_shape(self) -> None:
        transport = FakeTransport(post_payload={"content": [{"type": "text", "text": "BLETINER_OK"}]})
        ok, _message = probe_model("anthropic", "claude-sonnet-4-5", api_key="sk-ant", transport=transport)
        assert ok is True
        call = transport.post_calls[0]
        assert call["url"] == "https://api.anthropic.com/v1/messages"
        assert call["headers"]["x-api-key"] == "sk-ant"
        assert call["headers"]["anthropic-version"] == "2023-06-01"

    def test_ollama_probe_wire_shape(self) -> None:
        transport = FakeTransport(post_payload={"message": {"content": "BLETINER_OK"}})
        ok, _message = probe_model("ollama", "llama3.2:1b", transport=transport)
        assert ok is True
        call = transport.post_calls[0]
        assert call["url"] == "http://localhost:11434/api/chat"
        assert call["body"]["stream"] is False

    def test_auth_failure_returns_honest_tuple(self) -> None:
        transport = FakeTransport(post_error=AuthenticationError("Authentication failed (HTTP 401)"))
        ok, message = probe_model("openai", "gpt-4o-mini", api_key="sk-bad", transport=transport)
        assert ok is False
        assert "401" in message
        assert "API key" in message

    def test_unexpected_failure_is_reported_not_raised(self) -> None:
        transport = FakeTransport(post_error=RuntimeError("boom"))
        ok, message = probe_model("openai", "gpt-4o-mini", api_key="sk-test", transport=transport)
        assert ok is False
        assert "boom" in message

    def test_missing_model_id(self) -> None:
        ok, message = probe_model("openai", "", api_key="sk-test", transport=FakeTransport())
        assert ok is False
        assert "No model id" in message

    def test_ollama_probe_needs_no_key(self) -> None:
        transport = FakeTransport(post_payload={"message": {"content": "BLETINER_OK"}})
        ok, _message = probe_model("ollama", "llama3.2:1b", transport=transport)
        assert ok is True
        assert "Authorization" not in transport.post_calls[0]["headers"]


class TestCuratedFallback:
    def test_curated_entries_are_explicitly_unverified(self) -> None:
        models = curated_models("openai")
        assert models, "expected a curated fallback list for cloud providers"
        assert all(m.verified is False for m in models)
        assert all(m.provider == "openai" for m in models)

    def test_no_demo_or_simulation_entries(self) -> None:
        texts = {
            m.model_id.lower() for prov in ("openai", "anthropic", "deepseek") for m in curated_models(prov)
        }
        assert not any("simulation" in text or "demo" in text or "fake" in text for text in texts)

    def test_ollama_and_custom_have_no_curated_invention(self) -> None:
        assert curated_models("ollama") == []
        assert curated_models("custom") == []

    def test_unknown_provider_curated_empty(self) -> None:
        assert curated_models("nope") == []

    def test_default_probe_models_are_real_ids(self) -> None:
        assert default_probe_model("openai") == "gpt-4o-mini"
        assert default_probe_model("deepseek") == "deepseek-chat"
        assert default_probe_model("ollama") == ""
