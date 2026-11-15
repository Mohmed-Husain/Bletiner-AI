"""Per-investigation filesystem layout (DOC-04 section 9.1)."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from bletiner.models.errors import StorageError

WORKSPACE_SUBDIRS = ("raw_outputs", "artifacts", "temp")
INVESTIGATION_SUBDIRS = ("checkpoints", "logs", "reports", "evidence")


@dataclass(frozen=True)
class InvestigationPaths:
    root: Path
    workspace: Path
    checkpoints: Path
    logs: Path
    reports: Path
    evidence: Path


class ArtifactStore:
    """Creates and resolves ``<app_data>/investigations/<id>/...`` trees with traversal protection."""

    def __init__(self, app_data_dir: Path) -> None:
        self._base = (app_data_dir / "investigations").resolve()

    def paths_for(self, investigation_id: str) -> InvestigationPaths:
        try:
            canonical = str(uuid.UUID(investigation_id))
        except ValueError as exc:
            raise StorageError(f"investigation id must be a UUID: {investigation_id!r}") from exc
        if canonical != investigation_id.lower():
            raise StorageError(f"non-canonical investigation id: {investigation_id!r}")
        root = self._base / canonical
        return InvestigationPaths(
            root=root,
            workspace=root / "workspace",
            checkpoints=root / "checkpoints",
            logs=root / "logs",
            reports=root / "reports",
            evidence=root / "evidence",
        )

    def create(self, investigation_id: str) -> InvestigationPaths:
        p = self.paths_for(investigation_id)
        for d in (p.workspace, p.checkpoints, p.logs, p.reports, p.evidence):
            d.mkdir(parents=True, exist_ok=True)
        for sub in WORKSPACE_SUBDIRS:
            (p.workspace / sub).mkdir(exist_ok=True)
        # UID 1000 inside the container must be able to write; on Linux hosts we own it, on Docker Desktop
        # the VM translates ownership. Never world-writable.
        os.chmod(p.workspace, 0o750)
        return p

    def resolve_inside(self, investigation_id: str, relative: str) -> Path:
        """Resolve *relative* under the investigation root, refusing escapes (``..``, absolute, symlinks)."""
        root = self.paths_for(investigation_id).root.resolve()
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            raise StorageError(f"path escapes investigation root: {relative!r}")
        return candidate
