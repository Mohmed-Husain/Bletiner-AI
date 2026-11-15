"""Phase 4 Tier B: provider adapter translation (no network; fake transport)."""

from __future__ import annotations

from typing import Any

import pytest

from bletiner.models.errors import AuthenticationError, ProviderError
from bletiner.providers.anthropic_adapter import AnthropicAdapter
from bletiner.providers.base import AgentMessage, ModelProfile, Role, ToolSpec, ToolUse
from bletiner.providers.openai_adapter import OpenAICompatibleAdapter


class FakeTransport:
    def __init__(self, response: dict[str, Any] | None = None, raise_exc: Exception | None = None) -> None:
        self.response = response or {}
        self.raise_exc = raise_exc
        self.last: dict[str, Any] = {}

    def post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        self.last = {"url": url, "headers": headers, "body": body}
        if self.raise_exc:
            raise self.raise_exc
        return self.response


TOOLS = [ToolSpec("nmap_fast", "scan", {"type": "object", "properties": {"target": {"type": "string"}}})]
MSGS = [AgentMessage(Role.SYSTEM, "sys"), AgentMessage(Role.USER, "scan local-lab.test")]


class TestOpenAIAdapter:
    def test_request_shape_and_auth(self) -> None:
        t = FakeTransport(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                "model": "gpt-x",
            }
        )
        a = OpenAICompatibleAdapter(t)
        prof = ModelProfile("openai", "gpt-x", base_url="https://api.openai.com/v1")
        resp = a.complete(prof, MSGS, tools=TOOLS, api_key="sk-test")
        assert t.last["headers"]["Authorization"] == "Bearer sk-test"
        assert t.last["url"].endswith("/chat/completions")
        assert t.last["body"]["tools"][0]["function"]["name"] == "nmap_fast"
        assert resp.message.content == "ok"
        assert resp.usage.total == 8

    def test_parses_tool_calls(self) -> None:
        t = FakeTransport(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "nmap_fast",
                                        "arguments": '{"target": "local-lab.test"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
        resp = OpenAICompatibleAdapter(t).complete(ModelProfile("openai", "gpt-x"), MSGS, tools=TOOLS)
        assert resp.requested_tools == (ToolUse("c1", "nmap_fast", {"target": "local-lab.test"}),)

    def test_tool_result_round_trips(self) -> None:
        t = FakeTransport({"choices": [{"message": {"content": "done"}}]})
        msgs = [*MSGS, AgentMessage(Role.TOOL, "scan output", tool_call_id="c1")]
        OpenAICompatibleAdapter(t).complete(ModelProfile("openai", "gpt-x"), msgs)
        tool_msg = next(m for m in t.last["body"]["messages"] if m["role"] == "tool")
        assert tool_msg["tool_call_id"] == "c1"

    def test_malformed_response_raises(self) -> None:
        with pytest.raises(ProviderError):
            OpenAICompatibleAdapter(FakeTransport({"nope": 1})).complete(ModelProfile("openai", "m"), MSGS)

    def test_auth_error_mapped(self) -> None:
        a = OpenAICompatibleAdapter(FakeTransport(raise_exc=PermissionError("401")))
        with pytest.raises(AuthenticationError):
            a.complete(ModelProfile("openai", "m"), MSGS, api_key="bad")

    def test_base_url_override_for_ollama(self) -> None:
        t = FakeTransport({"choices": [{"message": {"content": "x"}}]})
        OpenAICompatibleAdapter(t).complete(
            ModelProfile("ollama", "llama3.2:1b", base_url="http://localhost:11434/v1"), MSGS
        )
        assert t.last["url"] == "http://localhost:11434/v1/chat/completions"


class TestAnthropicAdapter:
    def test_system_hoisted_and_tool_use_parsed(self) -> None:
        t = FakeTransport(
            {
                "content": [
                    {"type": "text", "text": "hello"},
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "nmap_fast",
                        "input": {"target": "local-lab.test"},
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 4},
                "stop_reason": "tool_use",
            }
        )
        a = AnthropicAdapter(t)
        resp = a.complete(ModelProfile("anthropic", "claude-x"), MSGS, tools=TOOLS, api_key="k")
        assert t.last["body"]["system"] == "sys"
        assert t.last["headers"]["x-api-key"] == "k"
        assert "system" not in [m.get("role") for m in t.last["body"]["messages"]]
        assert resp.message.content == "hello"
        assert resp.requested_tools[0].name == "nmap_fast"
        assert resp.usage.total == 14

    def test_tool_result_block(self) -> None:
        t = FakeTransport({"content": [{"type": "text", "text": "ok"}]})
        msgs = [*MSGS, AgentMessage(Role.TOOL, "out", tool_call_id="t1")]
        AnthropicAdapter(t).complete(ModelProfile("anthropic", "claude-x"), msgs)
        last_user = t.last["body"]["messages"][-1]
        assert last_user["content"][0]["type"] == "tool_result"
        assert last_user["content"][0]["tool_use_id"] == "t1"
