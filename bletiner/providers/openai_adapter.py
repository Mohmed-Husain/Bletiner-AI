"""OpenAI-compatible adapter — covers OpenAI, Ollama, LM Studio, DeepSeek, Groq, Together, OpenRouter.

These all speak the ``/chat/completions`` schema; only ``base_url`` and auth differ (DOC-06). Translation is
pure and unit-tested; the HTTP call goes through the injected :class:`~bletiner.providers.base.Transport`.
"""

from __future__ import annotations

import json
from typing import Any

from bletiner.models.errors import AuthenticationError, ModelUnavailableError, ProviderError
from bletiner.providers.base import (
    AgentMessage,
    ModelProfile,
    ProviderResponse,
    Role,
    ToolSpec,
    ToolUse,
    Transport,
    Usage,
)

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatibleAdapter:
    name = "openai"

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def _url(self, profile: ModelProfile) -> str:
        base = (profile.base_url or DEFAULT_BASE_URL).rstrip("/")
        return f"{base}/chat/completions"

    def _to_wire(self, messages: list[AgentMessage], tools: list[ToolSpec] | None) -> dict[str, Any]:
        wire_msgs: list[dict[str, Any]] = []
        for m in messages:
            if m.role is Role.TOOL:
                wire_msgs.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
                continue
            entry: dict[str, Any] = {"role": m.role.value, "content": m.content}
            if m.tool_uses:
                entry["tool_calls"] = [
                    {
                        "id": tu.id,
                        "type": "function",
                        "function": {"name": tu.name, "arguments": json.dumps(tu.arguments)},
                    }
                    for tu in m.tool_uses
                ]
            wire_msgs.append(entry)
        body: dict[str, Any] = {"messages": wire_msgs}
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
        return body

    def _from_wire(self, payload: dict[str, Any], model: str) -> ProviderResponse:
        try:
            choice = payload["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"malformed OpenAI response: {exc}") from exc
        tool_uses: list[ToolUse] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_uses.append(ToolUse(id=str(tc.get("id", "")), name=str(fn.get("name", "")), arguments=args))
        usage_raw = payload.get("usage") or {}
        return ProviderResponse(
            message=AgentMessage(
                role=Role.ASSISTANT, content=msg.get("content") or "", tool_uses=tuple(tool_uses)
            ),
            usage=Usage(
                input_tokens=int(usage_raw.get("prompt_tokens", 0)),
                output_tokens=int(usage_raw.get("completion_tokens", 0)),
            ),
            stop_reason=str(choice.get("finish_reason") or "end_turn"),
            model=str(payload.get("model") or model),
        )

    def complete(
        self,
        profile: ModelProfile,
        messages: list[AgentMessage],
        *,
        tools: list[ToolSpec] | None = None,
        api_key: str | None = None,
    ) -> ProviderResponse:
        body = self._to_wire(messages, tools)
        body["model"] = profile.model
        body["max_tokens"] = profile.max_output_tokens
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            payload = self._transport.post(self._url(profile), headers, body)
        except PermissionError as exc:
            raise AuthenticationError(str(exc)) from exc
        except LookupError as exc:
            raise ModelUnavailableError(str(exc)) from exc
        return self._from_wire(payload, profile.model)
