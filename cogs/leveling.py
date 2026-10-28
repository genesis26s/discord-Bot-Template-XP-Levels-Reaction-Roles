"""Leveling system: XP on messages, rank, leaderboard, admin commands."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from services.leveling_service import LevelingService, level_for_xp, total_xp_for_level
from utils.permissions import require_admin, user_is_admin
from utils.paginator import Paginator
from utils.validators import validate_positive_int

log = logging.getLogger(__name__)


def progress_bar(current: int, total: int, length: int = 20) -> str:
    if total <= 0:
        return "▰" * length
    filled = int(length * current / total)
    filled = max(0, min(length, filled))
    return "▰" * filled + "▱" * (length - filled)


class Leveling(commands.Cog):
    """Leveling system configuration and rank commands."""

    leveling = app_commands.Group(name="leveling", description="Leveling commands.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        assert hasattr(bot, "db")
        self.service = LevelingService(bot.db)

    # ---- XP on message -------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if not isinstance(message.author, discord.Member):
            return
        try:
            result = await self.service.try_award_xp(
                guild_id=message.guild.id,
                user_id=message.author.id,
                channel_id=message.channel.id,
                member_role_ids=[r.id for r in message.author.roles],
            )
        except Exception:
            log.exception("XP award failed")
            return
        if not result or not result["leveled_up"]:
            return

        # Level-up announcement
        config = await self.service.get_guild_config(message.guild.id)
        channel = message.channel
        if config.get("announce_mode") == "channel" and config.get("leveling_channel_id"):
            ch = message.guild.get_channel(int(config["leveling_channel_id"]))
            if isinstance(ch, discord.TextChannel):
                channel = ch
        embed = discord.Embed(
            title="🎉 Level up!",
            description=f"{message.author.mention} reached **Level {result['new_level']}**!",
            color=0xFEE75C,
        )
        embed.set_thumbnail(url=message.author.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

        # Role rewards
        rewards = await self.service.roles_awarded_at(message.guild.id, result["new_level"])
        me = message.guild.me
        for role_id in rewards:
            role = message.guild.get_role(role_id)
            if role is None or me is None:
                continue
            if role >= me.top_role or not me.guild_permissions.manage_roles:
                continue
            try:
                await message.author.add_roles(role, reason="Level reward")
            except discord.HTTPException:
                log.warning("Failed to add reward role %s", role_id)

    # ---- /rank ---------------------------------------------------------
    @app_commands.command(name="rank", description="Show a user's level and rank.")
    @app_commands.describe(member="Member to inspect (defaults to you).")
    async def rank(self, interaction: discord.Interaction,
                   member: discord.Member | None = None):
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        member = member or interaction.user  # type: ignore[assignment]
        info = await self.service.get_rank(interaction.guild.id, member.id)
        bar = progress_bar(info["current_level_xp"], info["level_span"])
        embed = discord.Embed(title=f"🏅 {member.display_name}", color=0x5865F2)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Level", value=str(info["level"]))
        embed.add_field(name="Rank", value=f"#{info['rank']}")
        embed.add_field(name="Total XP", value=f"{info['xp']:,}")
        embed.add_field(
            name="Progress to next level",
            value=f"`{bar}`\n{info['current_level_xp']:,} / {info['level_span']:,} XP",
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    # ---- /leaderboard --------------------------------------------------
    @app_commands.command(name="leaderboard", description="Server XP leaderboard.")
    async def leaderboard(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        rows = await self.service.leaderboard(interaction.guild.id, limit=100)
        if not rows:
            return await interaction.response.send_message("No XP recorded yet.", ephemeral=True)

        pages: list[discord.Embed] = []
        per_page = 10
        for i in range(0, len(rows), per_page):
            chunk = rows[i : i + per_page]
            embed = discord.Embed(
                title=f"🏆 Leaderboard — {interaction.guild.name}",
                color=0xF1C40F,
            )
            lines = []
            for j, row in enumerate(chunk, start=i + 1):
                member = interaction.guild.get_member(row["user_id"])
                name = member.display_name if member else f"User {row['user_id']}"
                lines.append(
                    f"**#{j}** {name} — Lvl **{row['level']}** · {row['xp']:,} XP"
                )
            embed.description = "\n".join(lines)
            embed.timestamp = discord.utils.utcnow()
            pages.append(embed)
        view = Paginator(pages, interaction.user.id)
        await interaction.response.send_message(embed=pages[0], view=view)

    # =====================================================================
    # /leveling subgroup
    # =====================================================================
    @leveling.command(name="setup", description="Show the current leveling configuration.")
    @require_admin()
    async def setup_cmd(self, interaction: discord.Interaction):
        assert interaction.guild
        cfg = await self.service.get_guild_config(interaction.guild.id)
        ignored_ch = await self.service.list_ignored_channels(interaction.guild.id)
        ignored_rl = await self.service.list_ignored_roles(interaction.guild.id)
        rewards = await self.service.list_rewards(interaction.guild.id)

        embed = discord.Embed(title="⚙️ Leveling Configuration", color=0x5865F2)
        embed.add_field(name="Enabled", value="✅" if cfg["leveling_enabled"] else "❌")
        embed.add_field(name="XP range",
                        value=f"{cfg['xp_min']}–{cfg['xp_max']}")
        embed.add_field(name="Cooldown", value=f"{cfg['xp_cooldown']}s")
        embed.add_field(name="Announce channel",
                        value=f"<#{cfg['leveling_channel_id']}>"
                        if cfg["leveling_channel_id"] else "—")
        embed.add_field(name="Announce mode", value=cfg["announce_mode"])
        embed.add_field(name="Ignored channels",
                        value=", ".join(f"<#{c}>" for c in ignored_ch) or "—",
                        inline=False)
        embed.add_field(name="Ignored roles",
                        value=", ".join(f"<@&{r}>" for r in ignored_rl) or "—",
                        inline=False)
        reward_str = ", ".join(
            f"Lvl {r['level']}: <@&{r['role_id']}>" for r in rewards
        ) or "—"
        embed.add_field(name="Role rewards", value=reward_str, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @leveling.command(name="toggle", description="Enable or disable leveling.")
    @require_admin()
    async def toggle(self, interaction: discord.Interaction):
        assert interaction.guild
        cfg = await self.service.get_guild_config(interaction.guild.id)
        new_val = 0 if cfg["leveling_enabled"] else 1
        await self.service.update_guild_config(interaction.guild.id, leveling_enabled=new_val)
        await interaction.response.send_message(
            f"Leveling is now {'**enabled** ✅' if new_val else '**disabled** ❌'}.",
            ephemeral=True,
        )

    @leveling.command(name="set-xp", description="Configure XP range and cooldown.")
    @require_admin()
    @app_commands.describe(xp_min="Minimum XP per message", xp_max="Maximum XP per message",
                           cooldown="Cooldown in seconds")
    async def set_xp(self, interaction: discord.Interaction,
                     xp_min: int, xp_max: int, cooldown: int):
        assert interaction.guild
        validate_positive_int(xp_min, 1, 1000, "xp_min")
        validate_positive_int(xp_max, 1, 1000, "xp_max")
        validate_positive_int(cooldown, 0, 86400, "cooldown")
        if xp_min > xp_max:
            return await interaction.response.send_message(
                "`xp_min` must be ≤ `xp_max`.", ephemeral=True
            )
        await self.service.update_guild_config(
            interaction.guild.id, xp_min=xp_min, xp_max=xp_max, xp_cooldown=cooldown
        )
        await interaction.response.send_message(
            f"XP set to **{xp_min}–{xp_max}** per message, cooldown **{cooldown}s**.",
            ephemeral=True,
        )

    @leveling.command(name="set-channel",
                      description="Set announcement channel & mode.")
    @require_admin()
    @app_commands.describe(channel="Channel to announce in",
                           mode="'channel' to use channel above, 'current' to use message channel")
    async def set_channel(self, interaction: discord.Interaction,
                          channel: Optional[discord.TextChannel] = None,
                          mode: str = "channel"):
        assert interaction.guild
        if mode not in ("channel", "current"):
            return await interaction.response.send_message(
                "Mode must be `channel` or `current`.", ephemeral=True
            )
        await self.service.update_guild_config(
            interaction.guild.id,
            leveling_channel_id=channel.id if channel else None,
            announce_mode=mode,
        )
        await interaction.response.send_message("Announcement settings updated.",
                                                ephemeral=True)

    @leveling.command(name="reset", description="Reset a user's XP and level.")
    @require_admin()
    @app_commands.describe(member="Member to reset")
    async def reset(self, interaction: discord.Interaction, member: discord.Member):
        assert interaction.guild
        await self.service.reset_user(interaction.guild.id, member.id)
        await interaction.response.send_message(
            f"Reset **{member.display_name}**'s XP and level.", ephemeral=True
        )

    @leveling.command(name="ignore-channel", description="Toggle channel from XP rewards.")
    @require_admin()
    @app_commands.describe(channel="Channel to ignore/unignore")
    async def ignore_channel(self, interaction: discord.Interaction,
                             channel: discord.TextChannel):
        assert interaction.guild
        ignored = await self.service.list_ignored_channels(interaction.guild.id)
        if channel.id in ignored:
            await self.service.unignore_channel(interaction.guild.id, channel.id)
            await interaction.response.send_message(
                f"{channel.mention} is no longer ignored.", ephemeral=True
            )
        else:
            await self.service.ignore_channel(interaction.guild.id, channel.id)
            await interaction.response.send_message(
                f"{channel.mention} is now ignored.", ephemeral=True
            )

    @leveling.command(name="ignore-role", description="Toggle role from XP rewards.")
    @require_admin()
    @app_commands.describe(role="Role to ignore/unignore")
    async def ignore_role(self, interaction: discord.Interaction, role: discord.Role):
        assert interaction.guild
        ignored = await self.service.list_ignored_roles(interaction.guild.id)
        if role.id in ignored:
            await self.service.unignore_role(interaction.guild.id, role.id)
            await interaction.response.send_message(
                f"{role.mention} is no longer ignored.", ephemeral=True
            )
        else:
            await self.service.ignore_role(interaction.guild.id, role.id)
            await interaction.response.send_message(
                f"{role.mention} is now ignored.", ephemeral=True
            )

    @leveling.command(name="rewards", description="Manage role rewards at levels.")
    @require_admin()
    @app_commands.describe(action="add/remove/list", level="Target level",
                           role="Role to grant")
    async def rewards(self, interaction: discord.Interaction, action: str,
                      level: Optional[int] = None, role: Optional[discord.Role] = None):
        assert interaction.guild
        action = action.lower()
        if action == "list":
            rows = await self.service.list_rewards(interaction.guild.id)
            text = "\n".join(f"Lvl {r['level']} → <@&{r['role_id']}>" for r in rows) or "None."
            return await interaction.response.send_message(text, ephemeral=True)
        if action not in ("add", "remove") or level is None or role is None:
            return await interaction.response.send_message(
                "Usage: `/leveling rewards action:add|remove|list level:<n> role:<@role>`",
                ephemeral=True,
            )
        validate_positive_int(level, 1, 1000, "level")
        if action == "add":
            await self.service.add_reward(interaction.guild.id, level, role.id)
            await interaction.response.send_message(
                f"Added reward: Level {level} → {role.mention}", ephemeral=True
            )
        else:
            await self.service.remove_reward(interaction.guild.id, level, role.id)
            await interaction.response.send_message(
                f"Removed reward: Level {level} → {role.mention}", ephemeral=True
            )

    @leveling.command(name="set-level", description="Set a user's level.")
    @require_admin()
    @app_commands.describe(member="Member", level="New level")
    async def set_level(self, interaction: discord.Interaction,
                        member: discord.Member, level: int):
        assert interaction.guild
        validate_positive_int(level, 0, 1000, "level")
        await self.service.set_user_level(interaction.guild.id, member.id, level)
        await interaction.response.send_message(
            f"Set **{member.display_name}** to **Level {level}** "
            f"({total_xp_for_level(level):,} XP).", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Leveling(bot))
