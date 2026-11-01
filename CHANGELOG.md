# Changelog

All notable changes to this template are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/); versioning: [SemVer](https://semver.org/).

## [Unreleased]

## [1.0.0] — 2025-11-01

### Added
- Leveling system with per-guild XP config, cooldowns, ignored channels/roles,
  role rewards, `/rank`, `/leaderboard`, configurable announcements.
- Interactive embed builder with 7 slash commands, 5 modals, 4 templates.
- Reaction-role panels in three modes (`react`, `buttons`, `dropdown`) with
  persistent views that survive restarts.
- Interactive `/setup` admin panel with actionable controls and `/panel` alias.
- `/config export` / `/config import` round-trip with validation and rollback.
- `/diagnostics`, `/permissions` health checks.
- `/leveling sync-rewards` backfill for existing members.
- `/leveling xp-table` preview, `/leveling rebuild-levels` for curve changes.
- Full pytest suite covering curve math, DB isolation, cooldown race, embed
  validation, permission enforcement, and FK cascade deletes.
- Structured logging, WAL-mode SQLite, serialized async writes, transactional
  config import.

### Security
- All admin commands require Administrator or Manage Server.
- Every interactive component re-checks permissions on click.
- Self-assignable roles blocked if elevated, managed, or above bot's top role.
- `/config import` validates types, bounds, and MIME before writing.
- Deleting a saved embed requires authorship or Administrator.

### Fixed
- Wired `tree.on_error` (previously a dead `on_app_command_error` method —
  every user-facing error showed "application did not respond").
- Reaction-role buttons and dropdowns now use persistent `discord.ui.View`
  subclasses registered with `bot.add_view`, restoring click handling after
  restart without conflicting with `on_interaction`.
- Dropdown single-select now removes roles when a new choice is picked and
  when nothing is selected.
- XP cooldown is atomic — concurrent messages from the same user can no
  longer award double XP.
- SQLite `PRAGMA foreign_keys` now applied outside `executescript` and
  verified at connect time (cascade deletes now actually work).
- `/help` recurses into command groups so `/leveling setup` etc. appear.
- `/botinfo` uptime renders as a duration, not a past timestamp.
- Embed builder always acknowledges interactions — no more silent failures.
- Emoji inputs validated before being stored.
- Uncached members can now have reaction roles removed.
- Paths anchor to the script directory, not the CWD.
