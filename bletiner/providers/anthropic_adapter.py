"""Anthropic Messages API adapter (DOC-06). Distinct wire schema from OpenAI: system is a top-level field,
content is a block list, and tool calls are ``tool_use`` blocks. Translation is pure and unit-tested.
"""

from __future__ import annotations

from typing import Any

from bletiner.models.errors import AuthenticationError, ProviderError
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

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"


class AnthropicAdapter:
    name = "anthropic"

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def _to_wire(self, messages: list[AgentMessage], tools: list[ToolSpec] | None) -> dict[str, Any]:
        system_parts: list[str] = []
        wire: list[dict[str, Any]] = []
        for m in messages:
            if m.role is Role.SYSTEM:
                system_parts.append(m.content)
                continue
            if m.role is Role.TOOL:
                wire.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
                        ],
                    }
                )
                continue
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tu in m.tool_uses:
                blocks.append({"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.arguments})
            wire.append({"role": m.role.value, "content": blocks or [{"type": "text", "text": ""}]})
        body: dict[str, Any] = {"messages": wire}
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
            ]
        return body

    def _from_wire(self, payload: dict[str, Any], model: str) -> ProviderResponse:
        try:
            content = payload["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"malformed Anthropic response: {exc}") from exc
        text_parts: list[str] = []
        tool_uses: list[ToolUse] = []
        for block in content:
            if block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
            elif block.get("type") == "tool_use":
                tool_uses.append(
                    ToolUse(
                        id=str(block.get("id", "")),
                        name=str(block.get("name", "")),
                        arguments=dict(block.get("input") or {}),
                    )
                )
        usage_raw = payload.get("usage") or {}
        return ProviderResponse(
            message=AgentMessage(
                role=Role.ASSISTANT, content="".join(text_parts), tool_uses=tuple(tool_uses)
            ),
            usage=Usage(int(usage_raw.get("input_tokens", 0)), int(usage_raw.get("output_tokens", 0))),
            stop_reason=str(payload.get("stop_reason") or "end_turn"),
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
        headers = {"Content-Type": "application/json", "anthropic-version": API_VERSION}
        if api_key:
            headers["x-api-key"] = api_key
        base = (profile.base_url or DEFAULT_BASE_URL).rstrip("/")
        try:
            payload = self._transport.post(f"{base}/messages", headers, body)
        except PermissionError as exc:
            raise AuthenticationError(str(exc)) from exc
        return self._from_wire(payload, profile.model)
