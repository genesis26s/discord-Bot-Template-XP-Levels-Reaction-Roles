"""Shared error types and helpers."""

class UserError(Exception):
    """Error caused by user input — safe to show back to the user."""


class PermissionError_(UserError):
    """Insufficient permissions for the action."""


class ValidationError(UserError):
    """Invalid user input."""


async def safe_reply(interaction, content: str, ephemeral: bool = True) -> None:
    """Reply to an interaction safely, whether it has been responded to already."""
    import discord
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)
    except discord.HTTPException:
        pass
