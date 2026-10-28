"""Reaction-role persistence and panel building."""
from __future__ import annotations

from typing import Any, Optional

import discord

from database import Database
from utils.errors import ValidationError
from utils.permissions import bot_can_manage


VALID_MODES = {"react", "buttons", "dropdown"}
RESTRICTED_PERMS = {"administrator", "manage_guild", "manage_roles",
                    "manage_channels", "manage_messages", "kick_members",
                    "ban_members", "moderate_members"}


def _role_is_restricted(role: discord.Role) -> bool:
    perms = role.permissions
    return any(getattr(perms, p) for p in RESTRICTED_PERMS)


def _safe_emoji(emoji_str: str):
    try:
        return discord.PartialEmoji.from_str(emoji_str)
    except Exception:
        return None


# =====================================================================
# Persistent view classes (registered via bot.add_view).
# =====================================================================
class ReactionRoleButtonView(discord.ui.View):
    def __init__(self, panel: dict, mappings: list[dict]):
        super().__init__(timeout=None)
        self.panel = panel
        for m in mappings:
            role_id = int(m["role_id"])
            btn = discord.ui.Button(
                label=(m.get("description") or f"Role {role_id}")[:80],
                emoji=_safe_emoji(m["emoji"]),
                style=discord.ButtonStyle.secondary,
                custom_id=f"rr:{panel['id']}:{role_id}",
            )
            btn.callback = self._make_callback(role_id)
            self.add_item(btn)

    def _make_callback(self, role_id: int):
        async def callback(interaction: discord.Interaction):
            await _toggle_role(interaction, role_id)
        return callback


class ReactionRoleDropdownView(discord.ui.View):
    def __init__(self, panel: dict, mappings: list[dict]):
        super().__init__(timeout=None)
        self.panel = panel
        options = [
            discord.SelectOption(
                label=(m.get("description") or f"Role {m['role_id']}")[:100],
                value=str(m["role_id"]),
                emoji=_safe_emoji(m["emoji"]),
            )
            for m in mappings
        ]
        if not options:
            return
        max_sel = int(panel.get("max_selections") or 0)
        actual_max = len(options) if max_sel == 0 else min(max_sel, len(options))
        select = discord.ui.Select(
            placeholder="Select your roles...",
            min_values=0,   # allow deselecting everything (empty submission)
            max_values=actual_max,
            options=options,
            custom_id=f"rrsel:{panel['id']}",
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        await _apply_select(interaction, self.panel)


# =====================================================================
# Module-level interaction handlers (used by the views).
# =====================================================================
async def _toggle_role(interaction: discord.Interaction, role_id: int):
    guild = interaction.guild
    member = interaction.user
    if not guild or not isinstance(member, discord.Member):
        return await interaction.response.send_message(
            "This only works in a server.", ephemeral=True)
    role = guild.get_role(role_id)
    if not role or not guild.me or not bot_can_manage(role, guild.me):
        return await interaction.response.send_message(
            "That role is unavailable.", ephemeral=True)
    try:
        if role in member.roles:
            await member.remove_roles(role, reason="Role button toggle")
            msg = f"Removed {role.mention}."
        else:
            await member.add_roles(role, reason="Role button toggle")
            msg = f"Added {role.mention}."
    except discord.HTTPException:
        return await interaction.response.send_message(
            "Failed to update your roles.", ephemeral=True)
    await interaction.response.send_message(msg, ephemeral=True)


async def _apply_select(interaction: discord.Interaction, panel: dict):
    guild = interaction.guild
    member = interaction.user
    if not guild or not isinstance(member, discord.Member):
        return await interaction.response.send_message(
            "This only works in a server.", ephemeral=True)

    selected = {int(v) for v in (interaction.data or {}).get("values", [])}

    # Get this panel's mappings from the interaction's parent (the service
    # caller is responsible for keeping the DB in sync; here we just diff
    # against the panel's configured roles stored on the view).
    # The view stashes the panel; we look up mappings via the panel id.
    db_mappings = await _fetch_mappings(interaction, panel["id"])
    panel_role_ids = {int(m["role_id"]) for m in db_mappings}

    current = {r.id for r in member.roles}

    to_add: list[discord.Role] = []
    to_remove: list[discord.Role] = []
    for rid in panel_role_ids:
        role = guild.get_role(rid)
        if not role or not guild.me or not bot_can_manage(role, guild.me):
            continue
        if rid in selected and rid not in current:
            to_add.append(role)
        elif rid not in selected and rid in current:
            to_remove.append(role)

    await interaction.response.defer(ephemeral=True)

    for role in to_add:
        try:
            await member.add_roles(role, reason="Self role")
        except discord.HTTPException:
            pass
    for role in to_remove:
        try:
            await member.remove_roles(role, reason="Self role")
        except discord.HTTPException:
            pass

    parts = []
    if to_add:
        parts.append("Added: " + ", ".join(r.mention for r in to_add))
    if to_remove:
        parts.append("Removed: " + ", ".join(r.mention for r in to_remove))
    await interaction.followup.send(" | ".join(parts) or "No changes.",
                                    ephemeral=True)


async def _fetch_mappings(interaction: discord.Interaction, panel_id: int) -> list[dict]:
    # Reuse whatever DB is attached to the bot — no global state.
    bot = interaction.client
    db = getattr(bot, "db", None)
    if db is None:
        return []
    return await db.fetchall(
        "SELECT * FROM reaction_role_mappings WHERE panel_id=? ORDER BY id ASC",
        (panel_id,),
    )


# =====================================================================
# Service
# =====================================================================
class ReactionRoleService:
    def __init__(self, db: Database):
        self.db = db

    async def create_panel(self, guild_id: int, name: str, channel_id: int,
                           mode: str, description: str | None,
                           max_selections: int = 0) -> int:
        if mode not in VALID_MODES:
            raise ValidationError(f"mode must be one of {sorted(VALID_MODES)}")
        if await self.get_panel_by_name(guild_id, name):
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

    async def list_all_panels(self) -> list[dict]:
        return await self.db.fetchall(
            "SELECT * FROM reaction_role_panels ORDER BY id ASC"
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

    # ---- rendering ----------------------------------------------------
    async def build_panel_message(self, panel: dict,
                                    mappings: list[dict]) -> discord.Embed:
        embed = discord.Embed(
            title=f"🎭 {panel['name']}",
            description=panel.get("description") or "Select a role below.",
            color=0x5865F2,
        )
        for m in mappings:
            label = f"{m['emoji']}  →  <@&{m['role_id']}>"
            if m.get("description"):
                label += f"\n*{m['description']}*"
            embed.add_field(name="\u200b", value=label, inline=False)
        if panel["mode"] == "dropdown":
            max_sel = int(panel.get("max_selections") or 0)
            if max_sel > 0:
                embed.set_footer(text=f"Select up to {max_sel} role(s).")
            else:
                embed.set_footer(text="Select any number of roles.")
        return embed

    async def build_view(self, panel: dict, mappings: list[dict],
                          bot: Any) -> Optional[discord.ui.View]:
        mode = panel["mode"]
        if mode == "react" or not mappings:
            return None
        if mode == "buttons":
            return ReactionRoleButtonView(panel, mappings)
        if mode == "dropdown":
            return ReactionRoleDropdownView(panel, mappings)
        return None
