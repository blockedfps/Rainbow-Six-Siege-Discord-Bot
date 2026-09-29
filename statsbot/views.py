"""Compact Discord Components V2 cards; no embeds or ordinary message content."""

from __future__ import annotations

from datetime import datetime
import unicodedata

import discord

from .models import Match, PLATFORMS, PlayerStats


FOOTER = "-# blockedfps | Stats"
PLACEHOLDER_URL = "attachment://rank-placeholder.png"


def _text(value: str, limit: int = 80) -> str:
    """Keep provider text on one line and prevent formatting or mention injection."""
    # Bounds apply after escaping, since backslashes count towards Discord's limit.
    value = " ".join(str(value).split())
    value = "".join(c for c in value if not unicodedata.category(c).startswith("C"))
    value = value.replace("<", "‹").replace(">", "›")
    value = discord.utils.escape_markdown(value).replace("@", "@\u200b")
    if len(value) > limit:
        value = value[: limit - 1].rstrip("\\") + "…"
    return value or "—"


def _number(value: int | float | None, *, decimal: bool = False) -> str:
    if value is None:
        return "—"
    rendered = f"{value:.2f}" if decimal else str(value)
    return _text(rendered, 24)


def _time(value: datetime, style: str = "R") -> str:
    return f"<t:{int(value.timestamp())}:{style}>"


def _match_line(match: Match, position: int) -> str:
    result = {"win": "Sieg", "loss": "Niederlage", "draw": "Unentschieden"}.get(
        match.result, "Ergebnis unbekannt"
    )
    score = f" · {_text(match.score, 24)}" if match.score else ""
    return (
        f"**{position:02d} · {result}**{score} · {_time(match.played_at, 'f')}\n"
        f"> {_text(match.map_name, 64)} · *{_text(match.mode, 56)}*\n"
        f"> **K/D {_text(match.kd, 24)}** · "
        f"{_number(match.kills)} K / {_number(match.deaths)} D / {_number(match.assists)} A"
    )


class StatsLayout(discord.ui.LayoutView):
    """A single black-accent container, with no interactive controls or expiry."""

    def __init__(self, *children: discord.ui.Item) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Container(*children, accent_colour=discord.Colour(0)))
        if self.content_length() > 4000:
            raise ValueError("Das Statistik-Layout überschreitet das Discord-Textlimit.")


def build_stats_view(stats: PlayerStats) -> discord.ui.LayoutView:
    """Build the card. The caller supplies the placeholder file when necessary."""
    children: list[discord.ui.Item] = []
    if stats.demo:
        children.append(discord.ui.TextDisplay("**DEMO · Beispieldaten, keine Live-Statistiken**"))
    platform = PLATFORMS.get(stats.platform, "Plattform unbekannt")
    children.extend([
        discord.ui.Section(
            f"## {_text(stats.username, 96)}\n"
            f"> **Rainbow Six Siege** · {_text(platform, 32)}\n"
            f"> *{_text(stats.season, 96)}*",
            accessory=discord.ui.Thumbnail(
                stats.rank_icon_url or PLACEHOLDER_URL,
                description=("Rang: " if stats.rank_icon_url else "Rangbild fehlt. Rang: ")
                + _text(stats.rank_name or 'Nicht verfügbar', 96),
            ),
        ),
        discord.ui.Separator(),
        discord.ui.TextDisplay(
            f"**K/D · {_text(stats.kd_scope, 96)}**\n"
            f"> **{_number(stats.kd, decimal=True)}**\n\n"
            f"**Aktueller Rang**\n"
            f"> **{_text(stats.rank_name or 'Nicht verfügbar', 96)}** · "
            f"{_number(stats.rank_points)} RP"
        ),
        discord.ui.Separator(),
    ])
    matches = sorted(stats.matches, key=lambda match: match.played_at, reverse=True)[:5]
    history_title = "**Letzte 5 Matches**"
    if stats.matches_status == "private":
        history = f"{history_title}\n> *Die Match-Historie dieses Profils ist privat.*"
    elif stats.matches_status != "available":
        history = f"{history_title}\n> *Der Datenanbieter stellt keine Match-Historie bereit.*"
    elif not matches:
        history = f"{history_title}\n> *Keine Matches in der verfügbaren Historie gefunden.*"
    else:
        available = f"\n*Nur {len(matches)} von 5 Matches verfügbar.*" if len(matches) < 5 else ""
        history = history_title + available + "\n\n" + "\n\n".join(
            _match_line(match, index) for index, match in enumerate(matches, start=1)
        )
    children.extend([
        discord.ui.TextDisplay(history),
        discord.ui.Separator(),
        discord.ui.TextDisplay(
            f"-# Datenstand {_time(stats.updated_at) if stats.updated_at else 'nicht angegeben'} · "
            f"{'Beispielwerte' if stats.demo else ('Aus dem Cache' if stats.cached else 'Frisch abgerufen')}\n{FOOTER}"
        ),
    ])
    return StatsLayout(*children)


def build_error_view(title: str, message: str) -> discord.ui.LayoutView:
    """Errors use the same black-accent Components V2 presentation."""
    return StatsLayout(
        discord.ui.TextDisplay(f"## {_text(title, 120)}"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(f"> {_text(message, 1400)}"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(FOOTER),
    )
