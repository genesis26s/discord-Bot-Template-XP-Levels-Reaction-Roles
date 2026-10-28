# Discord Bot Template

A production-ready, modular **Python 3.11+ / discord.py 2.x** bot template with:
- Full **leveling system** (XP, ranks, leaderboard, rewards, cooldowns)
- Interactive **embed builder**
- **Reaction-role** panels (reactions, buttons, dropdowns)
- Interactive `/setup` **administration panel**
- Full **SQLite** persistence, tests, and structured logging

Every feature is per-guild and configurable via slash commands — no source edits needed.

---

## 1. Prerequisites

- Python **3.11+**
- A Discord application with a **bot** account
- (Optional) `git`, `sqlite3` CLI

## 2. Create the Discord application

1. Go to https://discord.com/developers/applications → **New Application**
2. **Bot** tab → **Reset Token** → copy it (store it safely — you'll paste it into `.env` only)
3. **Privileged Gateway Intents**: enable **Message Content Intent** and **Server Members Intent**.
   - **Required:** `MESSAGE_CONTENT` (for per-message XP), `SERVER_MEMBERS` (member lookup for role rewards).
   - **Optional:** `PRESENCE` (not used by this template — leave off).
4. **OAuth2 → URL Generator**: scopes `bot`, `applications.commands`; permissions:  
   `Manage Roles`, `Manage Channels`, `Send Messages`, `Embed Links`, `Add Reactions`, `Read Message History`, `Use External Emojis`.
5. Use the generated URL to invite the bot to a test server.

## 3. Install

```bash
git clone <your-fork> discord-bot-template
cd discord-bot-template
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
