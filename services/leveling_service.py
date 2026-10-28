"""Leveling business logic — no Discord imports."""
from __future__ import annotations

import math
import random
import time
from typing import Any, Iterable, Optional

from database import Database


def total_xp_for_level(level: int) -> int:
    """Total XP required to reach `level` from 0."""
    if level <= 0:
        return 0
    return 5 * level * level + 55 * level


def level_for_xp(xp: int) -> int:
    if xp <= 0:
        return 0
    # 5L^2 + 55L - xp <= 0  →  L = floor((-55 + sqrt(55^2 + 20xp)) / 10)
    disc = 55 * 55 + 20 * xp
    level = int((-55 + math.sqrt(disc)) / 10)
    while total_xp_for_level(level + 1) <= xp:
        level += 1
    while level > 0 and total_xp_for_level(level) > xp:
        level -= 1
    return level


class LevelingService:
    def __init__(self, db: Database, rng: Optional[random.Random] = None):
        self.db = db
        self.rng = rng or random.Random()

    # ---- config -------------------------------------------------------
    async def get_guild_config(self, guild_id: int) -> dict[str, Any]:
        row = await self.db.fetchone(
            "SELECT * FROM guild_config WHERE guild_id=?", (guild_id,)
        )
        if not row:
            await self.db.execute(
                "INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)", (guild_id,)
            )
            row = await self.db.fetchone(
                "SELECT * FROM guild_config WHERE guild_id=?", (guild_id,)
            )
        return row or {}

    async def update_guild_config(self, guild_id: int, **fields: Any) -> None:
        await self.get_guild_config(guild_id)
        allowed = {"leveling_enabled", "leveling_channel_id", "announce_mode",
                   "xp_min", "xp_max", "xp_cooldown", "embeds_enabled",
                   "reaction_roles_enabled"}
        keys = [k for k in fields if k in allowed]
        if not keys:
            return
        assignments = ", ".join(f"{k}=?" for k in keys) + ", updated_at=strftime('%s','now')"
        params = [fields[k] for k in keys] + [guild_id]
        await self.db.execute(
            f"UPDATE guild_config SET {assignments} WHERE guild_id=?", params
        )

    # ---- ignored channels / roles ------------------------------------
    async def ignore_channel(self, guild_id: int, channel_id: int) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO ignored_channels (guild_id, channel_id) VALUES (?,?)",
            (guild_id, channel_id),
        )

    async def unignore_channel(self, guild_id: int, channel_id: int) -> None:
        await self.db.execute(
            "DELETE FROM ignored_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        )

    async def list_ignored_channels(self, guild_id: int) -> list[int]:
        rows = await self.db.fetchall(
            "SELECT channel_id FROM ignored_channels WHERE guild_id=?", (guild_id,)
        )
        return [r["channel_id"] for r in rows]

    async def ignore_role(self, guild_id: int, role_id: int) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO ignored_roles (guild_id, role_id) VALUES (?,?)",
            (guild_id, role_id),
        )

    async def unignore_role(self, guild_id: int, role_id: int) -> None:
        await self.db.execute(
            "DELETE FROM ignored_roles WHERE guild_id=? AND role_id=?",
            (guild_id, role_id),
        )

    async def list_ignored_roles(self, guild_id: int) -> list[int]:
        rows = await self.db.fetchall(
            "SELECT role_id FROM ignored_roles WHERE guild_id=?", (guild_id,)
        )
        return [r["role_id"] for r in rows]

    # ---- role rewards --------------------------------------------------
    async def add_reward(self, guild_id: int, level: int, role_id: int) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO level_rewards (guild_id, level, role_id) VALUES (?,?,?)",
            (guild_id, level, role_id),
        )

    async def remove_reward(self, guild_id: int, level: int, role_id: int) -> None:
        await self.db.execute(
            "DELETE FROM level_rewards WHERE guild_id=? AND level=? AND role_id=?",
            (guild_id, level, role_id),
        )

    async def list_rewards(self, guild_id: int) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM level_rewards WHERE guild_id=? ORDER BY level ASC",
            (guild_id,),
        )

    async def roles_awarded_at(self, guild_id: int, level: int) -> list[int]:
        rows = await self.db.fetchall(
            "SELECT role_id FROM level_rewards WHERE guild_id=? AND level=?",
            (guild_id, level),
        )
        return [r["role_id"] for r in rows]

    # ---- user data ----------------------------------------------------
    async def ensure_user(self, guild_id: int, user_id: int) -> dict[str, Any]:
        row = await self.db.fetchone(
            "SELECT * FROM user_levels WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        if not row:
            await self.db.execute(
                "INSERT OR IGNORE INTO user_levels (guild_id, user_id) VALUES (?,?)",
                (guild_id, user_id),
            )
            row = await self.db.fetchone(
                "SELECT * FROM user_levels WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            )
        return row or {"guild_id": guild_id, "user_id": user_id, "xp": 0, "level": 0,
                       "total_messages": 0, "last_xp_at": 0}

    async def reset_user(self, guild_id: int, user_id: int) -> None:
        await self.db.execute(
            "UPDATE user_levels SET xp=0, level=0, total_messages=0, last_xp_at=0 "
            "WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )

    async def set_user_level(self, guild_id: int, user_id: int, level: int) -> None:
        xp = total_xp_for_level(max(0, level))
        await self.ensure_user(guild_id, user_id)
        await self.db.execute(
            "UPDATE user_levels SET xp=?, level=? WHERE guild_id=? AND user_id=?",
            (xp, max(0, level), guild_id, user_id),
        )

    # ---- XP award logic ------------------------------------------------
    async def try_award_xp(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        member_role_ids: Iterable[int],
        *,
        now: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """Return award info if XP was granted, else None."""
        now = now if now is not None else int(time.time())
        config = await self.get_guild_config(guild_id)
        if not config.get("leveling_enabled"):
            return None

        if await self.db.fetchone(
            "SELECT 1 FROM ignored_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        ):
            return None

        role_ids = list(member_role_ids)
        if role_ids:
            placeholders = ",".join("?" * len(role_ids))
            if await self.db.fetchone(
                f"SELECT 1 FROM ignored_roles WHERE guild_id=? AND role_id IN ({placeholders})",
                (guild_id, *role_ids),
            ):
                return None

        user = await self.ensure_user(guild_id, user_id)
        if now - int(user.get("last_xp_at") or 0) < int(config["xp_cooldown"]):
            return None

        gain = self.rng.randint(int(config["xp_min"]), int(config["xp_max"]))
        old_level = int(user["level"])
        new_xp = int(user["xp"]) + gain
        new_level = level_for_xp(new_xp)

        await self.db.execute(
            "UPDATE user_levels SET xp=?, level=?, total_messages=total_messages+1, "
            "last_xp_at=? WHERE guild_id=? AND user_id=?",
            (new_xp, new_level, now, guild_id, user_id),
        )
        return {
            "xp_gain": gain,
            "new_xp": new_xp,
            "old_level": old_level,
            "new_level": new_level,
            "leveled_up": new_level > old_level,
        }

    # ---- rank ----------------------------------------------------------
    async def get_rank(self, guild_id: int, user_id: int) -> dict[str, Any]:
        user = await self.ensure_user(guild_id, user_id)
        rank_row = await self.db.fetchone(
            "SELECT COUNT(*) + 1 AS rank FROM user_levels "
            "WHERE guild_id=? AND (level > ? OR (level = ? AND xp > ?))",
            (guild_id, user["level"], user["level"], user["xp"]),
        )
        rank = int(rank_row["rank"]) if rank_row else 1
        xp = int(user["xp"])
        lvl = int(user["level"])
        cur = total_xp_for_level(lvl)
        nxt = total_xp_for_level(lvl + 1)
        return {
            "level": lvl,
            "xp": xp,
            "rank": rank,
            "current_level_xp": xp - cur,
            "level_span": nxt - cur,
            "next_level_xp": nxt,
        }

    async def leaderboard(self, guild_id: int, limit: int = 100) -> list[dict]:
        return await self.db.fetchall(
            "SELECT user_id, xp, level, total_messages FROM user_levels "
            "WHERE guild_id=? ORDER BY level DESC, xp DESC LIMIT ?",
            (guild_id, limit),
        )
