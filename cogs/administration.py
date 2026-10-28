"""Administration, diagnostics, config import/export."""
from __future__ import annotations

import json
import logging
from io import BytesIO

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


def _coerce_int(value, lo: int, hi: int, name: str) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer (got {value!r})")
    if not (lo <= v <= hi):
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return v


class Administration(commands.Cog):
    """Config and diagnostics."""

    config = app_commands.Group(name="config",
                                 description="Server configuration.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.leveling = LevelingService(bot.db)
        self.embeds = EmbedService(bot.db)
        self.rr = ReactionRoleService(bot.db)

    @app_commands.command(name="permissions",
                           description="Inspect bot permissions.")
    @require_admin()
    async def permissions(self, interaction: discord.Interaction):
        assert interaction.guild
        me = interaction.guild.me
        if not me:
            return await interaction.response.send_message(
                "Bot member missing.", ephemeral=True)
        p = me.guild_permissions
        embed = discord.Embed(title="🔒 Bot Permissions", color=0x5865F2)
        for perm in ("administrator", "manage_roles", "manage_channels",
                      "send_messages", "embed_links", "add_reactions",
                      "read_message_history", "use_external_emojis"):
            embed.add_field(name=perm.replace("_", " ").title(),
                             value="✅" if getattr(p, perm) else "❌")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="diagnostics",
                           description="System health checks.")
    @require_admin()
    async def diagnostics(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🩺 Diagnostics", color=0x5865F2)
        db_ok = await self.bot.db.ping() if hasattr(self.bot, "db") else False
        embed.add_field(name="Database",
                         value="✅ connected" if db_ok else "❌ down")
        embed.add_field(name="Latency", value=f"{self.bot.latency*1000:.0f} ms")
        embed.add_field(name="Cogs loaded", value=str(len(self.bot.cogs)))
        if interaction.guild and interaction.guild.me:
            p = interaction.guild.me.guild_permissions
            missing = [perm for perm in
                        ("manage_roles", "send_messages", "embed_links",
                         "add_reactions", "read_message_history")
                        if not getattr(p, perm)]
            embed.add_field(name="Missing perms",
                             value=", ".join(missing) or "none")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config.command(name="view",
                     description="View the server's configuration.")
    @require_admin()
    async def view(self, interaction: discord.Interaction):
        assert interaction.guild
        cfg = await self.leveling.get_guild_config(interaction.guild.id)
        embed = discord.Embed(title="⚙️ Server Configuration", color=0x5865F2)
        for k, v in cfg.items():
            embed.add_field(name=k, value=f"`{v}`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config.command(name="export",
                     description="Export configuration as JSON.")
    @require_admin()
    async def export(self, interaction: discord.Interaction):
        assert interaction.guild
        gid = interaction.guild.id
        data = {
            "guild_config": await self.leveling.get_guild_config(gid),
            "ignored_channels": await self.leveling.list_ignored_channels(gid),
            "ignored_roles": await self.leveling.list_ignored_roles(gid),
            "level_rewards": await self.leveling.list_rewards(gid),
            "saved_embeds": [
                {**r, "data": (await self.embeds.get(gid, r["name"]))["data"]}
                for r in await self.embeds.list(gid)
            ],
            "rr_panels": await self.rr.list_panels(gid),
        }
        buf = BytesIO(json.dumps(data, indent=2, default=str).encode())
        await interaction.response.send_message(
            content="Configuration exported.",
            file=discord.File(buf, filename=f"config-{gid}.json"),
            ephemeral=True,
        )

    @config.command(name="import",
                     description="Import configuration from JSON.")
    @require_admin()
    @app_commands.describe(file="JSON file produced by /config export")
    async def import_cfg(self, interaction: discord.Interaction,
                          file: discord.Attachment):
        assert interaction.guild
        if file.size > 512 * 1024:
            return await interaction.response.send_message(
                "File too large (max 512 KiB).", ephemeral=True)
        try:
            data = json.loads(await file.read())
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ Invalid JSON: {e}", ephemeral=True)

        gid = interaction.guild.id

        # --- validate before any write ---
        try:
            raw_cfg = data.get("guild_config") or {}
            clean_cfg: dict = {}
            for key, (lo, hi) in INT_FIELDS.items():
                if raw_cfg.get(key) is not None:
                    clean_cfg[key] = _coerce_int(raw_cfg[key], lo, hi, key)
            if raw_cfg.get("announce_mode") in ("channel", "current"):
                clean_cfg["announce_mode"] = raw_cfg["announce_mode"]
            if clean_cfg.get("xp_min") and clean_cfg.get("xp_max") \
                    and clean_cfg["xp_min"] > clean_cfg["xp_max"]:
                raise ValueError("xp_min must be <= xp_max")

            channels = [int(c) for c in (data.get("ignored_channels") or [])]
            roles = [int(r) for r in (data.get("ignored_roles") or [])]
            rewards: list[tuple[int, int]] = []
            for r in data.get("level_rewards") or []:
                lvl = _coerce_int(r.get("level"), 1, 1000, "level")
                rid = _coerce_int(r.get("role_id"), 1, 2**63 - 1, "role_id")
                rewards.append((lvl, rid))
        except (ValueError, TypeError) as e:
            return await interaction.response.send_message(
                f"❌ Validation failed: {e}", ephemeral=True)

        # --- single transaction so a mid-import failure rolls back ---
        try:
            async with self.bot.db.transaction() as conn:
                await conn.execute(
                    "INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)",
                    (gid,),
                )
                if clean_cfg:
                    assignments = ", ".join(f"{k}=?" for k in clean_cfg)
                    await conn.execute(
                        f"UPDATE guild_config SET {assignments}, "
                        f"updated_at=strftime('%s','now') WHERE guild_id=?",
                        (*clean_cfg.values(), gid),
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
        except Exception as e:
            log.exception("Config import transaction failed")
            return await interaction.response.send_message(
                f"❌ Import rolled back: {e}", ephemeral=True)

        # Embeds have their own validator; do them one by one outside the tx.
        imported_embeds = 0
        for e in data.get("saved_embeds") or []:
            try:
                await self.embeds.save(
                    gid, e["name"], interaction.user.id, e["data"])
                imported_embeds += 1
            except (ValidationError, KeyError, TypeError):
                continue

        await interaction.response.send_message(
            f"✅ Configuration imported "
            f"(embeds: {imported_embeds}/{len(data.get('saved_embeds') or [])}).",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Administration(bot))
