"""Global slash command with Guild Install and User Install support."""

from __future__ import annotations

import logging
import math

import aiohttp
import discord
from discord import app_commands

from .config import Config, ROOT
from .providers import DemoProvider, DisabledProvider, HTTPProvider, ProviderError, StatsService
from .views import build_error_view, build_stats_view

log = logging.getLogger("statsbot")


def use_private_response(interaction: discord.Interaction, configured: bool) -> bool:
    """Honor server restrictions when only a user installation is available."""
    external_only = interaction.is_user_integration() and not interaction.is_guild_integration()
    return configured or bool(
        interaction.guild_id and external_only and not interaction.app_permissions.use_external_apps
    )


class StatsClient(discord.Client):
    def __init__(self, config: Config):
        super().__init__(
            intents=discord.Intents.none(),
            allowed_mentions=discord.AllowedMentions.none(),
            activity=discord.Activity(type=discord.ActivityType.watching, name="/stats · Rainbow Six Siege"),
        )
        self.config = config
        self.api_session: aiohttp.ClientSession | None = None
        self.service: StatsService | None = None
        self.tree = app_commands.CommandTree(self)
        self._register_commands()

    def _register_commands(self) -> None:
        @self.tree.command(name="stats", description="Zeigt R6-K/D, aktuellen Rang und die letzten 5 verfügbaren Matches.")
        @app_commands.allowed_installs(guilds=True, users=True)
        @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
        @app_commands.describe(platform="Wähle PlayStation, Xbox oder PC.", username="Der Spielername auf der gewählten Plattform.")
        @app_commands.choices(platform=[
            app_commands.Choice(name="PlayStation", value="playstation"),
            app_commands.Choice(name="Xbox", value="xbox"),
            app_commands.Choice(name="PC", value="pc"),
        ])
        @app_commands.checks.cooldown(1, 10.0, key=lambda interaction: interaction.user.id)
        async def stats(
            interaction: discord.Interaction,
            platform: app_commands.Choice[str],
            username: app_commands.Range[str, 1, 64],
        ) -> None:
            await interaction.response.defer(
                thinking=True, ephemeral=use_private_response(interaction, self.config.private),
            )
            try:
                if self.service is None:
                    raise ProviderError("Bot startet", "Bitte versuche es gleich noch einmal.")
                result = await self.service.get(platform.value, username)
                view = build_stats_view(result)
                files = []
                if not result.rank_icon_url:
                    files.append(discord.File(
                        ROOT / "assets" / "rank-placeholder.png", filename="rank-placeholder.png",
                    ))
                try:
                    await interaction.edit_original_response(
                        view=view, content=None, embeds=[], attachments=files,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                finally:
                    for file in files:
                        file.close()
            except ProviderError as exc:
                await self._send_error(interaction, exc.title, exc.message)

        @self.tree.error
        async def on_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            if isinstance(error, app_commands.CommandOnCooldown):
                await self._send_error(
                    interaction, "Kurz warten",
                    f"Du kannst /stats in {math.ceil(error.retry_after)} Sekunden wieder verwenden.",
                )
                return
            original = getattr(error, "original", error)
            if isinstance(original, discord.HTTPException):
                log.warning("Discord-Antwort fehlgeschlagen (Status %s).", original.status)
                return
            # Do not log provider URLs, credentials, response bodies or usernames.
            log.error("Interaktion %s fehlgeschlagen: %s", interaction.id, type(original).__name__)
            await self._send_error(
                interaction, "Abfrage fehlgeschlagen",
                "Die Statistiken konnten nicht angezeigt werden. Bitte versuche es erneut. "
                f"Fehlernummer: {interaction.id}",
            )

    async def _send_error(self, interaction: discord.Interaction, title: str, message: str) -> None:
        view = build_error_view(title, message)
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(
                    view=view, content=None, embeds=[], attachments=[],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await interaction.response.send_message(
                    view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
                )
        except discord.HTTPException as exc:
            log.warning("Fehlerhinweis konnte nicht gesendet werden (Status %s).", exc.status)

    async def setup_hook(self) -> None:
        self.api_session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=8))
        provider = {
            "arenyze": lambda: self._arenyze_provider(),
            "demo": lambda: DemoProvider(),
            "disabled": lambda: DisabledProvider(),
            "http": lambda: HTTPProvider(self.api_session, self.config),
        }[self.config.data_mode]()
        self.service = StatsService(provider, cache_ttl=self.config.cache_ttl)
        # Global registration is essential for user installations and DMs.
        # Use a dedicated Discord application: sync replaces its global command set.
        synced = await self.tree.sync()
        log.info("%d globaler Command registriert. Datenmodus: %s", len(synced), self.config.data_mode)

    def _arenyze_provider(self):
        from .arenyze import ArenyzeProvider
        return ArenyzeProvider(self.api_session, self.config)

    async def on_ready(self) -> None:
        log.info("blockedfps | Stats ist online. /stats ist bereit.")

    async def close(self) -> None:
        if self.service is not None:
            await self.service.close()
        if self.api_session is not None and not self.api_session.closed:
            await self.api_session.close()
        await super().close()
