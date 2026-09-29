"""Demo source and an HTTP adapter for the explicitly documented JSON contract.

There is deliberately no fabricated public Tracker R6 endpoint here.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import replace
import json
import time
from urllib.parse import quote

import aiohttp

from .config import Config, ROOT
from .models import DataError, PLATFORMS, PlayerStats, parse_stats


class ProviderError(Exception):
    """An actionable, safe message; upstream bodies and secrets are never echoed."""

    def __init__(self, title: str, message: str):
        super().__init__(message)
        self.title = title
        self.message = message


def normalize_query(platform: str, username: str) -> tuple[str, str]:
    username = username.strip()
    if platform not in PLATFORMS:
        raise ProviderError("Plattform ungültig", "Wähle PlayStation, Xbox oder PC.")
    if not 1 <= len(username) <= 64 or any(ord(c) < 32 or ord(c) == 127 for c in username):
        raise ProviderError("Name ungültig", "Gib einen Spielernamen mit 1 bis 64 Zeichen ein.")
    return platform, username


class DemoProvider:
    async def fetch(self, platform: str, username: str) -> PlayerStats:
        platform, _ = normalize_query(platform, username)
        payload = json.loads((ROOT / "examples" / "stats-response.json").read_text(encoding="utf-8"))
        payload["profile"]["platform"] = platform
        # Keep the demo name: never label invented stats as the queried player.
        return replace(parse_stats(payload, expected_platform=platform), demo=True)


class DisabledProvider:
    async def fetch(self, platform: str, username: str) -> PlayerStats:
        raise ProviderError(
            "R6-Datenquelle fehlt",
            "Ein normaler Tracker.gg-Key unterstützt Rainbow Six Siege nicht. "
            "Der Betreiber muss eine R6-Datenquelle einrichten. "
            "Zum Layout-Test ist DATA_MODE=demo verfügbar.",
        )


class HTTPProvider:
    """Fetch one normalized profile+history response over HTTPS.

    The provider must map its actual API to docs/API_SCHEMA.md. This class alone
    cannot make an unsupported upstream API supply R6 data.
    """

    MAX_BODY_BYTES = 1_000_000

    def __init__(self, session: aiohttp.ClientSession, config: Config):
        self.session = session
        self.config = config
        self._calls: deque[float] = deque()
        self._rate_lock = asyncio.Lock()
        self._backoff_until = 0.0

    async def _reserve_request(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            while self._calls and self._calls[0] <= now - 60:
                self._calls.popleft()
            if now < self._backoff_until:
                raise ProviderError("API-Pause", "Die Datenquelle begrenzt Anfragen. Bitte später erneut versuchen.")
            if len(self._calls) >= self.config.requests_per_minute:
                raise ProviderError("Viele Anfragen", "Das API-Limit wurde erreicht. Bitte in einer Minute erneut versuchen.")
            self._calls.append(now)

    async def fetch(self, platform: str, username: str) -> PlayerStats:
        platform, username = normalize_query(platform, username)
        url = self.config.api_url.format(
            platform=quote(platform, safe=""), username=quote(username, safe=""),
        )
        headers = {"Accept": "application/json", "User-Agent": "blockedfps-stats/1.0"}
        if self.config.api_key:
            headers[self.config.api_key_header] = self.config.api_key_prefix + self.config.api_key
        payload = await self.request_json(url, headers)
        try:
            return parse_stats(payload, expected_platform=platform)
        except DataError:
            raise ProviderError(
                "API-Format passt nicht",
                "Die Antwort entspricht nicht dem vereinbarten Datenformat. "
                "Der Betreiber muss den Adapter anhand von docs/API_SCHEMA.md anpassen.",
            ) from None

    async def request_json(self, url: str, headers: dict[str, str]) -> object:
        """Shared bounded transport for the normalized and provider-specific adapters."""
        await self._reserve_request()
        try:
            async with self.session.get(
                url, headers=headers, allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=self.config.api_timeout),
            ) as response:
                status = response.status
                if status in {401, 403}:
                    raise ProviderError(
                        "API-Zugriff abgelehnt",
                        "Die Datenquelle verweigert den Zugriff. Der Betreiber muss API-Key "
                        "und die Freischaltung beim gewählten Anbieter prüfen.",
                    )
                if status == 404:
                    raise ProviderError("Profil nicht gefunden", "Prüfe Spielername und Plattform. Das Profil kann auch privat sein.")
                if status == 429:
                    try:
                        retry = max(1, min(300, int(response.headers.get("Retry-After", "60"))))
                    except (ValueError, TypeError):
                        retry = 60
                    self._backoff_until = time.monotonic() + retry
                    raise ProviderError("API-Limit erreicht", f"Bitte in ungefähr {retry} Sekunden erneut versuchen.")
                if status != 200:
                    raise ProviderError("Datenquelle nicht erreichbar", "Die API antwortet momentan nicht wie erwartet. Bitte später erneut versuchen.")
                if response.content_type != "application/json" and not response.content_type.endswith("+json"):
                    raise ProviderError("Ungültige API-Antwort", "Die Datenquelle hat kein JSON geliefert. Bitte die API-Konfiguration prüfen.")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > self.MAX_BODY_BYTES:
                        raise ProviderError("API-Antwort zu groß", "Die Datenquelle liefert zu viele Daten. Bitte den API-Adapter prüfen.")
                return json.loads(body)
        except ProviderError:
            raise
        except (ValueError, UnicodeError, RecursionError):
            raise ProviderError(
                "API-Format passt nicht",
                "Die Antwort entspricht nicht dem vereinbarten Datenformat. "
                "Der Betreiber muss den Adapter anhand von docs/API_SCHEMA.md anpassen.",
            ) from None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise ProviderError("API nicht erreichbar", "Die Anfrage ist fehlgeschlagen oder hat zu lange gedauert. Bitte später erneut versuchen.") from None


class StatsService:
    """Bounded in-memory cache and coalesced requests for the same profile."""

    def __init__(self, provider, *, cache_ttl: int = 60, capacity: int = 256):
        self.provider = provider
        self.cache_ttl = cache_ttl
        self.capacity = capacity
        self._cache: OrderedDict[tuple[str, str], tuple[float, PlayerStats]] = OrderedDict()
        self._inflight: dict[tuple[str, str], asyncio.Task[PlayerStats]] = {}
        self._semaphore = asyncio.Semaphore(4)

    async def _fetch(self, key: tuple[str, str]) -> PlayerStats:
        async with self._semaphore:
            stats = await self.provider.fetch(*key)
        if self.cache_ttl:
            self._cache[key] = (time.monotonic() + self.cache_ttl, stats)
            self._cache.move_to_end(key)
            while len(self._cache) > self.capacity:
                self._cache.popitem(last=False)
        return stats

    def _finished(self, key, task) -> None:
        self._inflight.pop(key, None)
        # Retrieve exceptions even when all waiting callers were cancelled.
        if not task.cancelled():
            task.exception()

    async def get(self, platform: str, username: str) -> PlayerStats:
        key = normalize_query(platform, username)
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            self._cache.move_to_end(key)
            return replace(cached[1], cached=True)
        self._cache.pop(key, None)
        task = self._inflight.get(key)
        if task is None:
            if len(self._inflight) >= 32:
                raise ProviderError("Gerade ausgelastet", "Bitte versuche es in wenigen Sekunden erneut.")
            task = asyncio.create_task(self._fetch(key))
            self._inflight[key] = task
            task.add_done_callback(lambda completed: self._finished(key, completed))
        return await asyncio.shield(task)

    async def close(self) -> None:
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        self._cache.clear()
