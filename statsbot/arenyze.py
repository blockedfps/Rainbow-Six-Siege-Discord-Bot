"""Arenyze V2 profile adapter; never treats RP history as match history.

The public documentation includes empty V2 arrays and fuller V1 examples of
their contents. Selection is deliberately conservative: currentSeason is
required, only the requested platform family and ranked playlist are used,
and season segment rankPoints (which can represent a peak) never become the
current rank. See docs/API_OPTIONS.md for the integration's limitations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import aiohttp

from .config import Config
from .models import DataError, PlayerStats, _number, _text, _timestamp, https_url
from .providers import HTTPProvider, ProviderError, normalize_query


ENDPOINT = "https://public-api.arenyze.com/r6/api/v2/profile"
PLATFORM_MAPPING = {"pc": ("uplay", "pc"), "playstation": ("psn", "console"), "xbox": ("xbl", "console")}


def _object(value: Any, field: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DataError(f"Objekt erwartet: {field}")
    return value


def _list(value: Any, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DataError(f"Liste erwartet: {field}")
    return value


def _season_id(value: Any) -> str | None:
    if type(value) is int and value >= 0:
        return str(value)
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 8:
        return str(int(value))
    return None


def _time(value: Any) -> datetime | None:
    try:
        return _timestamp(value, "update_time")
    except DataError:
        return None


def _family(value: Any) -> str | None:
    if value in ("pc", "uplay", "ubi"):
        return "pc"
    if value in ("console", "psn", "xbl", "playstation", "xbox"):
        return "console"
    return None


def _current_segment(data: dict, current: str, family: str) -> dict:
    candidates = []
    for raw in _list(data.get("segments"), "segments"):
        segment = _object(raw, "segment")
        attr = _object(segment.get("attributes"), "segment.attributes")
        if segment.get("type") != "season" or _season_id(attr.get("season")) != current:
            continue
        if _family(attr.get("platform")) != family:
            continue
        if attr.get("gamemode") != "pvp_ranked" and attr.get("sessionType") != "ranked":
            continue
        # Regional fragments cannot safely stand in for a global season KD.
        if attr.get("region") not in (None, "", "all", "global"):
            continue
        candidates.append(segment)
    # Never resolve duplicate seasons by highest RP or by API list order.
    return candidates[0] if len(candidates) == 1 else {}


def _current_board(stats: dict, current: str, family: str, response_family: str | None) -> dict:
    families = _list(stats.get("platform_families_full_profiles"), "platform_families_full_profiles")
    candidates: list[dict] = []
    for raw in families:
        group = _object(raw, "platform family")
        declared = group.get("platform_family", group.get("platform_families"))
        actual_family = _family(declared)
        # The published example omits the family field. Only infer it for a
        # single family group when the V2 response explicitly confirms it.
        if declared is None and len(families) == 1:
            actual_family = response_family
        if actual_family != family:
            continue
        for raw_board in _list(group.get("board_ids_full_profiles"), "board_ids_full_profiles"):
            board = _object(raw_board, "board")
            if board.get("board_id") not in ("ranked", "pvp_ranked"):
                continue
            for raw_profile in _list(board.get("full_profiles"), "full_profiles"):
                full = _object(raw_profile, "full profile")
                if _season_id(full.get("season_id")) == current:
                    candidates.append(full)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        # Choose a uniquely latest current snapshot; never the highest rank.
        dated = [(_time(_object(item.get("profile"), "profile").get("update_time")), item) for item in candidates]
        if all(stamp is not None for stamp, _ in dated):
            dated.sort(key=lambda pair: pair[0], reverse=True)
            if dated[0][0] != dated[1][0]:
                return dated[0][1]
    return {}


def _kd(full: dict, segment: dict) -> float | None:
    # Both documented season_statistics and current profile hold season totals.
    for raw in (full.get("season_statistics"), full.get("profile")):
        totals = _object(raw, "season totals")
        if totals.get("kills") is not None and totals.get("deaths") is not None:
            kills = _number(totals["kills"], "kills", integer=True)
            deaths = _number(totals["deaths"], "deaths", integer=True)
            # Infinity is not part of the normalized numeric contract.
            return kills / deaths if deaths else None
    stats = _object(segment.get("stats"), "segment.stats")
    ratio = _object(stats.get("kdRatio"), "kdRatio")
    return _number(ratio.get("value"), "kdRatio.value")


def _rank_from_history(history: dict, points: int | None) -> tuple[str | None, str | None]:
    if points is None:
        return None, None
    data = _object(history.get("data"), "history.data")
    series = _object(data.get("history"), "history.data.history")
    entries = []
    for entry in _list(series.get("data"), "history entries"):
        if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[1], dict):
            continue
        stamp = _time(entry[0])
        if stamp is not None:
            entries.append((stamp, entry[1]))
    if not entries:
        return None, None
    entries.sort(key=lambda pair: pair[0], reverse=True)
    # A matching *old* point must not override a newer, inconsistent point.
    if len(entries) > 1 and entries[0][0] == entries[1][0] and entries[0][1] != entries[1][1]:
        return None, None
    latest = entries[0][1]
    if _number(latest.get("value"), "history RP", integer=True) != points:
        return None, None
    meta = _object(latest.get("metadata"), "rank metadata")
    label = meta.get("rank")
    if not isinstance(label, str) or not label.strip():
        return None, None
    return _text(label, "rank", 60), https_url(meta.get("imageUrl"))


def parse_arenyze(payload: Any, *, expected_platform: str) -> PlayerStats:
    """Normalize documented fields; unknown current data raises DataError."""
    root = _object(payload, "response")
    # Some API gateways wrap an otherwise unchanged response in `data`.
    if "player" not in root and isinstance(root.get("data"), dict) and "player" in root["data"]:
        root = root["data"]
    if root.get("error") or root.get("success") is False:
        raise DataError("Anbieter meldet einen Fehler.")
    player = _object(root.get("player"), "player")
    upstream_platform, family = PLATFORM_MAPPING[expected_platform]
    if player.get("platformType") != upstream_platform:
        raise DataError("Die Antwort gehört zu einer anderen Plattform.")
    response_family = _family(player.get("platformFamilies"))
    if player.get("platformFamilies") is not None and response_family != family:
        raise DataError("Die Antwort gehört zu einer anderen Plattformfamilie.")
    username = _text(player.get("nameOnPlatform"), "nameOnPlatform", 64)
    meta = _object(root.get("meta"), "meta")
    if "partial" in meta and type(meta["partial"]) is not bool:
        raise DataError("meta.partial ist ungültig.")
    errors = _object(meta.get("errors"), "meta.errors")
    # Ignore datasets explicitly marked as failed, even if stale data remains.
    seasons = {} if "seasons" in errors else _object(root.get("seasons"), "seasons")
    data = _object(seasons.get("data"), "seasons.data")
    season_meta = _object(data.get("metadata"), "seasons.metadata")
    current = _season_id(season_meta.get("currentSeason"))
    if current is None:
        raise DataError("Aktuelle Saison nicht eindeutig angegeben.")
    stats = {} if "stats" in errors else _object(root.get("stats"), "stats")
    full = _current_board(stats, current, family, response_family)
    segment = _current_segment(data, current, family)
    if not full and not segment:
        raise DataError("Keine eindeutigen aktuellen Ranked-Daten.")
    profile = _object(full.get("profile"), "profile")
    points = _number(profile.get("rank_points"), "rank_points", integer=True)
    rank_id = _number(profile.get("rank"), "rank", integer=True)
    history = {} if "history" in errors else _object(root.get("history"), "history")
    label, icon = _rank_from_history(history, points)
    if label is None:
        label = f"Rang {rank_id}" if rank_id is not None else None
    # RP without a label remains meaningful; do not invent an RP-to-rank table.
    segment_meta = _object(segment.get("metadata"), "segment.metadata")
    season_label = segment_meta.get("shortName") or segment_meta.get("name") or f"Saison {current}"
    return PlayerStats(
        username=username, platform=expected_platform,
        season=_text(season_label, "season", 64), kd=_kd(full, segment),
        kd_scope="Ranked · aktuelle Saison", rank_name=label, rank_points=points,
        rank_icon_url=icon, updated_at=_time(profile.get("update_time")),
        matches=(), matches_status="unavailable",
    )


class ArenyzeProvider:
    def __init__(self, session: aiohttp.ClientSession, config: Config):
        self.config = config
        self.transport = HTTPProvider(session, config)

    async def fetch(self, platform: str, username: str) -> PlayerStats:
        platform, username = normalize_query(platform, username)
        upstream_platform, family = PLATFORM_MAPPING[platform]
        query = urlencode({"nameOnPlatform": username, "platformType": upstream_platform, "platform_families": family})
        payload = await self.transport.request_json(
            f"{ENDPOINT}?{query}",
            {"Accept": "application/json", "User-Agent": "blockedfps-stats/1.0", "api-key": self.config.arenyze_key},
        )
        try:
            return parse_arenyze(payload, expected_platform=platform)
        except DataError:
            raise ProviderError(
                "R6-Daten unvollständig",
                "Arenyze liefert keine eindeutig zuordenbaren aktuellen Ranked-Daten. "
                "Bitte später erneut versuchen; bei dauerhaften Fehlern den API-Adapter prüfen.",
            ) from None
