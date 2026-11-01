"""Interactive admin panel via /setup — with actionable controls."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.embed_service import EmbedService
from services.leveling_service import LevelingService
from services.reaction_role_service import ReactionRoleService
from utils.permissions import require_admin, require_interaction_admin


def _main_embed() -> discord.Embed:
    return discord.Embed(
        title="🛠️ Administration Panel",
        description=(
            "Pick a category below.\n\n"
            "**Leveling** can be configured directly here. Advanced options "
            "are available via their slash-command groups."
        ),
        color=0x5865F2,
    ).set_footer(text="Every action re-checks your permissions.")


# =====================================================================
# Modals
# =====================================================================
class SetXpModal(discord.ui.Modal, title="Leveling — XP range"):
    xp_min = discord.ui.TextInput(label="Minimum XP per message", default="15")
    xp_max = discord.ui.TextInput(label="Maximum XP per message", default="25")
    cooldown = discord.ui.TextInput(label="Cooldown (seconds)", default="60")

    def __init__(self, service: LevelingService, guild_id: int,
                 parent_view: "LevelingView | None"):
        super().__init__()
        self.service = service
        self.guild_id = guild_id
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            xp_min = int(self.xp_min.value)
            xp_max = int(self.xp_max.value)
            cooldown = int(self.cooldown.value)
        except ValueError:
            return await interaction.response.send_message(
                "All fields must be integers.", ephemeral=True)
        if not (1 <= xp_min <= xp_max <= 1000):
            return await interaction.response.send_message(
                "Require 1 ≤ min ≤ max ≤ 1000.", ephemeral=True)
        if not (0 <= cooldown <= 86400):
            return await interaction.response.send_message(
                "Cooldown must be 0–86400 seconds.", ephemeral=True)
        await self.service.update_guild_config(
            self.guild_id, xp_min=xp_min, xp_max=xp_max, xp_cooldown=cooldown)
        if self.parent_view and self.parent_view.message:
            try:
                cfg = await self.service.get_guild_config(self.guild_id)
                await self.parent_view.message.edit(
                    embed=self.parent_view._embed(cfg), view=self.parent_view)
            except discord.HTTPException:
                pass
        await interaction.response.send_message(
            f"XP range set to **{xp_min}–{xp_max}** per message, "
            f"cooldown **{cooldown}s**.", ephemeral=True)


class AddRewardModal(discord.ui.Modal, title="Leveling — Add role reward"):
    level = discord.ui.TextInput(label="Level", default="5")
    role_id = discord.ui.TextInput(label="Role ID (right-click → Copy ID)")

    def __init__(self, service: LevelingService, guild_id: int,
                 parent_view: "LevelingView | None"):
        super().__init__()
        self.service = service
        self.guild_id = guild_id
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            level = int(self.level.value)
            role_id = int(self.role_id.value)
        except ValueError:
            return await interaction.response.send_message(
                "Both fields must be integers.", ephemeral=True)
        if not (1 <= level <= 1000):
            return await interaction.response.send_message(
                "Level must be 1–1000.", ephemeral=True)
        assert interaction.guild
        role = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message(
                "No role with that ID in this server.", ephemeral=True)
        me = interaction.guild.me
        if me is None or role >= me.top_role:
            return await interaction.response.send_message(
                "That role is above my highest role — I couldn't assign it.",
                ephemeral=True)
        await self.service.add_reward(self.guild_id, level, role_id)
        if self.parent_view and self.parent_view.message:
            try:
                cfg = await self.service.get_guild_config(self.guild_id)
                await self.parent_view.message.edit(
                    embed=self.parent_view._embed(cfg), view=self.parent_view)
            except discord.HTTPException:
                pass
        await interaction.response.send_message(
            f"Reward added: Level {level} → {role.mention}", ephemeral=True)


# =====================================================================
# Leveling category view
# =====================================================================
class LevelingView(discord.ui.View):
    def __init__(self, bot, author_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.author_id = author_id
        self.service = LevelingService(bot.db)
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Not your panel.", ephemeral=True)
            return False
        try:
            require_interaction_admin(interaction)
        except Exception as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return False
        return True

    def _embed(self, cfg: dict) -> discord.Embed:
        e = discord.Embed(title="📈 Leveling", color=0x5865F2)
        e.add_field(name="Enabled",
                    value="✅" if cfg["leveling_enabled"] else "❌")
        e.add_field(name="XP range", value=f"{cfg['xp_min']}–{cfg['xp_max']}")
        e.add_field(name="Cooldown", value=f"{cfg['xp_cooldown']}s")
        e.add_field(name="Channel",
                    value=f"<#{cfg['leveling_channel_id']}>"
                    if cfg["leveling_channel_id"] else "—")
        e.add_field(name="Mode", value=cfg["announce_mode"])
        return e

    async def refresh(self, interaction: discord.Interaction):
        cfg = await self.service.get_guild_config(interaction.guild_id)
        try:
            await interaction.response.edit_message(embed=self._embed(cfg),
                                                    view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Toggle ON/OFF",
                       style=discord.ButtonStyle.primary)
    async def toggle(self, interaction: discord.Interaction, _):
        cfg = await self.service.get_guild_config(interaction.guild_id)
        new = 0 if cfg["leveling_enabled"] else 1
        await self.service.update_guild_config(
            interaction.guild_id, leveling_enabled=new)
        await self.refresh(interaction)

    @discord.ui.button(label="Set XP range",
                       style=discord.ButtonStyle.secondary)
    async def set_xp(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(
            SetXpModal(self.service, interaction.guild_id, self))

    @discord.ui.button(label="Add reward",
                       style=discord.ButtonStyle.secondary)
    async def add_reward(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(
            AddRewardModal(self.service, interaction.guild_id, self))

    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.danger)
    async def back(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(
            embed=_main_embed(),
            view=SetupView(self.bot, self.author_id),
        )


# =====================================================================
# Main menu
# =====================================================================
class CategorySelect(discord.ui.Select):
    def __init__(self, parent: "SetupView"):
        self.parent_view = parent
        super().__init__(
            placeholder="Choose a category…",
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(label="Leveling System",
                                     value="leveling", emoji="📈"),
                discord.SelectOption(label="Embed Builder",
                                     value="embeds", emoji="🎨"),
                discord.SelectOption(label="Reaction Roles",
                                     value="rr", emoji="🎭"),
                discord.SelectOption(label="Permissions",
                                     value="perms", emoji="🔒"),
                discord.SelectOption(label="Channels",
                                     value="channels", emoji="📺"),
                discord.SelectOption(label="System Information",
                                     value="info", emoji="ℹ️"),
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            require_interaction_admin(interaction)
        except Exception as e:
            return await interaction.response.send_message(str(e), ephemeral=True)
        await self.parent_view.show_category(interaction, self.values[0])


class SetupView(discord.ui.View):
    def __init__(self, bot, author_id: int):
        super().__init__(timeout=600)
        self.bot = bot
        self.author_id = author_id
        self.add_item(CategorySelect(self))
        self.leveling = LevelingService(bot.db)
        self.embeds = EmbedService(bot.db)
        self.rr = ReactionRoleService(bot.db)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Not your panel.", ephemeral=True)
            return False
        return True

    async def show_category(self, interaction: discord.Interaction, cat: str):
        assert interaction.guild
        if cat == "leveling":
            view = LevelingView(self.bot, self.author_id)
            view.message = interaction.message
            cfg = await self.leveling.get_guild_config(interaction.guild.id)
            return await interaction.response.edit_message(
                embed=view._embed(cfg), view=view)
        if cat == "embeds":
            rows = await self.embeds.list(interaction.guild.id)
            e = discord.Embed(
                title="🎨 Embeds",
                description=(
                    f"{len(rows)} saved embed(s).\n\n"
                    "Use **`/embed create`** to design one, "
                    "**`/embed edit`** to modify, "
                    "**`/embed send`** to publish."
                ),
                color=0x5865F2,
            )
            return await interaction.response.edit_message(
                embed=e, view=SetupView(self.bot, self.author_id))
        if cat == "rr":
            panels = await self.rr.list_panels(interaction.guild.id)
            e = discord.Embed(
                title="🎭 Reaction Roles",
                description=(
                    f"{len(panels)} panel(s).\n\n"
                    "Use **`/reactionroles create`** to start a panel, "
                    "**`/reactionroles add`** to bind an emoji→role, "
                    "**`/reactionroles refresh`** to re-post."
                ),
                color=0x5865F2,
            )
            return await interaction.response.edit_message(
                embed=e, view=SetupView(self.bot, self.author_id))
        if cat == "perms":
            me = interaction.guild.me
            if not me:
                return await interaction.response.send_message(
                    "Bot member missing.", ephemeral=True)
            p = me.guild_permissions
            e = discord.Embed(title="🔒 Bot Permissions", color=0x5865F2)
            for k, v in {
                "Manage Roles": p.manage_roles,
                "Manage Channels": p.manage_channels,
                "Send Messages": p.send_messages,
                "Embed Links": p.embed_links,
                "Add Reactions": p.add_reactions,
                "Read History": p.read_message_history,
                "External Emojis": p.use_external_emojis,
            }.items():
                e.add_field(name=k, value="✅" if v else "❌")
            return await interaction.response.edit_message(
                embed=e, view=SetupView(self.bot, self.author_id))
        if cat == "channels":
            cfg = await self.leveling.get_guild_config(interaction.guild.id)
            e = discord.Embed(title="📺 Configured Channels", color=0x5865F2)
            e.add_field(
                name="Leveling announcements",
                value=f"<#{cfg['leveling_channel_id']}>"
                if cfg["leveling_channel_id"] else "—")
            ignored = await self.leveling.list_ignored_channels(
                interaction.guild.id)
            e.add_field(name="Ignored channels",
                        value=", ".join(f"<#{c}>" for c in ignored) or "—",
                        inline=False)
            return await interaction.response.edit_message(
                embed=e, view=SetupView(self.bot, self.author_id))
        if cat == "info":
            assert self.bot.user
            e = discord.Embed(title="ℹ️ System Information", color=0x5865F2)
            e.add_field(name="Bot", value=str(self.bot.user))
            e.add_field(name="Guilds", value=str(len(self.bot.guilds)))
            e.add_field(name="Latency", value=f"{self.bot.latency*1000:.0f} ms")
            return await interaction.response.edit_message(
                embed=e, view=SetupView(self.bot, self.author_id))


class SetupPanel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="setup",
                          description="Open the interactive admin panel.")
    @require_admin()
    async def setup_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=_main_embed(),
            view=SetupView(self.bot, interaction.user.id),
            ephemeral=True,
        )

    @app_commands.command(name="panel",
                          description="Alias for /setup — admin panel.")
    @require_admin()
    async def panel_alias(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=_main_embed(),
            view=SetupView(self.bot, interaction.user.id),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(SetupPanel(bot))
