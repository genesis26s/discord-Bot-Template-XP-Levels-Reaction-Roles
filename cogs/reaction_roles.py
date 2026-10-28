"""Reaction-role panels with reactions, buttons and dropdowns."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from services.reaction_role_service import (
    RESTRICTED_PERMS, VALID_MODES, ReactionRoleService,
)
from utils.errors import ValidationError
from utils.permissions import bot_can_manage, require_admin
from utils.validators import validate_emoji

log = logging.getLogger(__name__)


def _check_role_safe(role: discord.Role, me: discord.Member) -> str | None:
    if role.is_default() or role.managed:
        return "Cannot use @everyone or an integration-managed role."
    if any(getattr(role.permissions, p) for p in RESTRICTED_PERMS):
        return "Role has elevated permissions and cannot be self-assigned."
    if not bot_can_manage(role, me):
        return "Bot cannot assign this role (hierarchy/permissions)."
    return None


class ReactionRoles(commands.Cog):
    """Reaction role systems."""

    rr = app_commands.Group(name="reactionroles",
                             description="Reaction role management.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.service = ReactionRoleService(bot.db)

    # ---- register persistent views on startup ------------------------
    async def cog_load(self) -> None:
        try:
            panels = await self.service.list_all_panels()
        except Exception:
            log.exception("Failed to load reaction-role panels for view registration")
            return
        for panel in panels:
            try:
                await self._register_panel_view(panel)
            except Exception:
                log.exception("Failed to register view for panel %s", panel.get("id"))

    async def _register_panel_view(self, panel: dict) -> None:
        mappings = await self.service.mappings(panel["id"])
        view = await self.service.build_view(panel, mappings, self.bot)
        if view:
            self.bot.add_view(view)

    # ---- panel posting / refreshing -----------------------------------
    async def _post_panel(self, guild: discord.Guild, panel: dict):
        channel = guild.get_channel(int(panel["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return None
        mappings = await self.service.mappings(panel["id"])
        embed = await self.service.build_panel_message(panel, mappings)
        view = await self.service.build_view(panel, mappings, self.bot)
        try:
            msg = await channel.send(embed=embed, view=view)
        except discord.HTTPException:
            log.exception("Failed to post reaction-role panel")
            return None
        if view:
            self.bot.add_view(view)
        if panel["mode"] == "react":
            for m in mappings:
                try:
                    await msg.add_reaction(m["emoji"])
                except discord.HTTPException:
                    log.warning("Failed to add reaction %s", m["emoji"])
        await self.service.set_message_id(panel["id"], msg.id)
        return msg

    async def _rebuild(self, guild: discord.Guild, panel: dict):
        channel = guild.get_channel(int(panel["channel_id"]))
        if isinstance(channel, discord.TextChannel) and panel.get("message_id"):
            try:
                msg = await channel.fetch_message(int(panel["message_id"]))
            except discord.NotFound:
                return await self._post_panel(guild, panel)
            except discord.HTTPException:
                return None
            mappings = await self.service.mappings(panel["id"])
            embed = await self.service.build_panel_message(panel, mappings)
            view = await self.service.build_view(panel, mappings, self.bot)
            try:
                await msg.edit(embed=embed, view=view)
            except discord.HTTPException:
                return None
            if view:
                self.bot.add_view(view)
            if panel["mode"] == "react":
                try:
                    await msg.clear_reactions()
                except discord.HTTPException:
                    pass
                for m in mappings:
                    try:
                        await msg.add_reaction(m["emoji"])
                    except discord.HTTPException:
                        pass
            return msg
        return await self._post_panel(guild, panel)

    # ---- commands ------------------------------------------------------
    @rr.command(name="create", description="Create a reaction-role panel.")
    @require_admin()
    @app_commands.describe(name="Panel name", channel="Channel to post in",
                           mode="react/buttons/dropdown",
                           description="Optional description",
                           max_selections="Dropdown maximum (0 = unlimited)")
    @app_commands.choices(mode=[app_commands.Choice(name=m, value=m)
                                 for m in ("react", "buttons", "dropdown")])
    async def create(self, interaction: discord.Interaction, name: str,
                     channel: discord.TextChannel,
                     mode: str = "react",
                     description: str | None = None,
                     max_selections: int = 0):
        assert interaction.guild
        try:
            panel_id = await self.service.create_panel(
                interaction.guild.id, name, channel.id, mode, description,
                max(0, max_selections))
        except ValidationError as e:
            return await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        panel = await self.service.get_panel(panel_id)
        await interaction.response.defer(ephemeral=True)
        msg = await self._post_panel(interaction.guild, panel)
        if not msg:
            return await interaction.followup.send(
                "❌ Panel created but could not be posted "
                "(check channel permissions).", ephemeral=True)
        await interaction.followup.send(
            f"✅ Panel **{name}** created in {channel.mention} (mode: `{mode}`).",
            ephemeral=True)

    @rr.command(name="add", description="Add an emoji→role mapping.")
    @require_admin()
    async def add(self, interaction: discord.Interaction, panel: str,
                  emoji: str, role: discord.Role,
                  description: str | None = None):
        assert interaction.guild
        p = await self.service.get_panel_by_name(interaction.guild.id, panel)
        if not p:
            return await interaction.response.send_message(
                "Panel not found.", ephemeral=True)
        try:
            emoji = validate_emoji(emoji)
        except ValidationError as e:
            return await interaction.response.send_message(
                f"❌ {e}", ephemeral=True)
        err = _check_role_safe(role, interaction.guild.me)
        if err:
            return await interaction.response.send_message(
                f"❌ {err}", ephemeral=True)
        try:
            await self.service.add_mapping(p["id"], emoji, role.id, description)
        except ValidationError as e:
            return await interaction.response.send_message(
                f"❌ {e}", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await self._rebuild(interaction.guild, p)
        await interaction.followup.send(
            f"✅ Added {emoji} → {role.mention}.", ephemeral=True)

    @rr.command(name="remove", description="Remove a mapping.")
    @require_admin()
    async def remove(self, interaction: discord.Interaction, panel: str,
                     role: discord.Role):
        assert interaction.guild
        p = await self.service.get_panel_by_name(interaction.guild.id, panel)
        if not p:
            return await interaction.response.send_message(
                "Panel not found.", ephemeral=True)
        await self.service.remove_mapping(p["id"], role_id=role.id)
        await interaction.response.defer(ephemeral=True)
        await self._rebuild(interaction.guild, p)
        await interaction.followup.send(
            f"Removed {role.mention}.", ephemeral=True)

    @rr.command(name="list", description="List configured panels.")
    @require_admin()
    async def list_cmd(self, interaction: discord.Interaction):
        assert interaction.guild
        panels = await self.service.list_panels(interaction.guild.id)
        if not panels:
            return await interaction.response.send_message(
                "No panels configured.", ephemeral=True)
        embed = discord.Embed(title="🎭 Reaction-role panels", color=0x5865F2)
        for p in panels:
            mappings = await self.service.mappings(p["id"])
            mapping_lines = "\n".join(
                f"  {m['emoji']} → <@&{m['role_id']}>" for m in mappings
            ) or "  *(no mappings)*"
            embed.add_field(
                name=f"{p['name']}  ({p['mode']})",
                value=f"<#{p['channel_id']}>\n{mapping_lines}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @rr.command(name="delete", description="Delete a reaction-role panel.")
    @require_admin()
    async def delete(self, interaction: discord.Interaction, panel: str):
        assert interaction.guild
        p = await self.service.get_panel_by_name(interaction.guild.id, panel)
        if not p:
            return await interaction.response.send_message(
                "Panel not found.", ephemeral=True)
        if p.get("message_id"):
            ch = interaction.guild.get_channel(int(p["channel_id"]))
            if isinstance(ch, discord.TextChannel):
                try:
                    msg = await ch.fetch_message(int(p["message_id"]))
                    await msg.delete()
                except discord.HTTPException:
                    pass
        await self.service.delete_panel(p["id"])
        await interaction.response.send_message(
            f"Deleted **{panel}**.", ephemeral=True)

    @rr.command(name="refresh", description="Refresh/rebuild a panel message.")
    @require_admin()
    async def refresh(self, interaction: discord.Interaction, panel: str):
        assert interaction.guild
        p = await self.service.get_panel_by_name(interaction.guild.id, panel)
        if not p:
            return await interaction.response.send_message(
                "Panel not found.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        msg = await self._rebuild(interaction.guild, p)
        if msg:
            await interaction.followup.send("✅ Panel refreshed.", ephemeral=True)
        else:
            await interaction.followup.send(
                "❌ Failed to refresh panel.", ephemeral=True)

    @rr.command(name="setup", description="Show reaction-role configuration summary.")
    @require_admin()
    async def setup_cmd(self, interaction: discord.Interaction):
        assert interaction.guild
        panels = await self.service.list_panels(interaction.guild.id)
        embed = discord.Embed(
            title="🎭 Reaction-role setup",
            description=f"{len(panels)} panel(s) configured.",
            color=0x5865F2,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---- runtime: traditional reactions ------------------------------
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload, add=False)

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent,
                                add: bool):
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        panels = await self.service.list_panels(payload.guild_id)
        panel = next(
            (p for p in panels if p.get("message_id") == payload.message_id),
            None)
        if not panel or panel["mode"] != "react":
            return

        emoji_str = str(payload.emoji)
        mapping = await self.service.mapping_by_emoji(panel["id"], emoji_str)
        if not mapping:
            return
        role = guild.get_role(int(mapping["role_id"]))
        if not role or not guild.me or not bot_can_manage(role, guild.me):
            return
        try:
            if add:
                await member.add_roles(role, reason="Reaction role")
            else:
                await member.remove_roles(role, reason="Reaction role removed")
        except discord.HTTPException:
            log.warning("Failed reaction role toggle for %s", member.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRoles(bot))
