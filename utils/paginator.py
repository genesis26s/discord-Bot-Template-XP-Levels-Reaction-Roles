"""Reusable pagination view."""
from __future__ import annotations

from typing import Optional

import discord


class Paginator(discord.ui.View):
    def __init__(self, pages: list[discord.Embed], author_id: int,
                 timeout: float = 180.0):
        super().__init__(timeout=timeout)
        if not pages:
            raise ValueError("Paginator requires at least one page.")
        self.pages = pages
        self.author_id = author_id
        self.index = 0
        self.message: Optional[discord.Message] = None
        self._refresh()

    def _refresh(self) -> None:
        self.prev_button.disabled = self.index == 0
        self.next_button.disabled = self.index >= len(self.pages) - 1
        self.counter.label = f"{self.index + 1}/{len(self.pages)}"

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This paginator belongs to someone else.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, _btn):
        if not await self._guard(interaction):
            return
        self.index = max(0, self.index - 1)
        self._refresh()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, interaction: discord.Interaction, _btn):
        pass

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, _btn):
        if not await self._guard(interaction):
            return
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._refresh()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
