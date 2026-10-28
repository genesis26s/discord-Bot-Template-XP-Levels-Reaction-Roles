"""Reaction-role persistence and panel building."""
from __future__ import annotations

from typing import Any, Optional

import discord

from database import Database
from utils.errors import ValidationError
from utils.permissions import bot_can_manage


VALID_MODES = {"react", "buttons", "dropdown"}
RESTRICTED_PERMS = {"administrator", "manage_guild", "manage_roles", "manage_channels",
                    "manage_messages", "kick_members", "ban_members", "moderate_members"}


def _role_is_restricted(role: discord.Role) -> bool:
    perms = role.permissions
    return any(getattr(perms, p) for p in RESTRICTED_PERMS)


class ReactionRoleService:
    def __init__(self, db: Database):
        self.db = db

    async def create_panel(self, guild_id: int, name: str, channel_id: int,
                           mode: str, description: str | None,
                           max_selections: int = 0) -> int:
        if mode not in VALID_MODES:
            raise ValidationError(f"mode must be one of {sorted(VALID_MODES)}")
        existing = await self.get_panel_by_name(guild_id, name)
        if existing:
            raise ValidationError(f"A panel named '{name}' already exists.")
        return await self.db.execute(
            "INSERT INTO reaction_role_panels "
            "(guild_id, channel_id, name, mode, max_selections, description) "
            "VALUES (?,?,?,?,?,?)",
            (guild_id, channel_id, name, mode, max_selections, description),
        )

    async def set_message_id(self, panel_id: int, message_id: int) -> None:
        await self.db.execute(
            "UPDATE reaction_role_panels SET message_id=? WHERE id=?",
            (message_id, panel_id),
        )

    async def get_panel(self, panel_id: int) -> Optional[dict]:
        return await self.db.fetchone(
            "SELECT * FROM reaction_role_panels WHERE id=?", (panel_id,)
        )

    async def get_panel_by_name(self, guild_id: int, name: str) -> Optional[dict]:
        return await self.db.fetchone(
            "SELECT * FROM reaction_role_panels WHERE guild_id=? AND name=?",
            (guild_id, name),
        )

    async def list_panels(self, guild_id: int) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM reaction_role_panels WHERE guild_id=? ORDER BY name ASC",
            (guild_id,),
        )

    async def delete_panel(self, panel_id: int) -> None:
        await self.db.execute(
            "DELETE FROM reaction_role_panels WHERE id=?", (panel_id,)
        )

    async def add_mapping(self, panel_id: int, emoji: str, role_id: int,
                          description: str | None) -> int:
        if not emoji or not role_id:
            raise ValidationError("emoji and role are required.")
        return await self.db.execute(
            "INSERT INTO reaction_role_mappings (panel_id, emoji, role_id, description) "
            "VALUES (?,?,?,?)",
            (panel_id, emoji, role_id, description),
        )

    async def remove_mapping(self, panel_id: int, role_id: int | None = None,
                             emoji: str | None = None) -> int:
        if role_id is not None:
            await self.db.execute(
                "DELETE FROM reaction_role_mappings WHERE panel_id=? AND role_id=?",
                (panel_id, role_id),
            )
            return 1
        if emoji is not None:
            await self.db.execute(
                "DELETE FROM reaction_role_mappings WHERE panel_id=? AND emoji=?",
                (panel_id, emoji),
            )
            return 1
        return 0

    async def mappings(self, panel_id: int) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM reaction_role_mappings WHERE panel_id=? ORDER BY id ASC",
            (panel_id,),
        )

    async def mapping_by_emoji(self, panel_id: int, emoji: str) -> Optional[dict]:
        return await self.db.fetchone(
            "SELECT * FROM reaction_role_mappings WHERE panel_id=? AND emoji=?",
            (panel_id, emoji),
        )

    # ---- panel rendering ----------------------------------------------
    async def build_panel_message(self, panel: dict, mappings: list[dict]) -> discord.Embed:
        embed = discord.Embed(
            title=f"🎭 {panel['name']}",
            description=panel.get("description") or "React to receive a role.",
            color=0x5865F2,
        )
        for m in mappings:
            role_text = f"<@&{m['role_id']}>"
            label = f"{m['emoji']}  →  {role_text}"
            if m.get("description"):
                label += f"\n*{m['description']}*"
            embed.add_field(name="\u200b", value=label, inline=False)
        if panel["mode"] == "dropdown" and int(panel.get("max_selections") or 0) > 0:
            embed.set_footer(text=f"Select up to {panel['max_selections']} role(s).")
        return embed

    async def build_view(self, panel: dict, mappings: list[dict],
                         bot_member: discord.Member) -> discord.ui.View | None:
        """Returns a View or None for react-mode panels."""
        mode = panel["mode"]
        usable = [m for m in mappings if _is_usable(m, bot_member)]
        if mode == "react" or not usable:
            return None

        view = discord.ui.View(timeout=None)
        if mode == "buttons":
            for m in usable:
                btn = discord.ui.Button(
                    label=m["description"] or str(m["role_id"]),
                    emoji=_safe_emoji(m["emoji"]),
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"rr:{panel['id']}:{m['role_id']}",
                )
                view.add_item(btn)
        elif mode == "dropdown":
            options = [
                discord.SelectOption(
                    label=(m["description"] or "Role")[:100],
                    value=str(m["role_id"]),
                    emoji=_safe_emoji(m["emoji"]),
                )
                for m in usable
            ]
            select = discord.ui.Select(
                placeholder="Select a role...",
                min_values=1,
                max_values=min(int(panel["max_selections"] or 1) or 1, len(options)),
                options=options,
                custom_id=f"rrsel:{panel['id']}",
            )
            view.add_item(select)
        return view


def _safe_emoji(emoji_str: str):
    try:
        return discord.PartialEmoji.from_str(emoji_str)
    except Exception:
        return None


def _is_usable(mapping: dict, bot_member: discord.Member) -> bool:
    role = bot_member.guild.get_role(int(mapping["role_id"]))
    if role is None:
        return False
    if role.is_default() or role.managed:
        return False
    if _role_is_restricted(role):
        return False
    return bot_can_manage(role, bot_member)
