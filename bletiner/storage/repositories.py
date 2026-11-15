"""Typed repositories over the v2 schema."""

from __future__ import annotations

from datetime import datetime

import aiosqlite

from bletiner.models.entities import (
    Finding,
    Investigation,
    InvestigationStatus,
    NetworkAuditEvent,
    ProviderModelConfig,
    Session,
    SessionStatus,
    TermsAcceptance,
)
from bletiner.models.errors import RecordNotFoundError
from bletiner.storage.database import Database


def _ts(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _row(row: aiosqlite.Row) -> dict[str, object]:
    return {k: row[k] for k in row.keys()}  # noqa: SIM118 - aiosqlite.Row is not a Mapping


class TermsAcceptanceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, t: TermsAcceptance) -> None:
        await self._db.execute(
            "INSERT INTO terms_acceptance VALUES (?,?,?,?,?)",
            (t.acceptance_id, t.terms_version, t.terms_hash, _ts(t.accepted_at), t.acceptance_hash),
        )


class ProviderConfigRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, c: ProviderModelConfig) -> None:
        await self._db.execute(
            "INSERT INTO provider_model_config VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                c.config_id,
                c.provider_name,
                c.model_identifier,
                c.display_name,
                c.base_url,
                c.keyring_service_key,
                c.context_window_size,
                c.max_output_tokens,
                int(c.supports_tool_call),
                int(c.supports_vision),
                int(c.supports_streaming),
                int(c.is_active),
                _ts(c.created_at),
                _ts(c.updated_at),
            ),
        )


class InvestigationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, inv: Investigation) -> None:
        await self._db.execute(
            "INSERT INTO investigations VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                inv.investigation_id,
                inv.title,
                inv.description,
                inv.primary_target,
                inv.investigation_depth.value,
                inv.status.value,
                _ts(inv.created_at),
                _ts(inv.updated_at),
                _ts(inv.completed_at),
                inv.terms_acceptance_id,
            ),
        )

    async def get(self, investigation_id: str) -> Investigation:
        row = await self._db.fetchone(
            "SELECT * FROM investigations WHERE investigation_id = ?", (investigation_id,)
        )
        if row is None:
            raise RecordNotFoundError(f"investigation {investigation_id} not found")
        return Investigation.model_validate(_row(row))

    async def set_status(self, investigation_id: str, status: InvestigationStatus, now: datetime) -> None:
        await self.get(investigation_id)
        await self._db.execute(
            "UPDATE investigations SET status = ?, updated_at = ? WHERE investigation_id = ?",
            (status.value, _ts(now), investigation_id),
        )

    async def list_by_status(self, status: InvestigationStatus) -> list[Investigation]:
        rows = await self._db.fetchall(
            "SELECT * FROM investigations WHERE status = ? ORDER BY created_at DESC", (status.value,)
        )
        return [Investigation.model_validate(_row(r)) for r in rows]

    async def delete(self, investigation_id: str) -> None:
        await self._db.execute("DELETE FROM investigations WHERE investigation_id = ?", (investigation_id,))


class SessionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, s: Session) -> None:
        await self._db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                s.session_id,
                s.investigation_id,
                s.provider_config_id,
                s.session_status.value,
                s.relative_path,
                s.total_tokens_used,
                s.total_cost_usd,
                _ts(s.created_at),
                _ts(s.updated_at),
                _ts(s.ended_at),
            ),
        )

    async def get(self, session_id: str) -> Session:
        row = await self._db.fetchone("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        if row is None:
            raise RecordNotFoundError(f"session {session_id} not found")
        return Session.model_validate(_row(row))

    async def set_status(self, session_id: str, status: SessionStatus, now: datetime) -> None:
        await self.get(session_id)
        await self._db.execute(
            "UPDATE sessions SET session_status = ?, updated_at = ? WHERE session_id = ?",
            (status.value, _ts(now), session_id),
        )

    async def list_for_investigation(self, investigation_id: str) -> list[Session]:
        rows = await self._db.fetchall(
            "SELECT * FROM sessions WHERE investigation_id = ? ORDER BY created_at", (investigation_id,)
        )
        return [Session.model_validate(_row(r)) for r in rows]


class FindingRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, f: Finding) -> None:
        await self._db.execute(
            "INSERT INTO findings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f.finding_id,
                f.investigation_id,
                f.session_id,
                f.task_id,
                f.stable_finding_key,
                f.title,
                f.summary,
                f.category,
                f.affected_asset,
                f.affected_port,
                f.severity.value,
                f.confidence.value,
                f.verification_status.value,
                f.detection_method,
                f.impact_statement,
                f.remediation_guidance,
                int(f.is_included_in_report),
                _ts(f.first_detected_at),
                _ts(f.last_verified_at),
            ),
        )

    async def get(self, finding_id: str) -> Finding:
        row = await self._db.fetchone("SELECT * FROM findings WHERE finding_id = ?", (finding_id,))
        if row is None:
            raise RecordNotFoundError(f"finding {finding_id} not found")
        return Finding.model_validate(_row(row))

    async def list_for_investigation(self, investigation_id: str) -> list[Finding]:
        rows = await self._db.fetchall(
            "SELECT * FROM findings WHERE investigation_id = ? ORDER BY first_detected_at",
            (investigation_id,),
        )
        return [Finding.model_validate(_row(r)) for r in rows]


class NetworkAuditRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, e: NetworkAuditEvent) -> None:
        await self._db.execute(
            "INSERT INTO network_audit_logs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                e.log_id,
                e.session_id,
                _ts(e.timestamp),
                e.source_ip,
                e.destination_host,
                e.destination_ip,
                e.destination_port,
                e.protocol,
                e.decision.value,
                e.block_reason,
                e.bytes_transferred,
            ),
        )

    async def list_for_session(self, session_id: str) -> list[NetworkAuditEvent]:
        rows = await self._db.fetchall(
            "SELECT * FROM network_audit_logs WHERE session_id = ? ORDER BY timestamp", (session_id,)
        )
        return [NetworkAuditEvent.model_validate(_row(r)) for r in rows]
