"""Branching session state tree (DOC-03 section 2, ADR-005).

Checkpoints form an acyclic parent-linked graph supporting rewind, branch and fork, rather than a flat log.
Pure and in-memory; persistence (SQLite/JSON snapshots) is layered on at the storage boundary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    parent_id: str | None
    sequence: int
    state: str
    context_tokens: int
    data: dict[str, Any]
    created_at: datetime


class StateTree:
    def __init__(self) -> None:
        self._nodes: dict[str, Checkpoint] = {}
        self._head: str | None = None
        self._seq = 0

    @property
    def head(self) -> Checkpoint | None:
        return self._nodes[self._head] if self._head else None

    def __len__(self) -> int:
        return len(self._nodes)

    def commit(self, state: str, data: dict[str, Any], *, context_tokens: int = 0) -> Checkpoint:
        """Append a checkpoint as a child of the current head and advance head to it."""
        self._seq += 1
        cp = Checkpoint(
            checkpoint_id=str(uuid.uuid4()),
            parent_id=self._head,
            sequence=self._seq,
            state=state,
            context_tokens=context_tokens,
            data=dict(data),
            created_at=datetime.now(UTC),
        )
        self._nodes[cp.checkpoint_id] = cp
        self._head = cp.checkpoint_id
        return cp

    def get(self, checkpoint_id: str) -> Checkpoint:
        if checkpoint_id not in self._nodes:
            raise KeyError(f"unknown checkpoint {checkpoint_id}")
        return self._nodes[checkpoint_id]

    def rewind_to(self, checkpoint_id: str) -> Checkpoint:
        """Move head back to an existing checkpoint (its subtree is retained for audit)."""
        cp = self.get(checkpoint_id)
        self._head = cp.checkpoint_id
        return cp

    def lineage(self, checkpoint_id: str | None = None) -> list[Checkpoint]:
        """Root-to-node path. Defaults to the current head."""
        cur = checkpoint_id or self._head
        chain: list[Checkpoint] = []
        while cur is not None:
            node = self._nodes[cur]
            chain.append(node)
            cur = node.parent_id
        return list(reversed(chain))

    def children(self, checkpoint_id: str) -> list[Checkpoint]:
        return sorted(
            (n for n in self._nodes.values() if n.parent_id == checkpoint_id), key=lambda n: n.sequence
        )
