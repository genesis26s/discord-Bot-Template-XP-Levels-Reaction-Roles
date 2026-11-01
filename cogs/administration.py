"""Administration, diagnostics, config import/export."""
from __future__ import annotations

import json
import logging
import math
from io import BytesIO
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from services.embed_service import EmbedService
from services.leveling_service import LevelingService
from services.reaction_role_service import ReactionRoleService
from utils.errors import ValidationError
from utils.permissions import require_admin

log = logging.getLogger(__name__)


# Fields that are coerced into ints during import; (lo, hi) bounds.
INT_FIELDS: dict[str, tuple[int, int]] = {
    "xp_min": (1, 1000),
    "xp_max": (1, 1000),
    "xp_cooldown": (0, 86400),
    "leveling_enabled": (0, 1),
    "embeds_enabled": (0, 1),
    "reaction_roles_enabled": (0, 1),
}

# Columns present in exports but not imported (row identity / audit).
SKIP_COLUMNS = {"guild_id", "updated_at"}

MAX_FILE_BYTES = 512 * 1024
SNOWFLAKE_MAX = 2**63 - 1


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #

def _coerce_int(value: Any, lo: int, hi: int, name: str) -> int:
    """Strict int coercion. Rejects bools, floats, and non-numeric strings."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{name} must be an integer (got {value!r})")
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer (got {value!r})")
    if not (lo <= v <= hi):
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return v


def _coerce_id_list(value: Any, name: str) -> list[int]:
    """Validate a JSON list of snowflake IDs."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return [_coerce_int(x, 1, SNOWFLAKE_MAX, name) for x in value]


def _json_container(data: dict, key: str, typ: type, default: Any) -> Any:
    """Fetch a JSON value enforcing an exact type; ``None``/missing → default.

    Unlike ``data.get(k) or default`` this rejects wrong-type *empty*
    containers (e.g. ``[]`` where a dict is expected).
    """
    if key not in data or data[key] is None:
        return default
    v = data[key]
    if not isinstance(v, typ):
        raise ValueError(f"{key} must be a {typ.__name__}")
    return v


async def _gate_admin(interaction: discord.Interaction) -> bool:
    """Defence-in-depth admin check.

    Returns ``True`` if the caller was rejected (a response has been sent).
    This runs *in addition* to ``@require_admin()``; if that decorator is a
    no-op for app_commands (e.g. it uses ``commands.check`` instead of
    ``app_commands.check``) this still enforces the gate.
    """
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used in a server.", ephemeral=True
        )
        return True
    member = interaction.user
    if not isinstance(member, discord.Member):
        await interaction.response.send_message(
            "This command can only be used in a server.", ephemeral=True
        )
        return True
    perms = member.guild_permissions
    if not (perms.administrator or perms.manage_guild):
        await interaction.response.send_message(
            "You need **Administrator** or **Manage Server** to use this command.",
            ephemeral=True,
        )
        return True
    return False


def _verify_require_admin_compat() -> None:
    """Warn at startup if ``require_admin`` looks incompatible with app_commands."""
    try:
        @require_admin()
        async def _probe(_self, _i):  # pragma: no cover - diagnostic only
            return None

        if not hasattr(_probe, "__wrapped__"):
            log.warning(
                "require_admin() does not preserve __wrapped__ (no "
                "functools.wraps). app_commands metadata (describe, "
                "guild_only, …) may be silently dropped. Inline admin "
                "checks are active as a fallback."
            )
    except Exception:  # pragma: no cover - never block setup
        log.exception("Failed to verify require_admin() compatibility")


class Administration(commands.Cog):
    """Config and diagnostics."""

    config = app_commands.Group(
        name="config", description="Server configuration."
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.leveling = LevelingService(bot.db)
        self.embeds = EmbedService(bot.db)
        self.rr = ReactionRoleService(bot.db)

    # ------------------------------------------------------------------ #
    # Reaction-role panel import (best effort)
    # ------------------------------------------------------------------ #

    async def _import_panels(
        self, gid: int, panels: list
    ) -> tuple[int, int, str | None]:
        """Try to restore reaction-role panels.

        Returns ``(imported, skipped, reason_if_unsupported)``.
        """
        if not panels:
            return 0, 0, None
        if not hasattr(self.rr, "import_panel"):
            return 0, len(panels), "import_panel not available on service"
        imported = 0
        for p in panels:
            try:
                await self.rr.import_panel(gid, p)
                imported += 1
            except Exception:
                log.exception("Failed to import rr panel for guild %s", gid)
        return imported, len(panels) - imported, None

    # ------------------------------------------------------------------ #
    # Diagnostics
    # ------------------------------------------------------------------ #

    @app_commands.command(name="permissions", description="Inspect bot permissions.")
    @require_admin()
    async def permissions(self, interaction: discord.Interaction):
        if await _gate_admin(interaction):
            return
        guild = interaction.guild
        if guild is None:  # defensive: _gate_admin guarantees this
            return
        me = guild.me
        if me is None:
            return await interaction.response.send_message(
                "Bot member missing.", ephemeral=True
            )
        p = me.guild_permissions
        embed = discord.Embed(title="🔒 Bot Permissions", color=0x5865F2)
        for perm in (
            "administrator",
            "manage_roles",
            "manage_channels",
            "send_messages",
            "embed_links",
            "add_reactions",
            "read_message_history",
            "use_external_emojis",
        ):
            embed.add_field(
                name=perm.replace("_", " ").title(),
                value="✅" if getattr(p, perm, False) else "❌",
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="diagnostics", description="System health checks.")
    @require_admin()
    async def diagnostics(self, interaction: discord.Interaction):
        if await _gate_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(title="🩺 Diagnostics", color=0x5865F2)

        db = getattr(self.bot, "db", None)
        db_ok = False
        if db is not None and hasattr(db, "ping"):
            try:
                db_ok = bool(await db.ping())
            except Exception:
                log.exception("DB ping failed")
                db_ok = False
        embed.add_field(
            name="Database", value="✅ connected" if db_ok else "❌ down"
        )

        lat = self.bot.latency
        latency_str = (
            "n/a" if (lat is None or math.isnan(lat)) else f"{lat * 1000:.0f} ms"
        )
        embed.add_field(name="Latency", value=latency_str)
        embed.add_field(name="Cogs loaded", value=str(len(self.bot.cogs)))

        guild = interaction.guild
        if guild is not None and guild.me is not None:
            p = guild.me.guild_permissions
            missing = [
                perm
                for perm in (
                    "manage_roles",
                    "send_messages",
                    "embed_links",
                    "add_reactions",
                    "read_message_history",
                )
                if not getattr(p, perm, False)
            ]
            embed.add_field(
                name="Missing perms", value=", ".join(missing) or "none"
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # Config
    # ------------------------------------------------------------------ #

    @config.command(name="view", description="View the server's configuration.")
    @require_admin()
    async def view(self, interaction: discord.Interaction):
        if await _gate_admin(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        cfg = await self.leveling.get_guild_config(guild.id)
        embed = discord.Embed(title="⚙️ Server Configuration", color=0x5865F2)
        items = list(cfg.items())
        for k, v in items[:24]:
            embed.add_field(name=k, value=f"`{v}`", inline=True)
        if len(items) > 24:
            embed.add_field(
                name="…",
                value=f"+{len(items) - 24} more fields (see `/config export`)",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config.command(name="export", description="Export configuration as JSON.")
    @require_admin()
    async def export(self, interaction: discord.Interaction):
        if await _gate_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            return
        gid = guild.id

        # --- saved_embeds: tolerate rows deleted between list() and get() ---
        saved_embeds: list[dict] = []
        for r in await self.embeds.list(gid):
            name = r.get("name") if isinstance(r, dict) else None
            try:
                row = await self.embeds.get(gid, name)
            except Exception:
                log.exception("Failed to read embed %r for export", name)
                continue
            if not row or "data" not in row:
                log.warning(
                    "Embed %r missing while exporting guild %s", name, gid
                )
                continue
            saved_embeds.append({**r, "data": row["data"]})

        # --- reaction-role panels --------------------------------------- #
        try:
            rr_panels = await self.rr.list_panels(gid)
        except Exception:
            log.exception("Failed to list rr panels for export")
            rr_panels = []

        data = {
            "guild_config": await self.leveling.get_guild_config(gid),
            "ignored_channels": await self.leveling.list_ignored_channels(gid),
            "ignored_roles": await self.leveling.list_ignored_roles(gid),
            "level_rewards": await self.leveling.list_rewards(gid),
            "saved_embeds": saved_embeds,
            "rr_panels": rr_panels,
        }

        # --- serialise, tracking any values that needed coercion -------- #
        stringified: set[str] = set()

        def _default(o: Any) -> str:
            stringified.add(type(o).__name__)
            return str(o)

        buf = BytesIO(json.dumps(data, indent=2, default=_default).encode())
        if stringified:
            log.warning(
                "Export stringified non-JSON values (round-trip may fail): %s",
                ", ".join(sorted(stringified)),
            )

        await interaction.followup.send(
            content="Configuration exported.",
            file=discord.File(buf, filename=f"config-{gid}.json"),
            ephemeral=True,
        )

    @config.command(
        name="import",
        description="Import configuration from JSON.",
    )
    @require_admin()
    @app_commands.describe(
        file="JSON file produced by /config export",
        replace="If True, wipe existing ignored-channel/role/reward rows first.",
    )
    async def import_cfg(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        replace: bool = False,
    ):
        if await _gate_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            return
        gid = guild.id

        async def reply(msg: str):
            await interaction.followup.send(msg, ephemeral=True)

        # ----------------------------- read --------------------------- #
        if file.size > MAX_FILE_BYTES:
            return await reply("❌ File too large (max 512 KiB).")
        try:
            raw = await file.read()
            data = json.loads(raw)
        except Exception as e:
            return await reply(f"❌ Invalid JSON: {e}")
        if not isinstance(data, dict):
            return await reply("❌ Invalid JSON: top-level value must be an object.")

        # -------------------------- validation ------------------------ #
        try:
            raw_cfg = _json_container(data, "guild_config", dict, {})
            clean_cfg: dict[str, Any] = {}
            for key, (lo, hi) in INT_FIELDS.items():
                if raw_cfg.get(key) is not None:
                    clean_cfg[key] = _coerce_int(raw_cfg[key], lo, hi, key)
            if raw_cfg.get("announce_mode") in ("channel", "current"):
                clean_cfg["announce_mode"] = raw_cfg["announce_mode"]

            # Report unknown guild_config keys so nothing is silently dropped.
            handled = set(INT_FIELDS) | {"announce_mode"} | SKIP_COLUMNS
            dropped_cfg_keys = sorted(k for k in raw_cfg if k not in handled)

            channels = _coerce_id_list(
                data.get("ignored_channels"), "ignored_channels"
            )
            roles = _coerce_id_list(data.get("ignored_roles"), "ignored_roles")

            raw_rewards = _json_container(data, "level_rewards", list, [])
            rewards: list[tuple[int, int]] = []
            for r in raw_rewards:
                if not isinstance(r, dict):
                    raise ValueError(
                        f"level_rewards entries must be objects (got {r!r})"
                    )
                lvl = _coerce_int(r.get("level"), 1, 1000, "level")
                rid = _coerce_int(r.get("role_id"), 1, SNOWFLAKE_MAX, "role_id")
                rewards.append((lvl, rid))

            raw_embeds = _json_container(data, "saved_embeds", list, [])
            raw_panels = _json_container(data, "rr_panels", list, [])

            # Cross-field check against the *merged* state, so a partial
            # import can't leave the DB with xp_min > xp_max.
            current_cfg = await self.leveling.get_guild_config(gid)
            merged_min = clean_cfg.get("xp_min", current_cfg.get("xp_min"))
            merged_max = clean_cfg.get("xp_max", current_cfg.get("xp_max"))
            if (
                merged_min is not None
                and merged_max is not None
                and merged_min > merged_max
            ):
                raise ValueError(
                    f"xp_min ({merged_min}) must be <= xp_max ({merged_max})"
                )
        except (ValueError, TypeError, AttributeError, KeyError) as e:
            return await reply(f"❌ Validation failed: {e}")

        # ------------------------ database write ---------------------- #
        db = getattr(self.bot, "db", None)
        if db is None or not hasattr(db, "transaction"):
            log.error("bot.db.transaction unavailable during import")
            return await reply("❌ Import failed: database unavailable.")

        try:
            async with db.transaction() as conn:
                await conn.execute(
                    "INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)",
                    (gid,),
                )

                if replace:
                    await conn.execute(
                        "DELETE FROM ignored_channels WHERE guild_id=?", (gid,)
                    )
                    await conn.execute(
                        "DELETE FROM ignored_roles WHERE guild_id=?", (gid,)
                    )
                    await conn.execute(
                        "DELETE FROM level_rewards WHERE guild_id=?", (gid,)
                    )

                if clean_cfg:
                    assignments = ", ".join(f"{k}=?" for k in clean_cfg)
                    await conn.execute(
                        f"UPDATE guild_config SET {assignments}, "
                        f"updated_at=strftime('%s','now') WHERE guild_id=?",
                        (*clean_cfg.values(), gid),
                    )
                elif channels or roles or rewards:
                    # Even when only collection tables change, bump the
                    # audit timestamp.
                    await conn.execute(
                        "UPDATE guild_config SET "
                        "updated_at=strftime('%s','now') WHERE guild_id=?",
                        (gid,),
                    )

                for ch in channels:
                    await conn.execute(
                        "INSERT OR IGNORE INTO ignored_channels "
                        "(guild_id, channel_id) VALUES (?,?)",
                        (gid, ch),
                    )
                for rl in roles:
                    await conn.execute(
                        "INSERT OR IGNORE INTO ignored_roles "
                        "(guild_id, role_id) VALUES (?,?)",
                        (gid, rl),
                    )
                for lvl, rid in rewards:
                    await conn.execute(
                        "INSERT OR IGNORE INTO level_rewards "
                        "(guild_id, level, role_id) VALUES (?,?,?)",
                        (gid, lvl, rid),
                    )
        except Exception:
            log.exception("Config import transaction failed")
            return await reply(
                "❌ Import rolled back due to a database error. "
                "Check the bot logs for details."
            )

        # ------------------------ post-commit ------------------------- #
        # DB changes are already committed. Never let anything below turn a
        # partial success into an "interaction failed" for the user.
        imported_embeds = 0
        failed_embeds = 0
        for e in raw_embeds:
            try:
                await self.embeds.save(
                    gid, e["name"], interaction.user.id, e["data"]
                )
                imported_embeds += 1
            except (ValidationError, KeyError, TypeError):
                failed_embeds += 1
            except Exception:
                log.exception(
                    "Unexpected error importing embed %r",
                    e.get("name") if isinstance(e, dict) else e,
                )
                failed_embeds += 1

        imported_panels, failed_panels, panel_unsupported = (
            await self._import_panels(gid, raw_panels)
        )

        # --------------------------- report --------------------------- #
        lines = [
            f"✅ Configuration imported "
            f"(embeds: {imported_embeds}/{len(raw_embeds)}"
            + (f", {failed_embeds} skipped" if failed_embeds else "")
            + ")."
        ]

        if raw_panels:
            if panel_unsupported:
                lines.append(
                    f"⚠️ {len(raw_panels)} reaction-role panel(s) in the file "
                    f"were **not** restored: `{panel_unsupported}`."
                )
            else:
                lines.append(
                    f"↪️ Reaction-role panels: {imported_panels} imported"
                    + (
                        f", {failed_panels} skipped"
                        if failed_panels
                        else ""
                    )
                    + "."
                )

        if dropped_cfg_keys:
            lines.append(
                "ℹ️ Ignored unrecognised `guild_config` keys: "
                + ", ".join(f"`{k}`" for k in dropped_cfg_keys)
                + "."
            )

        if replace:
            lines.append("🗑️ Existing ignored-channel/role/reward rows were replaced.")

        await reply("\n".join(lines))


async def setup(bot: commands.Bot):
    _verify_require_admin_compat()
    await bot.add_cog(Administration(bot))
