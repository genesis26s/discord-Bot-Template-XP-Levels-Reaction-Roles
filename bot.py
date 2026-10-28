"""Main bot class — loads cogs, syncs slash commands."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import config
from database import Database
from utils.errors import UserError
from utils.logging_config import setup_logging

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
        intents.members = True           # required for role rewards / member lookups
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.db = Database(config.DATABASE_PATH)

    async def setup_hook(self) -> None:
        await self.db.connect()

        # Wire the REAL tree error handler (on_app_command_error is a myth).
        self.tree.on_error = self._on_tree_error

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

    async def _on_tree_error(self, interaction: discord.Interaction,
                              error: app_commands.AppCommandError) -> None:
        # Unwrap the wrapper discord.py puts around command exceptions.
        if isinstance(error, app_commands.CommandInvokeError):
            error = error.original

        if isinstance(error, UserError):
            msg = f"⚠️ {error}"
        elif isinstance(error, app_commands.MissingPermissions):
            msg = "🚫 You don't have permission to use this command."
        elif isinstance(error, app_commands.CheckFailure):
            msg = f"🚫 {error}"
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ Slow down — try again in {error.retry_after:.1f}s."
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

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s) in %d guilds",
                 self.user, self.user.id if self.user else "?", len(self.guilds))
        if not self.intents.message_content:
            log.warning("MESSAGE_CONTENT intent disabled — XP will not accumulate.")


async def run() -> None:
    config.validate()
    setup_logging(config.LOG_LEVEL)
    bot = TemplateBot()
    try:
        await bot.start(config.DISCORD_TOKEN)
    finally:
        try:
            await bot.close()
        except Exception:
            pass
        await bot.db.close()
