"""Main bot class — loads cogs, syncs slash commands."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from config import config
from database import Database
from utils.logging_config import setup_logging
from utils.errors import UserError

log = logging.getLogger(__name__)

COGS = (
    "cogs.general",
    "cogs.leveling",
    "cogs.embeds",
    "cogs.reaction_roles",
    "cogs.setup_panel",
    "cogs.administration",
)


class TemplateBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True   # required for XP on messages
        intents.members = True           # required for role rewards
        intents.reactions = True         # required for reaction roles
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.db = Database(config.DATABASE_PATH)

    async def setup_hook(self) -> None:
        await self.db.connect()
        for ext in COGS:
            try:
                await self.load_extension(ext)
                log.info("Loaded extension %s", ext)
            except Exception:
                log.exception("Failed loading extension %s", ext)

        try:
            if config.DEBUG_GUILD_ID:
                guild = discord.Object(id=config.DEBUG_GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                log.info("Synced commands to debug guild %s", config.DEBUG_GUILD_ID)
            else:
                await self.tree.sync()
                log.info("Synced global application commands.")
        except Exception:
            log.exception("Failed to sync commands")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s) in %d guilds",
                 self.user, self.user.id if self.user else "?", len(self.guilds))

    async def on_app_command_error(self, interaction: discord.Interaction,
                                    error: Exception) -> None:
        if isinstance(error, UserError):
            msg = f"⚠️ {error}"
        elif isinstance(error, discord.app_commands.CheckFailure):
            msg = f"🚫 {error}"
        else:
            log.exception("Unhandled app command error", exc_info=error)
            msg = "❌ Something went wrong. Please try again."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


async def run() -> None:
    config.validate()
    setup_logging(config.LOG_LEVEL)
    bot = TemplateBot()
    try:
        await bot.start(config.DISCORD_TOKEN)
    finally:
        await bot.db.close()
