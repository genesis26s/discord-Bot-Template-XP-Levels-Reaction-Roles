"""Permission checks used by every command."""
from __future__ import annotations

import discord
from discord import app_commands

from .errors import PermissionError_


ADMIN_PERMS = ("administrator", "manage_guild")


def user_is_admin(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False
    perms = member.guild_permissions
    return any(getattr(perms, p) for p in ADMIN_PERMS)


def bot_can_manage(role: discord.Role, me: discord.Member) -> bool:
    """Check the bot can actually assign *role* to a member."""
    if me.top_role <= role:
        return False
    if not me.guild_permissions.manage_roles:
        return False
    return True


def require_admin():
    """Slash command decorator requiring admin/manager privileges."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure("This command can only be used in a server.")
        if not user_is_admin(interaction.user):
            raise app_commands.CheckFailure("You need Administrator or Manage Server.")
        return True
    return app_commands.check(predicate)


def require_interaction_admin(interaction: discord.Interaction) -> None:
    """Raise if the acting user lacks privileges (for buttons/selects)."""
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        raise PermissionError_("This interaction is server-only.")
    if not user_is_admin(interaction.user):
        raise PermissionError_("You need Administrator or Manage Server.")
