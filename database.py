"""Async SQLite wrapper with schema migration."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Optional

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id           INTEGER PRIMARY KEY,
    leveling_enabled   INTEGER NOT NULL DEFAULT 1,
    leveling_channel_id INTEGER,
    announce_mode      TEXT    NOT NULL DEFAULT 'channel',
    xp_min             INTEGER NOT NULL DEFAULT 15,
    xp_max             INTEGER NOT NULL DEFAULT 25,
    xp_cooldown        INTEGER NOT NULL DEFAULT 60,
    embeds_enabled     INTEGER NOT NULL DEFAULT 1,
    reaction_roles_enabled INTEGER NOT NULL DEFAULT 1,
    created_at         INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at         INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS user_levels (
    guild_id       INTEGER NOT NULL,
    user_id        INTEGER NOT NULL,
    xp             INTEGER NOT NULL DEFAULT 0 CHECK(xp >= 0),
    level          INTEGER NOT NULL DEFAULT 0 CHECK(level >= 0),
    total_messages INTEGER NOT NULL DEFAULT 0 CHECK(total_messages >= 0),
    last_xp_at     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_user_levels_rank
    ON user_levels(guild_id, level DESC, xp DESC);

CREATE TABLE IF NOT EXISTS ignored_channels (
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS ignored_roles (
    guild_id INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS level_rewards (
    guild_id INTEGER NOT NULL,
    level    INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, level, role_id)
);

CREATE TABLE IF NOT EXISTS saved_embeds (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    author_id  INTEGER NOT NULL,
    data       TEXT    NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(guild_id, name)
);

CREATE TABLE IF NOT EXISTS reaction_role_panels (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       INTEGER NOT NULL,
    channel_id     INTEGER NOT NULL,
    message_id     INTEGER,
    name           TEXT    NOT NULL,
    mode           TEXT    NOT NULL DEFAULT 'react',
    max_selections INTEGER NOT NULL DEFAULT 0,
    description    TEXT,
    created_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(guild_id, name)
);

CREATE TABLE IF NOT EXISTS reaction_role_mappings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_id    INTEGER NOT NULL,
    emoji       TEXT    NOT NULL,
    role_id     INTEGER NOT NULL,
    description TEXT,
    FOREIGN KEY(panel_id) REFERENCES reaction_role_panels(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER,
    user_id    INTEGER,
    action     TEXT NOT NULL,
    details    TEXT,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
"""


class Database:
    """Serialised async SQLite wrapper."""

    def __init__(self, path: str):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row

        # Set pragmas OUTSIDE of executescript — see SQLite docs; inside a
        # transaction they are silently ignored, which disables foreign_keys.
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA synchronous = NORMAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

        # Verify FK enforcement actually took effect.
        rows = await self._conn.execute_fetchall("PRAGMA foreign_keys")
        if not rows or rows[0][0] != 1:
            raise RuntimeError(
                "SQLite foreign keys could not be enabled — "
                "cascade deletes will not work."
            )

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ---- Write helpers --------------------------------------------------
    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        assert self._conn is not None
        async with self._write_lock:
            cur = await self._conn.execute(sql, params)
            await self._conn.commit()
            return cur.lastrowid or 0

    async def execute_rowcount(self, sql: str, params: Iterable[Any] = ()) -> int:
        assert self._conn is not None
        async with self._write_lock:
            cur = await self._conn.execute(sql, params)
            await self._conn.commit()
            return cur.rowcount

    async def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> None:
        assert self._conn is not None
        async with self._write_lock:
            await self._conn.executemany(sql, seq)
            await self._conn.commit()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """All-or-nothing write block. Inside, use the raw connection."""
        assert self._conn is not None
        async with self._write_lock:
            await self._conn.execute("BEGIN")
            try:
                yield self._conn
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise

    # ---- Read helpers ---------------------------------------------------
    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        assert self._conn is not None
        async with self._conn.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        assert self._conn is not None
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def ping(self) -> bool:
        try:
            await self.fetchone("SELECT 1")
            return True
        except Exception:
            return False
