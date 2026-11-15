"""Async SQLite connection manager: WAL, foreign keys, transactional migrations (DOC-07 sections 3-8)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import aiosqlite

from bletiner.models.errors import IntegrityViolationError, MigrationError, StorageError

CURRENT_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class Migration:
    target_version: int
    sql: str


def builtin_migrations() -> list[Migration]:
    sql = (
        resources.files("bletiner.storage.migrations")
        .joinpath("v0002_initial_schema.sql")
        .read_text(encoding="utf-8")
    )
    # Greenfield databases go straight to the documented v2 schema (docs/07 section 8).
    return [Migration(target_version=2, sql=sql)]


class Database:
    """Single-writer async SQLite database. Use :meth:`open` to construct."""

    def __init__(self, conn: aiosqlite.Connection, path: str) -> None:
        self._conn = conn
        self.path = path
        # Single serialized writer (DOC-11 Phase 1): one connection, transactions never interleave.
        self._write_lock = asyncio.Lock()

    @classmethod
    async def open(cls, path: str | Path, *, migrate: bool = True) -> Database:
        p = str(path)
        if p != ":memory:":
            Path(p).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(p, isolation_level=None)  # explicit transaction control
        conn.row_factory = aiosqlite.Row
        db = cls(conn, p)
        await db._configure()
        if migrate:
            await db.migrate(builtin_migrations())
        return db

    async def _configure(self) -> None:
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA busy_timeout = 5000")
        await self._conn.execute("PRAGMA synchronous = NORMAL")
        if self.path != ":memory:":
            cur = await self._conn.execute("PRAGMA journal_mode = WAL")
            row = await cur.fetchone()
            if row is None or str(row[0]).lower() != "wal":
                raise StorageError(f"could not enable WAL mode (got {row[0] if row else None!r})")

    async def close(self) -> None:
        await self._conn.close()

    async def journal_mode(self) -> str:
        cur = await self._conn.execute("PRAGMA journal_mode")
        row = await cur.fetchone()
        return str(row[0]) if row else ""

    async def foreign_keys_enabled(self) -> bool:
        cur = await self._conn.execute("PRAGMA foreign_keys")
        row = await cur.fetchone()
        return bool(row and row[0] == 1)

    async def user_version(self) -> int:
        cur = await self._conn.execute("PRAGMA user_version")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def migrate(self, migrations: Iterable[Migration]) -> int:
        """Apply pending migrations, each atomically. On failure the version is left untouched."""
        async with self._write_lock:
            return await self._migrate_locked(migrations)

    async def _migrate_locked(self, migrations: Iterable[Migration]) -> int:
        for m in sorted(migrations, key=lambda m: m.target_version):
            current = await self.user_version()
            if m.target_version <= current:
                continue
            try:
                await self._conn.execute("BEGIN EXCLUSIVE")
                for stmt in _split_sql(m.sql):
                    await self._conn.execute(stmt)
                # PRAGMA cannot be parameterised; target_version is an int from our own migration list.
                await self._conn.execute(f"PRAGMA user_version = {int(m.target_version)}")
                await self._conn.execute("COMMIT")
            except sqlite3.Error as exc:
                await self._conn.execute("ROLLBACK")
                raise MigrationError(f"migration to v{m.target_version} failed: {exc}") from exc
        return await self.user_version()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._write_lock:
            await self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                await self._conn.execute("ROLLBACK")
                raise
            else:
                await self._conn.execute("COMMIT")

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        try:
            async with self.transaction() as conn:
                await conn.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise IntegrityViolationError(str(exc)) from exc

    async def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
        cur = await self._conn.execute(sql, params)
        return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
        cur = await self._conn.execute(sql, params)
        return list(await cur.fetchall())

    async def table_names(self) -> list[str]:
        rows = await self.fetchall("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [str(r[0]) for r in rows]


def _split_sql(script: str) -> list[str]:
    """Split a DDL script into complete statements using sqlite3.complete_statement (handles quotes)."""
    statements: list[str] = []
    buf = ""
    for line in script.splitlines(keepends=True):
        stripped = line.strip()
        if not buf and (not stripped or stripped.startswith("--")):
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            statements.append(buf.strip())
            buf = ""
    if buf.strip():
        raise MigrationError(f"incomplete SQL statement at end of migration: {buf[:80]!r}")
    return statements
