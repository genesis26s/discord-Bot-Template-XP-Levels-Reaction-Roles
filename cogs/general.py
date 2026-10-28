"""General-purpose commands."""
from __future__ import annotations

import platform
import time

import discord
from discord import app_commands
from discord.ext import commands

import discord as dpy

from utils.paginator import Paginator


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._start = time.time()

    # ---- /ping ---------------------------------------------------------
    @app_commands.command(name="ping", description="Check latency and API status.")
    async def ping(self, interaction: discord.Interaction):
        ws = self.bot.latency * 1000
        t0 = time.perf_counter()
        await interaction.response.send_message("Pinging…")
        rtt = (time.perf_counter() - t0) * 1000
        embed = discord.Embed(title="🏓 Pong!", color=0x57F287)
        embed.add_field(name="WebSocket", value=f"`{ws:.0f} ms`")
        embed.add_field(name="Round trip", value=f"`{rtt:.0f} ms`")
        await interaction.edit_original_response(content=None, embed=embed)

    # ---- /help ---------------------------------------------------------
    @app_commands.command(name="help", description="Interactive command directory.")
    async def help(self, interaction: discord.Interaction):
        pages: list[discord.Embed] = []
        for cog_name, cog in self.bot.cogs.items():
            cmds = [c for c in cog.get_app_commands() if getattr(c, "name", None)]
            if not cmds:
                continue
            embed = discord.Embed(
                title=f"📖 {cog_name}",
                description=cog.__doc__ or "",
                color=0x5865F2,
            )
            for cmd in cmds:
                desc = (cmd.description or "")[:100]
                embed.add_field(name=f"/{cmd.qualified_name}", value=desc or "—", inline=False)
            pages.append(embed)

        if not pages:
            return await interaction.response.send_message("No commands registered.", ephemeral=True)
        view = Paginator(pages, interaction.user.id)
        await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)

    # ---- /botinfo ------------------------------------------------------
    @app_commands.command(name="botinfo", description="Bot statistics and information.")
    async def botinfo(self, interaction: discord.Interaction):
        assert self.bot.user is not None
        uptime = int(time.time() - self._start)
        embed = discord.Embed(title="🤖 Bot Information", color=0x5865F2)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="Name", value=self.bot.user.name)
        embed.add_field(name="ID", value=f"`{self.bot.user.id}`")
        embed.add_field(name="Uptime", value=f"<t:{int(time.time()) - uptime}:R>")
        embed.add_field(name="discord.py", value=dpy.__version__)
        embed.add_field(name="Python", value=platform.python_version())
        embed.add_field(name="Guilds", value=str(len(self.bot.guilds)))
        embed.add_field(name="Commands", value=str(len(self.bot.tree.get_commands())))
        await interaction.response.send_message(embed=embed)

    # ---- /serverinfo ---------------------------------------------------
    @app_commands.command(name="serverinfo", description="Server details.")
    async def serverinfo(self, interaction: discord.Interaction):
        g = interaction.guild
        if not g:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        embed = discord.Embed(title=g.name, color=0x5865F2)
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="Owner", value=f"<@{g.owner_id}>")
        embed.add_field(name="Members", value=str(g.member_count))
        embed.add_field(name="Channels", value=str(len(g.channels)))
        embed.add_field(name="Roles", value=str(len(g.roles)))
        embed.add_field(name="Created", value=f"<t:{int(g.created_at.timestamp())}:D>")
        embed.add_field(name="Boosts", value=str(g.premium_subscription_count or 0))
        await interaction.response.send_message(embed=embed)

    # ---- /userinfo -----------------------------------------------------
    @app_commands.command(name="userinfo", description="Member details.")
    @app_commands.describe(member="Member to inspect (defaults to you).")
    async def userinfo(self, interaction: discord.Interaction,
                       member: discord.Member | None = None):
        member = member or interaction.user  # type: ignore[assignment]
        assert isinstance(member, discord.Member)
        embed = discord.Embed(title=str(member), color=member.color or 0x5865F2)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=f"`{member.id}`")
        embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>"
                        if member.joined_at else "—")
        embed.add_field(name="Created", value=f"<t:{int(member.created_at.timestamp())}:R>")
        top = member.top_role.mention if member.top_role else "—"
        embed.add_field(name="Top role", value=top)
        embed.add_field(name="Bot?", value="Yes" if member.bot else "No")
        await interaction.response.send_message(embed=embed)

    # ---- /avatar -------------------------------------------------------
    @app_commands.command(name="avatar", description="Display a user's avatar.")
    @app_commands.describe(member="Member whose avatar to show.")
    async def avatar(self, interaction: discord.Interaction,
                     member: discord.Member | None = None):
        member = member or interaction.user  # type: ignore[assignment]
        assert isinstance(member, discord.Member)
        embed = discord.Embed(title=f"{member.display_name}'s avatar", color=0x5865F2)
        embed.set_image(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ---- /invite -------------------------------------------------------
    @app_commands.command(name="invite", description="Get the bot's invite link.")
    async def invite(self, interaction: discord.Interaction):
        perms = discord.Permissions(
            manage_roles=True, manage_channels=True,
            send_messages=True, embed_links=True,
            add_reactions=True, read_message_history=True,
            use_external_emojis=True,
        )
        url = discord.utils.oauth_url(self.bot.user.id, permissions=perms)
        await interaction.response.send_message(
            f"**Invite me:** <{url}>", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
