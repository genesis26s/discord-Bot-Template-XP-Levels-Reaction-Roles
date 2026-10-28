"""Interactive embed builder."""
from __future__ import annotations

import logging
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from services.embed_service import TEMPLATES, EmbedService, build_discord_embed
from utils.errors import ValidationError
from utils.permissions import require_admin
from utils.validators import validate_color

log = logging.getLogger(__name__)


def _empty_data() -> dict[str, Any]:
    return {"title": "", "description": "", "color": 0x5865F2, "fields": [],
            "author": {}, "footer": {}, "timestamp": False,
            "thumbnail": None, "image": None}


# ---- Modals -------------------------------------------------------------
class BaseModal(discord.ui.Modal):
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await interaction.response.send_message(f"❌ {error}", ephemeral=True)


class BasicModal(BaseModal):
    def __init__(self, view: "EmbedBuilderView"):
        super().__init__(title="Embed — Basic")
        self.view_ref = view
        d = view.data
        self.title_in = discord.ui.TextInput(
            label="Title", max_length=256, required=False,
            default=d.get("title", ""))
        self.desc_in = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph,
            max_length=4000, required=False, default=d.get("description", ""))
        self.color_in = discord.ui.TextInput(
            label="Color (hex)", required=False,
            default=f"#{d.get('color', 0x5865F2):06X}")
        for item in (self.title_in, self.desc_in, self.color_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        d = self.view_ref.data
        d["title"] = self.title_in.value or ""
        d["description"] = self.desc_in.value or ""
        try:
            d["color"] = validate_color(self.color_in.value or "#5865F2")
        except ValidationError as e:
            return await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        await self.view_ref.refresh(interaction)


class AuthorModal(BaseModal):
    def __init__(self, view: "EmbedBuilderView"):
        super().__init__(title="Embed — Author")
        self.view_ref = view
        a = view.data.get("author") or {}
        self.name_in = discord.ui.TextInput(label="Author name", max_length=256,
                                            required=False, default=a.get("name", ""))
        self.icon_in = discord.ui.TextInput(label="Author icon URL", required=False,
                                            default=a.get("icon_url") or "")
        self.url_in = discord.ui.TextInput(label="Author link URL", required=False,
                                           default=a.get("url") or "")
        for item in (self.name_in, self.icon_in, self.url_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        self.view_ref.data["author"] = {
            "name": self.name_in.value or "",
            "icon_url": self.icon_in.value or None,
            "url": self.url_in.value or None,
        }
        await self.view_ref.refresh(interaction)


class FooterModal(BaseModal):
    def __init__(self, view: "EmbedBuilderView"):
        super().__init__(title="Embed — Footer")
        self.view_ref = view
        f = view.data.get("footer") or {}
        self.text_in = discord.ui.TextInput(label="Footer text", max_length=2048,
                                            required=False, default=f.get("text", ""))
        self.icon_in = discord.ui.TextInput(label="Footer icon URL", required=False,
                                            default=f.get("icon_url") or "")
        for item in (self.text_in, self.icon_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        self.view_ref.data["footer"] = {
            "text": self.text_in.value or "",
            "icon_url": self.icon_in.value or None,
        }
        await self.view_ref.refresh(interaction)


class ImagesModal(BaseModal):
    def __init__(self, view: "EmbedBuilderView"):
        super().__init__(title="Embed — Images")
        self.view_ref = view
        self.thumb_in = discord.ui.TextInput(label="Thumbnail URL", required=False,
                                             default=view.data.get("thumbnail") or "")
        self.image_in = discord.ui.TextInput(label="Image URL", required=False,
                                             default=view.data.get("image") or "")
        for item in (self.thumb_in, self.image_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        self.view_ref.data["thumbnail"] = self.thumb_in.value or None
        self.view_ref.data["image"] = self.image_in.value or None
        await self.view_ref.refresh(interaction)


class FieldModal(BaseModal):
    def __init__(self, view: "EmbedBuilderView"):
        super().__init__(title="Embed — Add field")
        self.view_ref = view
        self.name_in = discord.ui.TextInput(label="Field name", max_length=256)
        self.value_in = discord.ui.TextInput(label="Field value", style=discord.TextStyle.paragraph,
                                             max_length=1024)
        self.inline_in = discord.ui.TextInput(label="Inline? (yes/no)", max_length=3,
                                              default="no", required=False)
        for item in (self.name_in, self.value_in, self.inline_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        self.view_ref.data.setdefault("fields", []).append({
            "name": self.name_in.value,
            "value": self.value_in.value,
            "inline": (self.inline_in.value or "").lower() in ("yes", "y", "true"),
        })
        await self.view_ref.refresh(interaction)


# ---- Builder view -------------------------------------------------------
class EmbedBuilderView(discord.ui.View):
    def __init__(self, service: EmbedService, author_id: int, guild_id: int,
                 name: str, data: dict[str, Any]):
        super().__init__(timeout=900)
        self.service = service
        self.author_id = author_id
        self.guild_id = guild_id
        self.name = name
        self.data = data
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This builder belongs to someone else.", ephemeral=True)
            return False
        return True

    async def refresh(self, interaction: discord.Interaction):
        try:
            embed = build_discord_embed(self.data)
        except ValidationError as e:
            return await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        if interaction.response.is_done():
            if self.message:
                await self.message.edit(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    # --- buttons ---
    @discord.ui.button(label="Basic", style=discord.ButtonStyle.primary, row=0)
    async def edit_basic(self, interaction, _):
        await interaction.response.send_modal(BasicModal(self))

    @discord.ui.button(label="Author", style=discord.ButtonStyle.secondary, row=0)
    async def edit_author(self, interaction, _):
        await interaction.response.send_modal(AuthorModal(self))

    @discord.ui.button(label="Footer", style=discord.ButtonStyle.secondary, row=0)
    async def edit_footer(self, interaction, _):
        await interaction.response.send_modal(FooterModal(self))

    @discord.ui.button(label="Images", style=discord.ButtonStyle.secondary, row=0)
    async def edit_images(self, interaction, _):
        await interaction.response.send_modal(ImagesModal(self))

    @discord.ui.button(label="Add field", style=discord.ButtonStyle.success, row=1)
    async def add_field(self, interaction, _):
        if len(self.data.get("fields", [])) >= 25:
            return await interaction.response.send_message(
                "Maximum of 25 fields.", ephemeral=True)
        await interaction.response.send_modal(FieldModal(self))

    @discord.ui.button(label="Timestamp", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_ts(self, interaction, _):
        self.data["timestamp"] = not self.data.get("timestamp", False)
        await self.refresh(interaction)

    @discord.ui.button(label="Clear fields", style=discord.ButtonStyle.danger, row=1)
    async def clear_fields(self, interaction, _):
        self.data["fields"] = []
        await self.refresh(interaction)

    @discord.ui.button(label="Save", style=discord.ButtonStyle.success, row=2)
    async def save(self, interaction, _):
        try:
            await self.service.save(self.guild_id, self.name, self.author_id, self.data)
        except ValidationError as e:
            return await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        await interaction.response.send_message(
            f"✅ Saved embed **{self.name}**.", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction, _):
        await interaction.response.send_message("Cancelled.", ephemeral=True)
        self.stop()


# ---- Cog ----------------------------------------------------------------
class Embeds(commands.Cog):
    """Interactive embed builder."""

    embed = app_commands.Group(name="embed", description="Saved embed management.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.service = EmbedService(bot.db)

    @embed.command(name="create", description="Create a new saved embed.")
    @require_admin()
    @app_commands.describe(name="Unique name for this embed")
    async def create(self, interaction: discord.Interaction, name: str):
        assert interaction.guild
        existing = await self.service.get(interaction.guild.id, name)
        if existing:
            return await interaction.response.send_message(
                f"Embed **{name}** already exists — use `/embed edit`.", ephemeral=True)
        view = EmbedBuilderView(self.service, interaction.user.id,
                                interaction.guild.id, name, _empty_data())
        try:
            embed = build_discord_embed(view.data)
        except ValidationError:
            embed = discord.Embed(title="Builder", description="Starting…",
                                  color=0x5865F2)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @embed.command(name="edit", description="Edit a saved embed.")
    @require_admin()
    @app_commands.describe(name="Name of saved embed")
    async def edit(self, interaction: discord.Interaction, name: str):
        assert interaction.guild
        row = await self.service.get(interaction.guild.id, name)
        if not row:
            return await interaction.response.send_message(
                f"No embed named **{name}**.", ephemeral=True)
        view = EmbedBuilderView(self.service, interaction.user.id,
                                interaction.guild.id, name, row["data"])
        embed = build_discord_embed(view.data)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @embed.command(name="preview", description="Preview a saved embed.")
    @require_admin()
    @app_commands.describe(name="Name of saved embed")
    async def preview(self, interaction: discord.Interaction, name: str):
        assert interaction.guild
        row = await self.service.get(interaction.guild.id, name)
        if not row:
            return await interaction.response.send_message("Not found.", ephemeral=True)
        embed = build_discord_embed(row["data"])
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @embed.command(name="send", description="Send a saved embed to a channel.")
    @require_admin()
    @app_commands.describe(name="Name of saved embed", channel="Target channel")
    async def send(self, interaction: discord.Interaction, name: str,
                   channel: discord.TextChannel):
        assert interaction.guild
        row = await self.service.get(interaction.guild.id, name)
        if not row:
            return await interaction.response.send_message("Not found.", ephemeral=True)
        embed = build_discord_embed(row["data"])
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as e:
            return await interaction.response.send_message(
                f"❌ Failed to send: {e}", ephemeral=True)
        await interaction.response.send_message(
            f"✅ Sent to {channel.mention}.", ephemeral=True)

    @embed.command(name="list", description="List saved embeds.")
    @require_admin()
    async def list_cmd(self, interaction: discord.Interaction):
        assert interaction.guild
        rows = await self.service.list(interaction.guild.id)
        if not rows:
            return await interaction.response.send_message("No embeds saved.",
                                                           ephemeral=True)
        lines = [f"• **{r['name']}** — <@{r['author_id']}>" for r in rows]
        embed = discord.Embed(title="📚 Saved embeds", color=0x5865F2,
                              description="\n".join(lines))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @embed.command(name="delete", description="Delete a saved embed.")
    @require_admin()
    @app_commands.describe(name="Name of saved embed")
    async def delete(self, interaction: discord.Interaction, name: str):
        assert interaction.guild
        row = await self.service.get(interaction.guild.id, name)
        if not row:
            return await interaction.response.send_message("Not found.", ephemeral=True)
        # Only the author or an administrator may delete
        if row["author_id"] != interaction.user.id and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "Only the embed's author or a server admin can delete this.",
                ephemeral=True)
        await self.service.delete(interaction.guild.id, name)
        await interaction.response.send_message(f"Deleted **{name}**.", ephemeral=True)

    @embed.command(name="template", description="Start from a predefined template.")
    @require_admin()
    @app_commands.describe(template="Template name", name="Save-as name")
    @app_commands.choices(template=[app_commands.Choice(name=k, value=k) for k in TEMPLATES])
    async def template(self, interaction: discord.Interaction, template: str, name: str):
        assert interaction.guild
        if template not in TEMPLATES:
            return await interaction.response.send_message("Unknown template.",
                                                           ephemeral=True)
        data = dict(TEMPLATES[template])
        view = EmbedBuilderView(self.service, interaction.user.id,
                                interaction.guild.id, name, data)
        embed = build_discord_embed(data)
        await interaction.response.send_message(
            content=f"Using template `{template}` — save when ready.",
            embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(Embeds(bot))
