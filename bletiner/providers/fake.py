"""Deterministic fake provider for agent tests (DOC-10 section "Determinism First").

Returns a scripted sequence of :class:`ProviderResponse` objects, so agent-loop tests are fully
reproducible and never touch a network.
"""

from __future__ import annotations

from collections.abc import Iterable

from bletiner.models.errors import ProviderError
from bletiner.providers.base import (
    AgentMessage,
    ModelProfile,
    ProviderResponse,
    ToolSpec,
)


class FakeProvider:
    name = "fake"

    def __init__(self, scripted: Iterable[ProviderResponse]) -> None:
        self._scripted = list(scripted)
        self._i = 0
        self.calls: list[list[AgentMessage]] = []

    def complete(
        self,
        profile: ModelProfile,
        messages: list[AgentMessage],
        *,
        tools: list[ToolSpec] | None = None,
        api_key: str | None = None,
    ) -> ProviderResponse:
        self.calls.append(list(messages))
        if self._i >= len(self._scripted):
            raise ProviderError("FakeProvider script exhausted")
        resp = self._scripted[self._i]
        self._i += 1
        return resp
