"""Phase 1 gate: SQLite WAL schema, migrations, repositories, FK integrity, artifact tree."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bletiner.models.entities import (
    Confidence,
    Decision,
    Finding,
    Investigation,
    InvestigationStatus,
    NetworkAuditEvent,
    ProviderModelConfig,
    Session,
    SessionStatus,
    Severity,
    TermsAcceptance,
    new_id,
)
from bletiner.models.errors import IntegrityViolationError, MigrationError, RecordNotFoundError, StorageError
from bletiner.storage.artifacts import ArtifactStore
from bletiner.storage.database import CURRENT_SCHEMA_VERSION, Database, Migration
from bletiner.storage.repositories import (
    FindingRepository,
    InvestigationRepository,
    NetworkAuditRepository,
    ProviderConfigRepository,
    SessionRepository,
    TermsAcceptanceRepository,
)

EXPECTED_TABLES = {
    "terms_acceptance",
    "provider_model_config",
    "investigations",
    "scope_targets",
    "sessions",
    "containers",
    "agent_runs",
    "checkpoints",
    "steering_instructions",
    "tasks",
    "tool_executions",
    "message_records",
    "findings",
    "evidence_artifacts",
    "reports",
    "network_audit_logs",
    "audit_log_entries",
}


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    d = await Database.open(tmp_path / "db" / "bletiner.db")
    yield d
    await d.close()


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


async def _seed(db: Database) -> tuple[Investigation, Session]:
    terms = TermsAcceptance(terms_version="1.0.0", terms_hash=_sha("terms"), acceptance_hash=_sha("acc"))
    await TermsAcceptanceRepository(db).add(terms)
    prov = ProviderModelConfig(
        provider_name="fake",
        model_identifier="fake-1",
        display_name="Fake",
        keyring_service_key="bletiner/fake",
        context_window_size=8192,
        max_output_tokens=1024,
    )
    await ProviderConfigRepository(db).add(prov)
    inv = Investigation(title="Lab", primary_target="local-lab.test", terms_acceptance_id=terms.acceptance_id)
    await InvestigationRepository(db).add(inv)
    sess = Session(
        investigation_id=inv.investigation_id,
        provider_config_id=prov.config_id,
        relative_path=f"investigations/{inv.investigation_id}",
    )
    await SessionRepository(db).add(sess)
    return inv, sess


async def test_schema_wal_fk_and_version(db: Database) -> None:
    assert await db.journal_mode() == "wal"
    assert await db.foreign_keys_enabled()
    assert await db.user_version() == CURRENT_SCHEMA_VERSION
    assert set(await db.table_names()) >= EXPECTED_TABLES


async def test_migration_is_idempotent(db: Database) -> None:
    assert await db.migrate([Migration(2, "CREATE TABLE should_not_run(x)")]) == 2
    assert "should_not_run" not in await db.table_names()


async def test_failed_migration_rolls_back(db: Database) -> None:
    bad = Migration(3, "CREATE TABLE half_applied(x INTEGER);\nCREATE TABLE broken (;\n")
    with pytest.raises(MigrationError):
        await db.migrate([bad])
    assert await db.user_version() == 2
    assert "half_applied" not in await db.table_names()


async def test_crud_round_trip(db: Database) -> None:
    inv, sess = await _seed(db)
    invs = InvestigationRepository(db)
    got = await invs.get(inv.investigation_id)
    assert got == inv
    now = datetime.now(UTC)
    await invs.set_status(inv.investigation_id, InvestigationStatus.ACTIVE, now)
    assert (await invs.get(inv.investigation_id)).status is InvestigationStatus.ACTIVE
    assert [i.investigation_id for i in await invs.list_by_status(InvestigationStatus.ACTIVE)] == [
        inv.investigation_id
    ]

    sessions = SessionRepository(db)
    await sessions.set_status(sess.session_id, SessionStatus.RUNNING, now)
    assert (await sessions.get(sess.session_id)).session_status is SessionStatus.RUNNING

    f = Finding(
        investigation_id=inv.investigation_id,
        session_id=sess.session_id,
        stable_finding_key=_sha("k"),
        title="t",
        summary="s",
        category="EXPOSED_SERVICE",
        affected_asset="198.51.100.20",
        affected_port=8443,
        severity=Severity.LOW,
        confidence=Confidence.MEDIUM,
        detection_method="nmap",
    )
    await FindingRepository(db).add(f)
    assert await FindingRepository(db).get(f.finding_id) == f

    ev = NetworkAuditEvent(
        session_id=sess.session_id,
        source_ip="172.30.0.2",
        destination_host="unauthorized-tenant.test",
        destination_ip="198.51.100.10",
        destination_port=443,
        protocol="tls",
        decision=Decision.BLOCKED,
        block_reason="SNI_OUT_OF_SCOPE",
    )
    audits = NetworkAuditRepository(db)
    await audits.add(ev)
    assert await audits.list_for_session(sess.session_id) == [ev]


async def test_missing_records_raise_typed_errors(db: Database) -> None:
    with pytest.raises(RecordNotFoundError):
        await InvestigationRepository(db).get(new_id())
    with pytest.raises(RecordNotFoundError):
        await SessionRepository(db).set_status(new_id(), SessionStatus.RUNNING, datetime.now(UTC))


async def test_foreign_key_violation_is_typed(db: Database) -> None:
    orphan = Investigation(title="x", primary_target="local-lab.test", terms_acceptance_id=new_id())
    with pytest.raises(IntegrityViolationError, match="FOREIGN KEY"):
        await InvestigationRepository(db).add(orphan)


async def test_check_constraint_violation_is_typed(db: Database) -> None:
    _, sess = await _seed(db)
    with pytest.raises(IntegrityViolationError):
        await db.execute(
            "UPDATE sessions SET session_status = 'HACKED' WHERE session_id = ?", (sess.session_id,)
        )


async def test_cascade_delete(db: Database) -> None:
    inv, sess = await _seed(db)
    await InvestigationRepository(db).delete(inv.investigation_id)
    with pytest.raises(RecordNotFoundError):
        await SessionRepository(db).get(sess.session_id)


async def test_concurrent_writes_serialize(db: Database) -> None:
    _, sess = await _seed(db)
    audits = NetworkAuditRepository(db)
    events = [
        NetworkAuditEvent(
            session_id=sess.session_id,
            source_ip="172.30.0.2",
            destination_host=f"h{i}.test",
            destination_port=443,
            protocol="tls",
            decision=Decision.ALLOWED,
        )
        for i in range(200)
    ]
    await asyncio.gather(*(audits.add(e) for e in events))
    assert len(await audits.list_for_session(sess.session_id)) == 200


class TestArtifactStore:
    def test_creates_tree(self, tmp_path: Path) -> None:
        inv_id = new_id()
        p = ArtifactStore(tmp_path).create(inv_id)
        for d in (
            p.workspace / "raw_outputs",
            p.workspace / "artifacts",
            p.workspace / "temp",
            p.checkpoints,
            p.logs,
            p.reports,
            p.evidence,
        ):
            assert d.is_dir()
        assert p.root == (tmp_path / "investigations" / inv_id).resolve()

    @pytest.mark.parametrize("bad", ["../etc", "x", "00000000-0000-0000-0000-00000000000G", ""])
    def test_rejects_bad_ids(self, tmp_path: Path, bad: str) -> None:
        with pytest.raises(StorageError):
            ArtifactStore(tmp_path).paths_for(bad)

    @pytest.mark.parametrize("rel", ["../../etc/shadow", "/etc/passwd", "workspace/../../x"])
    def test_rejects_traversal(self, tmp_path: Path, rel: str) -> None:
        store = ArtifactStore(tmp_path)
        inv_id = new_id()
        store.create(inv_id)
        with pytest.raises(StorageError):
            store.resolve_inside(inv_id, rel)

    def test_rejects_symlink_escape(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path)
        inv_id = new_id()
        p = store.create(inv_id)
        (p.workspace / "link").symlink_to(tmp_path)
        with pytest.raises(StorageError):
            store.resolve_inside(inv_id, "workspace/link/secret")
