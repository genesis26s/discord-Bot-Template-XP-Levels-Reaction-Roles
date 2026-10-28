"""Administration, diagnostics, config import/export."""
from __future__ import annotations

import json
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands

from services.embed_service import EmbedService
from services.leveling_service import LevelingService
from services.reaction_role_service import ReactionRoleService
from utils.errors import ValidationError
from utils.permissions import require_admin


class Administration(commands.Cog):
    """Config and diagnostics."""

    config = app_commands.Group(name="config", description="Server configuration.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.leveling = LevelingService(bot.db)
        self.embeds = EmbedService(bot.db)
        self.rr = ReactionRoleService(bot.db)

    @app_commands.command(name="permissions", description="Inspect bot permissions.")
    @require_admin()
    async def permissions(self, interaction: discord.Interaction):
        assert interaction.guild
        me = interaction.guild.me
        if not me:
            return await interaction.response.send_message("Bot member missing.",
                                                           ephemeral=True)
        p = me.guild_permissions
        embed = discord.Embed(title="🔒 Bot Permissions", color=0x5865F2)
        embed.add_field(name="Administrator", value="✅" if p.administrator else "❌")
        for perm in ("manage_roles", "manage_channels", "send_messages",
                     "embed_links", "add_reactions", "read_message_history",
                     "use_external_emojis"):
            embed.add_field(name=perm.replace("_", " ").title(),
                            value="✅" if getattr(p, perm) else "❌")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="diagnostics", description="System health checks.")
    @require_admin()
    async def diagnostics(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🩺 Diagnostics", color=0x5865F2)
        db_ok = await self.bot.db.ping() if hasattr(self.bot, "db") else False
        embed.add_field(name="Database", value="✅ connected" if db_ok else "❌ down")
        embed.add_field(name="Latency", value=f"{self.bot.latency*1000:.0f} ms")
        embed.add_field(name="Cogs loaded", value=str(len(self.bot.cogs)))
        if interaction.guild:
            me = interaction.guild.me
            if me:
                p = me.guild_permissions
                missing = [perm for perm in
                           ("manage_roles", "send_messages", "embed_links",
                            "add_reactions", "read_message_history")
                           if not getattr(p, perm)]
                embed.add_field(name="Missing perms",
                                value=", ".join(missing) or "none")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---- /config ------------------------------------------------------
    @config.command(name="view", description="View the server's configuration.")
    @require_admin()
    async def view(self, interaction: discord.Interaction):
        assert interaction.guild
        cfg = await self.leveling.get_guild_config(interaction.guild.id)
        embed = discord.Embed(title="⚙️ Server Configuration", color=0x5865F2)
        for k, v in cfg.items():
            embed.add_field(name=k, value=f"`{v}`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config.command(name="export", description="Export configuration as JSON.")
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

    @config.command(name="import", description="Import configuration from JSON.")
    @require_admin()
    @app_commands.describe(file="JSON file produced by /config export")
    async def import_cfg(self, interaction: discord.Interaction, file: discord.Attachment):
        assert interaction.guild
        if file.size > 512 * 1024:
            return await interaction.response.send_message("File too large.",
                                                           ephemeral=True)
        try:
            raw = await file.read()
            data = json.loads(raw)
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ Invalid JSON: {e}", ephemeral=True)

        gid = interaction.guild.id
        try:
            cfg = data.get("guild_config") or {}
            fields = {
                k: cfg.get(k) for k in
                ("leveling_enabled", "leveling_channel_id", "announce_mode",
                 "xp_min", "xp_max", "xp_cooldown", "embeds_enabled",
                 "reaction_roles_enabled")
                if cfg.get(k) is not None
            }
            if fields:
                await self.leveling.update_guild_config(gid, **fields)
            for ch in data.get("ignored_channels") or []:
                await self.leveling.ignore_channel(gid, int(ch))
            for rl in data.get("ignored_roles") or []:
                await self.leveling.ignore_role(gid, int(rl))
            for r in data.get("level_rewards") or []:
                await self.leveling.add_reward(gid, int(r["level"]), int(r["role_id"]))
            for e in data.get("saved_embeds") or []:
                try:
                    await self.embeds.save(gid, e["name"],
                                           interaction.user.id, e["data"])
                except ValidationError:
                    continue
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ Import error: {e}", ephemeral=True)

        await interaction.response.send_message("✅ Configuration imported.",
                                                ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Administration(bot))
