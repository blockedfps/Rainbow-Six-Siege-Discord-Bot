"""Explicit data contract for a future authorized R6 data provider.

This is our own normalized format, NOT a claimed Tracker.gg R6 API schema.
Only the provider adapter needs changing when a real R6 API is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any
from urllib.parse import urlsplit


PLATFORMS = {"pc": "PC · Ubisoft", "playstation": "PlayStation", "xbox": "Xbox"}


class DataError(ValueError):
    """The provider response does not satisfy the documented contract."""


def https_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 500:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not any(c.isspace() or ord(c) < 32 for c in value)
        ):
            return value
    except ValueError:
        pass
    return None


def _text(value: Any, field: str, max_length: int = 80) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise DataError(f"Ungültiges Textfeld: {field}")
    return " ".join(value.split())


def _number(value: Any, field: str, *, integer: bool = False) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataError(f"Ungültige Zahl: {field}")
    if value < 0 or value > 1_000_000_000 or not math.isfinite(value):
        raise DataError(f"Zahl außerhalb des Wertebereichs: {field}")
    if integer:
        if int(value) != value:
            raise DataError(f"Ganzzahl erwartet: {field}")
        return int(value)
    return float(value)


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise DataError(f"ISO-8601-Zeitstempel erwartet: {field}")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataError(f"Ungültiger Zeitstempel: {field}") from exc
    if result.tzinfo is None:
        raise DataError(f"Zeitzone fehlt: {field}")
    try:
        result = result.astimezone(timezone.utc)
        if not 1970 <= result.year <= 2100:
            raise ValueError
        return result
    except (ValueError, OverflowError) as exc:
        raise DataError(f"Zeitstempel außerhalb des Wertebereichs: {field}") from exc


@dataclass(frozen=True, slots=True)
class Match:
    id: str
    played_at: datetime
    map_name: str
    mode: str
    result: str
    kills: int | None
    deaths: int | None
    assists: int | None = None
    score: str | None = None

    @property
    def kd(self) -> str:
        if self.kills is None or self.deaths is None:
            return "—"
        if self.deaths == 0:
            return "∞" if self.kills else "—"
        return f"{self.kills / self.deaths:.2f}"


@dataclass(frozen=True, slots=True)
class PlayerStats:
    username: str
    platform: str
    season: str
    kd: float | None
    kd_scope: str
    rank_name: str | None
    rank_points: int | None
    rank_icon_url: str | None
    updated_at: datetime | None
    matches: tuple[Match, ...]
    matches_status: str = "available"
    demo: bool = False
    cached: bool = False


def parse_stats(payload: Any, *, expected_platform: str) -> PlayerStats:
    """Validate, de-duplicate and sort matches before choosing the newest five.

    Missing values stay missing. In particular, no rank is guessed from RP and
    missing history is never silently converted to five fabricated matches.
    """
    if (
        not isinstance(payload, dict)
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
    ):
        raise DataError("schema_version muss 1 sein.")
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        raise DataError("profile fehlt.")
    platform = profile.get("platform")
    if not isinstance(platform, str) or platform != expected_platform or platform not in PLATFORMS:
        raise DataError("Die Antwort gehört zu einer anderen Plattform.")
    rank = profile.get("rank")
    if rank is not None and not isinstance(rank, dict):
        raise DataError("profile.rank muss ein Objekt oder null sein.")
    rank = rank or {}
    rank_name = rank.get("name")
    if rank_name is not None:
        rank_name = _text(rank_name, "rank.name", 60)
    rank_points = _number(rank.get("points"), "rank.points", integer=True)
    if rank_name is None and (rank_points is not None or rank.get("icon_url") is not None):
        raise DataError("Rangpunkte oder Rangbild ohne Rangname.")
    status = payload.get("matches_status")
    if not isinstance(status, str) or status not in {"available", "unavailable", "private"}:
        raise DataError("matches_status fehlt oder ist ungültig.")
    raw_matches = payload.get("matches")
    if not isinstance(raw_matches, list) or len(raw_matches) > 100:
        raise DataError("matches muss eine Liste mit höchstens 100 Einträgen sein.")
    if status != "available" and raw_matches:
        raise DataError("Nicht verfügbare Match-Historie enthält Einträge.")
    matches: list[Match] = []
    for raw in raw_matches:
        if not isinstance(raw, dict):
            raise DataError("Ungültiger Match-Eintrag.")
        result = raw.get("result", "unknown")
        if not isinstance(result, str) or result not in {"win", "loss", "draw", "unknown"}:
            raise DataError("Ungültiges Match-Ergebnis.")
        score = raw.get("score")
        if score is not None:
            score = _text(score, "match.score", 16)
        matches.append(Match(
            id=_text(raw.get("id"), "match.id", 128),
            played_at=_timestamp(raw.get("played_at"), "match.played_at"),
            map_name=_text(raw.get("map", "Unbekannte Karte"), "match.map", 48),
            mode=_text(raw.get("mode", "Unbekannter Modus"), "match.mode", 40),
            result=result,
            kills=_number(raw.get("kills"), "match.kills", integer=True),
            deaths=_number(raw.get("deaths"), "match.deaths", integer=True),
            assists=_number(raw.get("assists"), "match.assists", integer=True),
            score=score,
        ))
    matches.sort(key=lambda m: m.played_at, reverse=True)
    distinct: dict[str, Match] = {}
    for match in matches:
        distinct.setdefault(match.id, match)
    return PlayerStats(
        username=_text(profile.get("username"), "profile.username", 64),
        platform=platform,
        season=_text(profile.get("season"), "profile.season", 64),
        kd=_number(profile.get("kd"), "profile.kd"),
        kd_scope=_text(profile.get("kd_scope"), "profile.kd_scope", 64),
        rank_name=rank_name,
        rank_points=rank_points,
        rank_icon_url=https_url(rank.get("icon_url")),
        updated_at=_timestamp(payload.get("updated_at"), "updated_at"),
        matches=tuple(list(distinct.values())[:5]),
        matches_status=status,
    )
