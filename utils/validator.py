"""Input validators."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .errors import ValidationError

HEX_COLOR = re.compile(r"^#?[0-9a-fA-F]{6}$")
URL_RE = re.compile(r"^https?://", re.I)


def validate_color(value: str | int) -> int:
    if isinstance(value, int):
        if 0 <= value <= 0xFFFFFF:
            return value
        raise ValidationError("Color must be a valid 24-bit integer.")
    if not HEX_COLOR.match(value):
        raise ValidationError("Color must be a hex value like `#5865F2`.")
    return int(value.lstrip("#"), 16)


def validate_url(url: str | None) -> str | None:
    if not url:
        return None
    if not URL_RE.match(url):
        raise ValidationError("URLs must start with `http://` or `https://`.")
    host = urlparse(url).hostname or ""
    if not host:
        raise ValidationError("URL is malformed.")
    return url


def clamp(text: str | None, limit: int) -> str:
    """Trim text to Discord's field length limits."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def validate_positive_int(value: int, *, minimum: int = 0, maximum: int = 10**9,
                          name: str = "value") -> int:
    if not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer.")
    if value < minimum or value > maximum:
        raise ValidationError(f"{name} must be between {minimum} and {maximum}.")
    return value
