"""Saved-embed business logic."""
from __future__ import annotations

import json
from typing import Any, Optional

import discord

from database import Database
from utils.errors import ValidationError
from utils.validators import clamp, validate_color, validate_url


EMBED_LIMITS = {
    "title": 256,
    "description": 4096,
    "field_name": 256,
    "field_value": 1024,
    "footer_text": 2048,
    "author_name": 256,
    "total": 6000,
    "fields": 25,
}


def validate_embed_data(data: dict[str, Any]) -> dict[str, Any]:
    """Validate/normalise raw embed data. Raises ValidationError on bad input."""
    out: dict[str, Any] = {}
    title = clamp(data.get("title") or "", EMBED_LIMITS["title"])
    desc = clamp(data.get("description") or "", EMBED_LIMITS["description"])
    if not title and not desc and not data.get("fields"):
        raise ValidationError("Embed needs a title, description, or at least one field.")
    out["title"] = title
    out["description"] = desc

    if "color" in data and data["color"] is not None:
        out["color"] = validate_color(data["color"])
    else:
        out["color"] = 0x5865F2

    # author
    author = data.get("author") or {}
    if author.get("name"):
        out["author"] = {
            "name": clamp(author["name"], EMBED_LIMITS["author_name"]),
            "icon_url": validate_url(author.get("icon_url")),
            "url": validate_url(author.get("url")),
        }

    footer = data.get("footer") or {}
    if footer.get("text"):
        out["footer"] = {
            "text": clamp(footer["text"], EMBED_LIMITS["footer_text"]),
            "icon_url": validate_url(footer.get("icon_url")),
        }

    thumbnail = validate_url(data.get("thumbnail"))
    image = validate_url(data.get("image"))
    if thumbnail: out["thumbnail"] = thumbnail
    if image: out["image"] = image

    fields = data.get("fields") or []
    if len(fields) > EMBED_LIMITS["fields"]:
        raise ValidationError(f"An embed can only have {EMBED_LIMITS['fields']} fields.")
    clean_fields = []
    for f in fields:
        name = clamp(f.get("name") or "", EMBED_LIMITS["field_name"])
        value = clamp(f.get("value") or "", EMBED_LIMITS["field_value"])
        if not name or not value:
            continue
        clean_fields.append({"name": name, "value": value,
                             "inline": bool(f.get("inline", False))})
    out["fields"] = clean_fields
    out["timestamp"] = bool(data.get("timestamp"))

    # Total length check
    total = len(out["title"]) + len(out["description"]) + len(out["author"].get("name", "") if "author" in out else "")
    total += len(out["footer"].get("text", "") if "footer" in out else "")
    for f in clean_fields:
        total += len(f["name"]) + len(f["value"])
    if total > EMBED_LIMITS["total"]:
        raise ValidationError(f"Embed total content exceeds {EMBED_LIMITS['total']} chars.")
    return out


def build_discord_embed(data: dict[str, Any]) -> discord.Embed:
    safe = validate_embed_data(data)
    embed = discord.Embed(
        title=safe["title"] or None,
        description=safe["description"] or None,
        color=safe["color"],
    )
    if "author" in safe:
        embed.set_author(
            name=safe["author"]["name"],
            icon_url=safe["author"].get("icon_url"),
            url=safe["author"].get("url"),
        )
    if "footer" in safe:
        embed.set_footer(
            text=safe["footer"]["text"],
            icon_url=safe["footer"].get("icon_url"),
        )
    if safe.get("thumbnail"):
        embed.set_thumbnail(url=safe["thumbnail"])
    if safe.get("image"):
        embed.set_image(url=safe["image"])
    for f in safe["fields"]:
        embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])
    if safe.get("timestamp"):
        embed.timestamp = discord.utils.utcnow()
    return embed


TEMPLATES: dict[str, dict[str, Any]] = {
    "rules": {
        "title": "📜 Server Rules",
        "description": "1. Be respectful.\n2. No spam.\n3. Follow Discord TOS.",
        "color": 0x5865F2,
        "footer": {"text": "Read carefully before participating."},
    },
    "welcome": {
        "title": "👋 Welcome!",
        "description": "We're glad you're here. Introduce yourself in #general.",
        "color": 0x57F287,
    },
    "announcement": {
        "title": "📣 Announcement",
        "description": "Replace this text with your announcement.",
        "color": 0xFEE75C,
    },
    "info": {
        "title": "ℹ️ Information",
        "description": "Replace with useful information.",
        "color": 0x00B0F4,
    },
}


class EmbedService:
    def __init__(self, db: Database):
        self.db = db

    async def save(self, guild_id: int, name: str, author_id: int, data: dict[str, Any]) -> None:
        validated = validate_embed_data(data)
        payload = json.dumps(validated)
        existing = await self.get(guild_id, name)
        if existing:
            await self.db.execute(
                "UPDATE saved_embeds SET data=?, updated_at=strftime('%s','now') "
                "WHERE guild_id=? AND name=?",
                (payload, guild_id, name),
            )
        else:
            await self.db.execute(
                "INSERT INTO saved_embeds (guild_id, name, author_id, data) VALUES (?,?,?,?)",
                (guild_id, name, author_id, payload),
            )

    async def get(self, guild_id: int, name: str) -> Optional[dict[str, Any]]:
        row = await self.db.fetchone(
            "SELECT * FROM saved_embeds WHERE guild_id=? AND name=?", (guild_id, name)
        )
        if not row:
            return None
        row["data"] = json.loads(row["data"])
        return row

    async def list(self, guild_id: int) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT name, author_id, created_at, updated_at FROM saved_embeds "
            "WHERE guild_id=? ORDER BY name ASC", (guild_id,)
        )
        return rows

    async def delete(self, guild_id: int, name: str) -> bool:
        existing = await self.get(guild_id, name)
        if not existing:
            return False
        await self.db.execute(
            "DELETE FROM saved_embeds WHERE guild_id=? AND name=?", (guild_id, name)
        )
        return True
