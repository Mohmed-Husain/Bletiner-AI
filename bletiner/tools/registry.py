"""Controlled tool registry with 4-tier risk classification (DOC-04 section 6.2, ADR-015, DOC-14 Phase 4).

The autonomous agent may only schedule Tier 1-3 tools, and Tier 3 only in DEEP mode. Any attempt to schedule
a Tier 4 tool raises ``Tier4ToolBlockedError`` — an inviolable, code-enforced gate, never prompt-enforced.
All tool commands are structured ``argv`` (no shell).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from bletiner.models.errors import PolicyViolationError, Tier4ToolBlockedError


class ToolTier(IntEnum):
    PASSIVE = 1  # whois, dig, whatweb, curl -I
    ACTIVE_RECON = 2  # nmap -sT, gobuster (rate-limited), nikto safe
    INTRUSIVE = 3  # nuclei safe templates, ffuf (rate-limited) - DEEP only
    DESTRUCTIVE = 4  # masscan flood, sqlmap exploit, hydra, metasploit - BLOCKED


class InvestigationMode(IntEnum):
    BASIC = 1
    DEEP = 2


@dataclass(frozen=True)
class ToolDef:
    name: str
    tier: ToolTier
    description: str
    argv_template: tuple[str, ...]  # e.g. ("nmap", "-sT", "-T3", "{target}")
    parameters: dict[str, Any] = field(default_factory=dict)
    max_rate_pps: int | None = None  # enforced rate limit for active tools

    def render_argv(self, params: Mapping[str, str]) -> list[str]:
        """Substitute ``{name}`` placeholders with validated params; every element stays one argv token."""
        out: list[str] = []
        for tok in self.argv_template:
            if tok.startswith("{") and tok.endswith("}"):
                key = tok[1:-1]
                if key not in params:
                    raise PolicyViolationError(f"missing tool parameter {key!r} for {self.name}")
                out.append(str(params[key]))
            else:
                out.append(tok)
        return out


class ToolRegistry:
    """Static, version-controlled registry (no dynamic plugin marketplace, DOC-14 section 7)."""

    def __init__(self, tools: list[ToolDef]) -> None:
        self._by_name: dict[str, ToolDef] = {}
        for t in tools:
            if t.name in self._by_name:
                raise ValueError(f"duplicate tool {t.name!r}")
            self._by_name[t.name] = t

    def get(self, name: str) -> ToolDef:
        if name not in self._by_name:
            raise PolicyViolationError(f"unknown tool {name!r} (not in static registry)")
        return self._by_name[name]

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def schedulable(self, mode: InvestigationMode) -> list[ToolDef]:
        """Tools the autonomous loop may schedule in *mode*. Never includes Tier 4."""
        max_tier = ToolTier.INTRUSIVE if mode is InvestigationMode.DEEP else ToolTier.ACTIVE_RECON
        return [t for t in self._by_name.values() if t.tier <= max_tier]

    def assert_schedulable(self, name: str, mode: InvestigationMode) -> ToolDef:
        """Gate called by the planner before queueing any tool. Raises on violation."""
        tool = self.get(name)
        if tool.tier is ToolTier.DESTRUCTIVE:
            raise Tier4ToolBlockedError(
                f"Tier 4 tool {name!r} can never be scheduled by the autonomous loop (ADR-015)"
            )
        if tool.tier is ToolTier.INTRUSIVE and mode is InvestigationMode.BASIC:
            raise PolicyViolationError(f"Tier 3 tool {name!r} requires DEEP mode")
        return tool
