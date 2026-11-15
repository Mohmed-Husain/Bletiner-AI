"""Provider abstraction (``pi-ai`` adaptation, DOC-03 section 1.2, DOC-06).

Internal ``AgentMessage`` structures are decoupled from vendor wire schemas; translation happens strictly at
the adapter boundary. Adapters never import ``bletiner.ui``/``storage``/``container`` (DOC-14 section 2.2).
The network transport is behind a ``Transport`` protocol so unit tests inject deterministic fakes and never
touch a real API (DOC-10 "ZERO Public Network Testing").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class ToolSpec:
    """Vendor-neutral declaration of a callable tool exposed to the model."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass(frozen=True)
class ToolUse:
    """A model's request to call a tool."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AgentMessage:
    """One message in the internal conversation representation."""

    role: Role
    content: str = ""
    tool_uses: tuple[ToolUse, ...] = ()
    tool_call_id: str | None = None  # set on Role.TOOL results


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ProviderResponse:
    """Normalized model output."""

    message: AgentMessage
    usage: Usage = Usage()
    stop_reason: str = "end_turn"
    model: str = ""

    @property
    def requested_tools(self) -> tuple[ToolUse, ...]:
        return self.message.tool_uses


@dataclass(frozen=True)
class ModelProfile:
    provider: str
    model: str
    base_url: str | None = None
    context_window: int = 8192
    max_output_tokens: int = 2048
    supports_tools: bool = True
    supports_streaming: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Transport(Protocol):
    """HTTP transport seam. Real impls POST JSON; tests return scripted payloads."""

    def post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class ProviderAdapter(Protocol):
    """Unified provider interface. Implementations translate to/from one vendor wire schema."""

    name: str

    def complete(
        self,
        profile: ModelProfile,
        messages: list[AgentMessage],
        *,
        tools: list[ToolSpec] | None = None,
        api_key: str | None = None,
    ) -> ProviderResponse: ...
