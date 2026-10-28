"""Interactive admin panel via /setup."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from services.embed_service import EmbedService
from services.leveling_service import LevelingService
from services.reaction_role_service import ReactionRoleService
from utils.permissions import require_admin, require_interaction_admin


def _main_embed() -> discord.Embed:
    e = discord.Embed(
        title="🛠️ Administration Panel",
        description="Select a category below to configure it.",
        color=0x5865F2,
    )
    e.set_footer(text="All actions are checked against your permissions.")
    return e


class CategorySelect(discord.ui.Select):
    def __init__(self, parent: "SetupView"):
        self.parent_view = parent
        options = [
            discord.SelectOption(label="Leveling System", value="leveling",
                                 emoji="📈"),
            discord.SelectOption(label="Embed Builder", value="embeds", emoji="🎨"),
            discord.SelectOption(label="Reaction Roles", value="rr", emoji="🎭"),
            discord.SelectOption(label="Permissions", value="perms", emoji="🔒"),
            discord.SelectOption(label="Channels", value="channels", emoji="📺"),
            discord.SelectOption(label="System Information", value="info", emoji="ℹ️"),
        ]
        super().__init__(placeholder="Choose a category…", options=options,
                         min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        try:
            require_interaction_admin(interaction)
        except Exception as e:
            return await interaction.response.send_message(str(e), ephemeral=True)
        await self.parent_view.show_category(interaction, self.values[0])


class SetupView(discord.ui.View):
    def __init__(self, bot: commands.Bot, author_id: int):
        super().__init__(timeout=600)
        self.bot = bot
        self.author_id = author_id
        self.add_item(CategorySelect(self))
        self.leveling = LevelingService(bot.db)
        self.embeds = EmbedService(bot.db)
        self.rr = ReactionRoleService(bot.db)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True

    async def show_category(self, interaction: discord.Interaction, cat: str):
        assert interaction.guild
        if cat == "leveling":
            cfg = await self.leveling.get_guild_config(interaction.guild.id)
            embed = discord.Embed(title="📈 Leveling", color=0x5865F2,
                                  description="Use `/leveling …` to edit.")
            embed.add_field(name="Enabled",
                            value="✅" if cfg["leveling_enabled"] else "❌")
            embed.add_field(name="XP range", value=f"{cfg['xp_min']}–{cfg['xp_max']}")
            embed.add_field(name="Cooldown", value=f"{cfg['xp_cooldown']}s")
            embed.add_field(name="Channel",
                            value=f"<#{cfg['leveling_channel_id']}>"
                            if cfg["leveling_channel_id"] else "—")
            await interaction.response.edit_message(embed=embed, view=self)
        elif cat == "embeds":
            rows = await self.embeds.list(interaction.guild.id)
            embed = discord.Embed(title="🎨 Embeds", color=0x5865F2,
                                  description=f"{len(rows)} saved embed(s).\n"
                                              "Use `/embed …` to create/edit.")
            await interaction.response.edit_message(embed=embed, view=self)
        elif cat == "rr":
            panels = await self.rr.list_panels(interaction.guild.id)
            embed = discord.Embed(title="🎭 Reaction Roles", color=0x5865F2,
                                  description=f"{len(panels)} panel(s).\n"
                                              "Use `/reactionroles …` to manage.")
            await interaction.response.edit_message(embed=embed, view=self)
        elif cat == "perms":
            me = interaction.guild.me
            if not me:
                return await interaction.response.send_message("Bot member missing.",
                                                               ephemeral=True)
            p = me.guild_permissions
            embed = discord.Embed(title="🔒 Bot Permissions", color=0x5865F2)
            checks = {
                "Manage Roles": p.manage_roles,
                "Manage Channels": p.manage_channels,
                "Send Messages": p.send_messages,
                "Embed Links": p.embed_links,
                "Add Reactions": p.add_reactions,
                "Read Message History": p.read_message_history,
                "Use External Emojis": p.use_external_emojis,
            }
            for k, v in checks.items():
                embed.add_field(name=k, value="✅" if v else "❌")
            await interaction.response.edit_message(embed=embed, view=self)
        elif cat == "channels":
            embed = discord.Embed(title="📺 Configured Channels", color=0x5865F2)
            cfg = await self.leveling.get_guild_config(interaction.guild.id)
            embed.add_field(name="Leveling announcements",
                            value=f"<#{cfg['leveling_channel_id']}>"
                            if cfg["leveling_channel_id"] else "—")
            ignored = await self.leveling.list_ignored_channels(interaction.guild.id)
            embed.add_field(name="Ignored channels",
                            value=", ".join(f"<#{c}>" for c in ignored) or "—",
                            inline=False)
            await interaction.response.edit_message(embed=embed, view=self)
        elif cat == "info":
            assert self.bot.user
            embed = discord.Embed(title="ℹ️ System Information", color=0x5865F2)
            embed.add_field(name="Bot", value=str(self.bot.user))
            embed.add_field(name="Guilds", value=str(len(self.bot.guilds)))
            embed.add_field(name="Latency", value=f"{self.bot.latency*1000:.0f} ms")
            await interaction.response.edit_message(embed=embed, view=self)


class SetupPanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup",
                          description="Open the interactive admin panel.")
    @require_admin()
    async def setup_cmd(self, interaction: discord.Interaction):
        view = SetupView(self.bot, interaction.user.id)
        await interaction.response.send_message(
            embed=_main_embed(), view=view, ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(SetupPanel(bot))
