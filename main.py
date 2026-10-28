"""Entry point."""
import asyncio
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Fail fast with a clear message if the package layout is wrong.
for _pkg in ("utils", "cogs", "services"):
    _p = BASE_DIR / _pkg
    if not _p.is_dir():
        raise RuntimeError(f"Missing directory: {_p}")
    if not (_p / "__init__.py").exists():
        raise RuntimeError(
            f"Missing {_pkg}/__init__.py — relative imports will fail.\n"
            f"Fix: touch {_pkg}/__init__.py"
        )

from bot import run  # noqa: E402

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("Shutdown requested.")
