"""Tier B tests for the durable session store and the sessions/findings widgets.

Covers: session.json manifest lifecycle, SQLite rows through Database + repositories, filesystem/SQLite
merge, legacy and malformed artifact tolerance, deletion safety, and offscreen widget behaviour against real
SessionSummary/Finding data.

Every filesystem test runs against tmp_path - the user's real ~/.bletiner directory is never touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bletiner.models.entities import (
    Confidence,
    Finding,
    Severity,
    VerificationStatus,
    new_id,
)
from bletiner.models.errors import StorageError
from bletiner.services.session_store import (
    COMPLETED_STATUS,
    ERROR_STATUS,
    LIVE_STATUS,
    STOPPED_STATUS,
    SessionStore,
    SessionSummary,
    read_json_object,
)
from bletiner.storage.database import CURRENT_SCHEMA_VERSION, Database


def _finding(
    investigation_id: str,
    session_id: str,
    *,
    key: str = "key-1",
    status: VerificationStatus = VerificationStatus.VERIFIED,
    title: str = "Open SSH banner",
    severity: Severity = Severity.LOW,
) -> Finding:
    return Finding(
        investigation_id=investigation_id,
        session_id=session_id,
        stable_finding_key=hashlib.sha256(key.encode("utf-8")).hexdigest(),
        title=title,
        summary="OpenSSH banner discloses the server version.",
        category="EXPOSED_SERVICE",
        affected_asset="lab.test",
        affected_port=22,
        severity=severity,
        confidence=Confidence.HIGH,
        verification_status=status,
        detection_method="nmap",
        remediation_guidance="Disable version banners or restrict access.",
    )


async def _db_rows(data_dir: Path, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
    db = await Database.open(data_dir / "bletiner.db", migrate=False)
    try:
        return await db.fetchall(sql, params)
    finally:
        await db.close()


@pytest.fixture
async def store(tmp_path: Path) -> Any:
    instance = SessionStore(tmp_path)
    await instance.open()
    try:
        yield instance
    finally:
        await instance.close()


def _write_manifest(root: Path, payload: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "session.json"
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return manifest


def _sqlite_scalar(path: Path, sql: str) -> Any:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(sql).fetchone()
        return row[0] if row is not None else None
    finally:
        connection.close()


class TestCreation:
    async def test_open_creates_database_and_directories(self, tmp_path: Path) -> None:
        instance = SessionStore(tmp_path)
        await instance.open()
        try:
            assert instance.db_path.is_file()
            assert (tmp_path / "investigations").is_dir()
            assert instance.db_error is None
        finally:
            await instance.close()

    async def test_create_writes_manifest_and_db_rows(self, store: SessionStore, tmp_path: Path) -> None:
        record = await store.create(
            target="lab.test", mode="deep", engine="local", provider="", model="", objective="audit lab"
        )
        assert store.db_error is None
        manifest_path = record.paths.root / "session.json"
        assert manifest_path.is_file()
        manifest = read_json_object(manifest_path)
        assert manifest is not None
        assert manifest["investigation_id"] == record.investigation_id
        assert manifest["session_id"] == record.session_id
        assert manifest["target"] == "lab.test"
        assert manifest["mode"] == "deep"
        assert manifest["engine"] == "local"
        assert manifest["status"] == LIVE_STATUS
        assert manifest["objective"] == "audit lab"
        assert manifest["findings_count"] == 0
        assert manifest["started_at"] is not None
        assert manifest["completed_at"] is None

        investigations = await _db_rows(tmp_path, "SELECT * FROM investigations")
        assert len(investigations) == 1
        assert investigations[0]["primary_target"] == "lab.test"
        assert investigations[0]["status"] == "ACTIVE"
        assert investigations[0]["investigation_depth"] == "DEEP"

        sessions = await _db_rows(tmp_path, "SELECT * FROM sessions")
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == record.session_id
        assert sessions[0]["session_status"] == "RUNNING"
        assert sessions[0]["relative_path"] == f"investigations/{record.investigation_id}"


class TestFindings:
    async def test_record_finding_updates_manifest_and_db(self, store: SessionStore, tmp_path: Path) -> None:
        record = await store.create(target="lab.test", mode="basic", engine="local")
        finding = _finding(record.investigation_id, record.session_id)
        await store.record_finding(finding)

        manifest = read_json_object(record.paths.root / "session.json")
        assert manifest is not None
        assert manifest["findings_count"] == 1
        assert manifest["findings_confirmed"] == 1
        artifact = json.loads((record.paths.evidence / "findings.json").read_text(encoding="utf-8"))
        assert len(artifact) == 1
        assert artifact[0]["title"] == "Open SSH banner"

        rows = await _db_rows(tmp_path, "SELECT * FROM findings")
        assert len(rows) == 1
        assert rows[0]["severity"] == "LOW"
        loaded = await store.load_findings(record.investigation_id)
        assert [finding.title for finding in loaded] == ["Open SSH banner"]

    async def test_record_finding_deduplicates_stable_key(self, store: SessionStore, tmp_path: Path) -> None:
        record = await store.create(target="lab.test", mode="basic", engine="local")
        await store.record_finding(_finding(record.investigation_id, record.session_id))
        await store.record_finding(_finding(record.investigation_id, record.session_id))
        artifact = json.loads((record.paths.evidence / "findings.json").read_text(encoding="utf-8"))
        assert len(artifact) == 1
        rows = await _db_rows(tmp_path, "SELECT * FROM findings")
        assert len(rows) == 1
        manifest = read_json_object(record.paths.root / "session.json")
        assert manifest is not None and manifest["findings_count"] == 1


class TestCompletion:
    async def test_complete_finalizes_manifest_and_db(self, store: SessionStore, tmp_path: Path) -> None:
        record = await store.create(target="lab.test", mode="deep", engine="local")
        (record.paths.reports / "report.md").write_text("# Report", encoding="utf-8")
        (record.paths.reports / "report.html").write_text("<html></html>", encoding="utf-8")
        confirmed = _finding(record.investigation_id, record.session_id, key="confirmed")
        potential = _finding(
            record.investigation_id,
            record.session_id,
            key="potential",
            status=VerificationStatus.DETECTED,
            title="Missing X-Frame-Options",
            severity=Severity.MEDIUM,
        )
        await store.complete(
            record,
            status="completed",
            stop_reason="completed",
            iterations=4,
            tokens_used=321,
            findings=[confirmed, potential],
            reports=[record.paths.reports / "report.md", record.paths.reports / "report.html"],
        )
        manifest = read_json_object(record.paths.root / "session.json")
        assert manifest is not None
        assert manifest["status"] == COMPLETED_STATUS
        assert manifest["stop_reason"] == "completed"
        assert manifest["iterations"] == 4
        assert manifest["tokens_used"] == 321
        assert manifest["findings_count"] == 2
        assert manifest["findings_confirmed"] == 1
        assert manifest["completed_at"] is not None
        assert manifest["duration_s"] is not None
        assert len(manifest["reports"]) == 2

        sessions = await _db_rows(tmp_path, "SELECT * FROM sessions")
        assert sessions[0]["session_status"] == "COMPLETED"
        assert sessions[0]["total_tokens_used"] == 321
        assert sessions[0]["ended_at"] is not None
        investigations = await _db_rows(tmp_path, "SELECT * FROM investigations")
        assert investigations[0]["status"] == "COMPLETED"

        summaries = await store.list_summaries()
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.investigation_id == record.investigation_id
        assert summary.target == "lab.test"
        assert summary.engine == "local"
        assert summary.status == COMPLETED_STATUS
        assert summary.findings_total == 2
        assert summary.findings_confirmed == 1
        assert summary.tokens_used == 321
        assert summary.report_md is not None and summary.report_md.is_file()
        assert summary.report_html is not None and summary.report_html.is_file()

    async def test_complete_with_stopped_status(self, store: SessionStore, tmp_path: Path) -> None:
        record = await store.create(target="lab.test", mode="basic", engine="local")
        await store.complete(
            record,
            status="stopped",
            stop_reason="stopped",
            iterations=1,
            tokens_used=5,
            findings=[],
            reports=None,
        )
        manifest = read_json_object(record.paths.root / "session.json")
        assert manifest is not None and manifest["status"] == STOPPED_STATUS
        investigations = await _db_rows(tmp_path, "SELECT * FROM investigations")
        assert investigations[0]["status"] == "TERMINATED"
        summary = await store.get_summary(record.investigation_id)
        assert summary is not None and summary.status == STOPPED_STATUS

    async def test_fail_records_error_status(self, store: SessionStore, tmp_path: Path) -> None:
        record = await store.create(
            target="lab.test", mode="deep", engine="ai", provider="deepseek", model="deepseek-chat"
        )
        await store.fail(record, error="container went away")
        manifest = read_json_object(record.paths.root / "session.json")
        assert manifest is not None
        assert manifest["status"] == ERROR_STATUS
        assert manifest["error"] == "container went away"
        investigations = await _db_rows(tmp_path, "SELECT * FROM investigations")
        assert investigations[0]["status"] == "FAILED"
        summary = await store.get_summary(record.investigation_id)
        assert summary is not None and summary.status == ERROR_STATUS
        assert summary.engine == "ai"
        assert summary.provider == "deepseek"
        assert summary.model == "deepseek-chat"

    async def test_complete_rejects_unknown_status(self, store: SessionStore) -> None:
        record = await store.create(target="lab.test", mode="basic", engine="local")
        with pytest.raises(StorageError):
            await store.complete(
                record,
                status="definitely-not-a-status",
                stop_reason="?",
                iterations=0,
                tokens_used=0,
                findings=[],
                reports=None,
            )


class TestHistoryMerge:
    async def test_list_summaries_without_database(self, tmp_path: Path) -> None:
        # A manifest written by an older build, with the DB absent entirely.
        investigation_id = new_id()
        root = tmp_path / "investigations" / investigation_id
        _write_manifest(
            root,
            {
                "investigation_id": investigation_id,
                "session_id": new_id(),
                "target": "legacy.test",
                "mode": "deep",
                "stop_reason": "completed",
                "iterations": 2,
                "tokens_used": 42,
                "findings_count": 3,
                "reports": [str(root / "reports" / "report.md")],
            },
        )
        (root / "reports").mkdir(parents=True, exist_ok=True)
        (root / "reports" / "report.md").write_text("# legacy", encoding="utf-8")

        instance = SessionStore(tmp_path)
        try:
            summaries = await instance.list_summaries()
        finally:
            await instance.close()
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.investigation_id == investigation_id
        assert summary.target == "legacy.test"
        assert summary.engine == "legacy"
        assert summary.status == COMPLETED_STATUS
        assert summary.started_at is not None  # falls back to the investigation folder mtime
        assert summary.findings_total == 3
        assert summary.report_md is not None and summary.report_md.is_file()

    async def test_corrupt_database_is_preserved_and_replaced(self, tmp_path: Path) -> None:
        investigation_id = new_id()
        root = tmp_path / "investigations" / investigation_id
        _write_manifest(
            root,
            {
                "investigation_id": investigation_id,
                "session_id": new_id(),
                "target": "corrupt-db.test",
                "mode": "basic",
                "engine": "local",
                "status": "completed",
                "started_at": datetime.now(UTC).isoformat(),
                "completed_at": datetime.now(UTC).isoformat(),
                "findings_count": 0,
                "reports": [],
            },
        )
        (tmp_path / "bletiner.db").write_bytes(b"this is not a sqlite database")

        instance = SessionStore(tmp_path)
        try:
            await instance.open()
            summaries = await instance.list_summaries()
            findings = await instance.load_findings(investigation_id)
        finally:
            await instance.close()
        assert instance.db_error is None
        assert instance.database_notice is not None and "preserved" in instance.database_notice
        preserved = list(tmp_path.glob("bletiner.legacy-*.db"))
        assert len(preserved) == 1
        assert preserved[0].read_bytes() == b"this is not a sqlite database"
        assert len(summaries) == 1 and summaries[0].target == "corrupt-db.test"
        assert findings == []
        assert (tmp_path / "bletiner.db").is_file()
        assert await _db_rows(tmp_path, "SELECT name FROM sqlite_master WHERE type='table'") != []

    async def test_legacy_user_database_is_preserved_and_replaced(self, tmp_path: Path) -> None:
        # Reproduce the user's pre-v2 database: user_version 0 with old unrelated tables.
        legacy_db = tmp_path / "bletiner.db"
        connection = sqlite3.connect(legacy_db)
        try:
            connection.execute("CREATE TABLE sessions (session_id TEXT, status TEXT)")
            connection.execute("INSERT INTO sessions VALUES ('old-session', 'completed')")
            connection.execute("CREATE TABLE execution_plans (plan_id TEXT)")
            connection.commit()
        finally:
            connection.close()
        assert _sqlite_scalar(legacy_db, "PRAGMA user_version") == 0

        investigation_id = new_id()
        root = tmp_path / "investigations" / investigation_id
        _write_manifest(
            root,
            {
                "investigation_id": investigation_id,
                "session_id": new_id(),
                "target": "legacy-user-db.test",
                "mode": "deep",
                "stop_reason": "completed",
                "findings_count": 2,
                "reports": [],
            },
        )

        instance = SessionStore(tmp_path)
        try:
            await instance.open()
            assert instance.db_error is None
            assert instance.database_notice is not None
            preserved = list(tmp_path.glob("bletiner.legacy-*.db"))
            assert len(preserved) == 1
            assert _sqlite_scalar(preserved[0], "PRAGMA user_version") == 0
            assert await instance.list_summaries()
            record = await instance.create(target="fresh.test", mode="basic", engine="local")
            await instance.complete(
                record,
                status="completed",
                stop_reason="completed",
                iterations=1,
                tokens_used=2,
                findings=[],
                reports=None,
            )
        finally:
            await instance.close()
        fresh_tables = await _db_rows(tmp_path, "SELECT name FROM sqlite_master WHERE type='table'")
        names = {str(row["name"]) for row in fresh_tables}
        assert {"investigations", "sessions", "findings"} <= names
        assert (await _db_rows(tmp_path, "PRAGMA user_version"))[0][0] == CURRENT_SCHEMA_VERSION
        sessions = await _db_rows(tmp_path, "SELECT * FROM sessions")
        assert record.session_id in {str(row["session_id"]) for row in sessions}
        # the legacy manifest was upserted into the fresh database as well
        assert investigation_id in {str(row["investigation_id"]) for row in sessions}

    async def test_db_created_at_fills_legacy_manifest_timestamps(
        self, store: SessionStore, tmp_path: Path
    ) -> None:
        record = await store.create(target="stamp.test", mode="basic", engine="local")
        await store.complete(
            record,
            status="completed",
            stop_reason="completed",
            iterations=1,
            tokens_used=1,
            findings=[],
            reports=None,
        )
        manifest_path = record.paths.root / "session.json"
        manifest = read_json_object(manifest_path)
        assert manifest is not None
        manifest.pop("started_at", None)
        manifest.pop("completed_at", None)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        db_created = (await _db_rows(tmp_path, "SELECT * FROM sessions"))[0]["created_at"]
        db_started = datetime.fromisoformat(str(db_created))
        summary = await store.get_summary(record.investigation_id)
        assert summary is not None and summary.started_at is not None
        assert abs((summary.started_at - db_started).total_seconds()) < 5
        merged = await store.list_summaries()
        match = next(s for s in merged if s.investigation_id == record.investigation_id)
        assert match.started_at is not None  # filled from the SQLite session created_at

    async def test_db_history_merges_when_manifest_missing(self, store: SessionStore, tmp_path: Path) -> None:
        record = await store.create(target="db-only.test", mode="basic", engine="local")
        await store.complete(
            record,
            status="completed",
            stop_reason="completed",
            iterations=1,
            tokens_used=7,
            findings=[],
            reports=None,
        )
        (record.paths.root / "session.json").unlink()
        summaries = await store.list_summaries()
        assert any(summary.investigation_id == record.investigation_id for summary in summaries)

    async def test_legacy_manifest_engine_and_status_defaults(self, tmp_path: Path) -> None:
        investigation_id = new_id()
        root = tmp_path / "investigations" / investigation_id
        _write_manifest(
            root,
            {
                "investigation_id": investigation_id,
                "session_id": new_id(),
                "target": "old.test",
                "mode": "deep",
                "stop_reason": "stopped",
                "findings_count": 1,
            },
        )
        instance = SessionStore(tmp_path)
        try:
            summary = await instance.get_summary(investigation_id)
        finally:
            await instance.close()
        assert summary is not None
        assert summary.engine == "legacy"
        assert summary.provider == ""
        assert summary.model == ""
        assert summary.status == STOPPED_STATUS
        assert summary.findings_total == 1
        assert summary.started_at is not None  # folder mtime fallback

    async def test_malformed_artifacts_are_ignored(self, tmp_path: Path) -> None:
        base = tmp_path / "investigations"
        (base / new_id()).mkdir(parents=True)  # directory without session.json
        broken = base / new_id()
        broken.mkdir(parents=True)
        (broken / "session.json").write_text("{not json", encoding="utf-8")
        not_object = base / new_id()
        not_object.mkdir(parents=True)
        (not_object / "session.json").write_text("[1, 2, 3]", encoding="utf-8")
        not_a_dir = base / "README.txt"
        not_a_dir.write_text("ignore me", encoding="utf-8")

        good_id = new_id()
        good = base / good_id
        _write_manifest(
            good,
            {
                "investigation_id": good_id,
                "session_id": new_id(),
                "target": "good.test",
                "mode": "deep",
                "engine": "local",
                "status": "completed",
                "findings_count": 0,
                "reports": [],
            },
        )
        (good / "evidence").mkdir(parents=True, exist_ok=True)
        (good / "evidence" / "findings.json").write_text("{broken", encoding="utf-8")

        instance = SessionStore(tmp_path)
        try:
            summaries = await instance.list_summaries()
            findings = await instance.load_findings(good_id)
            summary = await instance.get_summary("not-a-uuid")
        finally:
            await instance.close()
        assert [s.investigation_id for s in summaries] == [good_id]
        assert findings == []
        assert summary is None

    async def test_list_summaries_sorted_and_limited(self, store: SessionStore) -> None:
        ids: list[str] = []
        for index in range(3):
            record = await store.create(target=f"target-{index}.test", mode="basic", engine="local")
            await store.complete(
                record,
                status="completed",
                stop_reason="completed",
                iterations=1,
                tokens_used=index,
                findings=[],
                reports=None,
            )
            ids.append(record.investigation_id)
        summaries = await store.list_summaries(limit=2)
        assert len(summaries) == 2
        assert summaries[0].investigation_id == ids[-1]
        assert await store.list_summaries(limit=0) == []


class TestDeleteAndLookup:
    async def test_get_summary_round_trip(self, store: SessionStore) -> None:
        record = await store.create(target="lookup.test", mode="deep", engine="local")
        summary = await store.get_summary(record.investigation_id)
        assert summary is not None
        assert summary.target == "lookup.test"
        assert summary.status == LIVE_STATUS
        assert await store.get_summary(new_id()) is None

    async def test_delete_removes_directory_and_rows(self, store: SessionStore, tmp_path: Path) -> None:
        record = await store.create(target="delete.test", mode="basic", engine="local")
        await store.record_finding(_finding(record.investigation_id, record.session_id))
        await store.delete(record.investigation_id)

        assert not record.paths.root.exists()
        assert await store.get_summary(record.investigation_id) is None
        assert await store.list_summaries() == []
        assert await _db_rows(tmp_path, "SELECT * FROM investigations") == []
        assert await _db_rows(tmp_path, "SELECT * FROM sessions") == []
        assert await _db_rows(tmp_path, "SELECT * FROM findings") == []

    async def test_delete_rejects_paths_outside_investigations(
        self, store: SessionStore, tmp_path: Path
    ) -> None:
        with pytest.raises(StorageError):
            await store.delete("../../outside")
        outside = tmp_path / "outside"
        outside.mkdir()
        with pytest.raises(StorageError):
            await store.delete(str(outside))
        assert outside.exists()

    async def test_delete_missing_is_idempotent(self, store: SessionStore) -> None:
        await store.delete(new_id())


# --- offscreen widget tests --------------------------------------------------------------------------------


def _qapp() -> Any:
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return False
    return True


qt_required = pytest.mark.skipif(not _qt_available(), reason="PySide6 (ui extra) not installed")


def _summary(
    *,
    target: str = "lab.test",
    status: str = "completed",
    findings_total: int = 2,
    findings_confirmed: int = 1,
    engine: str = "local",
    model: str = "",
    root: Path | None = None,
    report_md: Path | None = None,
    report_html: Path | None = None,
) -> SessionSummary:
    now = datetime.now(UTC)
    base = root or Path("C:/nonexistent")
    return SessionSummary(
        investigation_id=new_id(),
        session_id=new_id(),
        target=target,
        mode="deep",
        engine=engine,
        provider="",
        model=model,
        status=status,
        findings_total=findings_total,
        findings_confirmed=findings_confirmed,
        started_at=now - timedelta(minutes=5),
        completed_at=now,
        duration_s=300.0,
        tokens_used=1234,
        root=base,
        report_md=report_md,
        report_html=report_html,
        findings_json=None,
    )


@qt_required
class TestSessionsFilesCard:
    def test_empty_state_is_honest(self) -> None:
        _qapp()
        from PySide6.QtCore import Qt

        from bletiner.ui.sessions_card import SessionsFilesCard

        card = SessionsFilesCard()
        try:
            item = card.tree.topLevelItem(0)
            assert item is not None
            assert "No active investigation" in item.text(0)
            # one single empty state: the header labels stay hidden until a run exists
            assert card.target_label.isHidden() is True
            assert card.meta_label.isHidden() is True
            assert card.previous.count() == 1
            assert "No previous sessions" in card.previous.item(0).text()
            assert card.previous.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            assert card.refresh_btn.isEnabled() is False
        finally:
            card.deleteLater()

    def test_renders_real_artifacts_and_sessions(self, tmp_path: Path) -> None:
        _qapp()
        from bletiner.storage.artifacts import ArtifactStore
        from bletiner.ui.sessions_card import SessionsFilesCard

        paths = ArtifactStore(tmp_path).create(new_id())
        (paths.reports / "report.md").write_text("# report", encoding="utf-8")
        (paths.reports / "report.html").write_text("<html></html>", encoding="utf-8")
        (paths.evidence / "findings.json").write_text("[]", encoding="utf-8")
        (paths.logs / "agent.log").write_text("log", encoding="utf-8")

        card = SessionsFilesCard()
        try:
            card.set_current("lab.test", paths, engine="local", status="completed", findings_total=2)
            root_item = card.tree.topLevelItem(0)
            assert root_item is not None
            assert "lab.test" in root_item.text(0)
            assert root_item.childCount() == 3  # reports/evidence/logs
            assert root_item.isExpanded()
            assert card.refresh_btn.isEnabled() is True
            assert "2 findings" in card.meta_label.text()
            assert card.target_label.isHidden() is False
            assert card.meta_label.isHidden() is False

            summary = _summary(
                root=paths.root,
                report_md=paths.reports / "report.md",
                report_html=paths.reports / "report.html",
            )
            opened: list[str] = []
            card.session_open_requested.connect(opened.append)
            card.set_previous([summary])
            assert card.previous.count() == 1
            row = card.previous.itemWidget(card.previous.item(0))
            assert row is not None
            row_text = f"{row.target_label.text()} {row.meta_label.text()}"
            assert "lab.test" in row_text
            assert "COMPLETED" in row_text
            assert "2 findings" in row_text
            card._on_previous_double_clicked(card.previous.item(0))
            assert opened == [summary.investigation_id]

            card.clear_current()
            assert card.refresh_btn.isEnabled() is False
            assert card.target_label.isHidden() is True
            assert "No active investigation" in card.tree.topLevelItem(0).text(0)
        finally:
            card.deleteLater()


@qt_required
class TestFindingsDialog:
    def _findings(self) -> list[Finding]:
        investigation_id = new_id()
        session_id = new_id()
        return [
            _finding(
                investigation_id,
                session_id,
                key="confirmed",
                status=VerificationStatus.VERIFIED,
                title="Verified SQL injection",
            ),
            _finding(
                investigation_id,
                session_id,
                key="potential",
                status=VerificationStatus.DETECTED,
                title="Missing security header",
                severity=Severity.MEDIUM,
            ),
        ]

    def test_empty_dialog(self) -> None:
        _qapp()
        from bletiner.ui.findings_dialog import FindingsDialog

        dialog = FindingsDialog([])
        try:
            assert dialog.table.topLevelItemCount() == 1
            assert dialog.counts.text().startswith("0 finding")
        finally:
            dialog.deleteLater()

    def test_filters_and_detail(self) -> None:
        _qapp()
        from bletiner.ui.findings_dialog import FindingsDialog

        findings = self._findings()
        dialog = FindingsDialog(findings)
        try:
            assert len(dialog.visible_findings) == 2
            dialog.set_filter_state("confirmed")
            assert [f.title for f in dialog.visible_findings] == ["Verified SQL injection"]
            dialog.set_filter_state("potential")
            assert [f.title for f in dialog.visible_findings] == ["Missing security header"]
            dialog.set_filter_state(None)
            dialog.search.setText("header")
            assert [f.title for f in dialog.visible_findings] == ["Missing security header"]
            dialog.search.setText("")
            dialog.table.setCurrentItem(dialog.table.topLevelItem(0))
            assert "Severity" in dialog.detail_meta.text()
            assert dialog.detail_title.text() in {"Verified SQL injection", "Missing security header"}
        finally:
            dialog.deleteLater()

    def test_initial_filter_state(self) -> None:
        _qapp()
        from bletiner.ui.findings_dialog import FindingsDialog

        dialog = FindingsDialog(self._findings(), filter_state="confirmed")
        try:
            assert dialog.filter_state == "confirmed"
            assert len(dialog.visible_findings) == 1
        finally:
            dialog.deleteLater()


@qt_required
class TestSessionsPanel:
    def _wait_for_cards(self, panel: Any, timeout: float = 5.0) -> None:
        app = _qapp()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if panel._cards:
                return
            time.sleep(0.01)
        raise AssertionError("sessions panel did not finish its background load")

    def test_empty_state_and_load(self) -> None:
        _qapp()
        from bletiner.ui.panels.sessions_view import SessionsPanel

        class FakeController:
            def __init__(self, summaries: list[SessionSummary]) -> None:
                self._summaries = summaries
                self.deleted: list[str] = []

            async def sessions(self, limit: int = 100) -> list[SessionSummary]:
                return list(self._summaries)

            async def delete_session(self, investigation_id: str) -> None:
                self.deleted.append(investigation_id)

        panel = SessionsPanel()
        try:
            controller = FakeController(
                [_summary(target="one.test"), _summary(target="two.test", status="stopped")]
            )
            panel.load(controller)
            self._wait_for_cards(panel)
            assert len(panel._cards) == 2
            assert "2 investigations" in panel.status_label.text()

            opened: list[str] = []
            panel.session_open_requested.connect(opened.append)
            first_id = panel._summaries[0].investigation_id
            panel._on_card_open_requested(first_id)
            assert opened == [first_id]

            second = FakeController([])
            panel.load(second)
            app = _qapp()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and panel._cards:
                app.processEvents()
                time.sleep(0.01)
            assert "No investigations yet" in self._collect_text(panel)
        finally:
            panel.deleteLater()

    @staticmethod
    def _collect_text(panel: Any) -> str:
        from PySide6.QtWidgets import QLabel

        texts: list[str] = []
        for label in panel.findChildren(QLabel):
            try:
                texts.append(str(label.text()))
            except RuntimeError:
                continue
        return " ".join(texts)

    def test_controller_error_is_visible(self) -> None:
        _qapp()
        from bletiner.ui.panels.sessions_view import SessionsPanel

        class BrokenController:
            async def sessions(self, limit: int = 100) -> list[SessionSummary]:
                raise RuntimeError("session database unavailable")

        panel = SessionsPanel()
        try:
            panel.load(BrokenController())
            app = _qapp()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                app.processEvents()
                if "Could not load session history" in self._collect_text(panel):
                    break
                time.sleep(0.01)
            assert "Could not load session history" in self._collect_text(panel)
            assert "session database unavailable" in self._collect_text(panel)
        finally:
            panel.deleteLater()
