"""Environment-based configuration."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Config:
    DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/bot.db")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    DEBUG_GUILD_ID: int = int(os.getenv("DEBUG_GUILD_ID", "0") or 0)

    @classmethod
    def validate(cls) -> None:
        if not cls.DISCORD_TOKEN:
            raise ValueError("DISCORD_TOKEN is required (set it in .env)")
        Path("data").mkdir(exist_ok=True)
        Path("logs").mkdir(exist_ok=True)

config = Config()
