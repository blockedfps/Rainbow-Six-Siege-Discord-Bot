from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from statsbot.config import Config, ConfigError, validate_api_url
from statsbot.models import parse_stats
from statsbot.providers import DemoProvider, DisabledProvider, HTTPProvider, ProviderError, StatsService


def response_payload() -> dict:
    return {
        "schema_version": 1, "updated_at": "2026-09-23T14:10:00Z",
        "profile": {"username": "Example.Player", "platform": "pc", "season": "Test-Saison", "kd": 1.42,
                    "kd_scope": "Ranked", "rank": {"name": "Gold II", "points": 2700}},
        "matches_status": "available", "matches": [],
    }


class Body:
    def __init__(self, value: bytes):
        self.value = value

    async def iter_chunked(self, size):
        for start in range(0, len(self.value), size):
            yield self.value[start : start + size]


class Response:
    def __init__(self, *, status=200, body=None, content_type="application/json", headers=None):
        self.status = status
        self.content_type = content_type
        self.headers = headers or {}
        self.content = Body(body if body is not None else json.dumps(response_payload()).encode())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def http(response=None, **config_overrides):
    session = MagicMock(spec=aiohttp.ClientSession)
    session.get.return_value = response or Response()
    config = Config(api_url="https://api.example.com/stats/{platform}/{username}", **config_overrides)
    return HTTPProvider(session, config), session


class HTTPProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_encoded_player_name_secret_header_timeout_and_no_redirects(self):
        provider, session = http(api_key="TOP_SECRET", api_key_prefix="Token ", api_key_header="X-API-Key", api_timeout=7)
        await provider.fetch("pc", "Name /?# ü")
        args, options = session.get.call_args
        self.assertEqual(args[0], "https://api.example.com/stats/pc/Name%20%2F%3F%23%20%C3%BC")
        self.assertEqual(options["headers"]["X-API-Key"], "Token TOP_SECRET")
        self.assertFalse(options["allow_redirects"])
        self.assertEqual(options["timeout"].total, 7)
        self.assertNotIn("TOP_SECRET", repr(provider.config))

    async def test_http_statuses_produce_safe_actionable_errors(self):
        for status, title in ((401, "API-Zugriff abgelehnt"), (403, "API-Zugriff abgelehnt"), (404, "Profil nicht gefunden"), (500, "Datenquelle nicht erreichbar"), (503, "Datenquelle nicht erreichbar"), (302, "Datenquelle nicht erreichbar")):
            with self.subTest(status=status):
                provider, session = http(Response(status=status, body=b"TOP_SECRET"), api_key="TOP_SECRET")
                with self.assertRaises(ProviderError) as raised:
                    await provider.fetch("pc", "Player")
                self.assertEqual(raised.exception.title, title)
                self.assertNotIn("TOP_SECRET", str(raised.exception))
                self.assertEqual(session.get.call_count, 1)
                self.assertFalse(session.get.call_args.kwargs["allow_redirects"])

    async def test_rate_limit_backoff_blocks_additional_network_requests(self):
        clock = [100.0]
        with patch("statsbot.providers.time", SimpleNamespace(monotonic=lambda: clock[0])):
            provider, session = http(Response(status=429, headers={"Retry-After": "9999"}))
            with self.assertRaises(ProviderError) as raised:
                await provider.fetch("pc", "Player")
            self.assertIn("300 Sekunden", raised.exception.message)
            with self.assertRaises(ProviderError) as raised:
                await provider.fetch("pc", "Another")
            self.assertEqual(raised.exception.title, "API-Pause")
            self.assertEqual(session.get.call_count, 1)
            clock[0] = 401
            session.get.return_value = Response()
            await provider.fetch("pc", "Player")
            self.assertEqual(session.get.call_count, 2)

    async def test_timeout_and_network_failure_are_safe(self):
        for failure in (asyncio.TimeoutError(), aiohttp.ClientConnectionError("TOP_SECRET")):
            provider, session = http()
            session.get.side_effect = failure
            with self.subTest(failure=type(failure).__name__), self.assertRaises(ProviderError) as raised:
                await provider.fetch("pc", "Player")
            self.assertEqual(raised.exception.title, "API nicht erreichbar")
            self.assertNotIn("TOP_SECRET", raised.exception.message)

    async def test_malformed_json_and_contract_shapes_are_safe(self):
        bad_status = response_payload()
        bad_status["matches_status"] = []
        huge_number = response_payload()
        huge_number["profile"]["kd"] = 10**500
        for body in (
            b"{broken", b"\xff", b"[]", b"null", b"{}",
            json.dumps(bad_status).encode(), json.dumps(huge_number).encode(),
            b"[" * 5000 + b"0" + b"]" * 5000,
        ):
            provider, _ = http(Response(body=body))
            with self.subTest(body=body[:50]):
                with self.assertRaises(ProviderError) as raised:
                    await provider.fetch("pc", "Player")
                self.assertEqual(raised.exception.title, "API-Format passt nicht")

    async def test_response_size_limit_and_content_type(self):
        provider, _ = http(Response(body=b" " * (HTTPProvider.MAX_BODY_BYTES + 1)))
        with self.assertRaises(ProviderError) as raised:
            await provider.fetch("pc", "Player")
        self.assertEqual(raised.exception.title, "API-Antwort zu groß")
        provider, _ = http(Response(content_type="text/html"))
        with self.assertRaises(ProviderError) as raised:
            await provider.fetch("pc", "Player")
        self.assertEqual(raised.exception.title, "Ungültige API-Antwort")
        provider, _ = http(Response(content_type="application/vnd.stats+json"))
        self.assertEqual((await provider.fetch("pc", "Player")).username, "Example.Player")

    async def test_local_rate_limit_resets_after_window(self):
        clock = [100.0]
        with patch("statsbot.providers.time", SimpleNamespace(monotonic=lambda: clock[0])):
            provider, session = http(requests_per_minute=1)
            await provider.fetch("pc", "Player")
            with self.assertRaises(ProviderError) as raised:
                await provider.fetch("pc", "Another")
            self.assertEqual(raised.exception.title, "Viele Anfragen")
            self.assertEqual(session.get.call_count, 1)
            clock[0] = 160
            await provider.fetch("pc", "Player")
            self.assertEqual(session.get.call_count, 2)

    async def test_invalid_query_never_reaches_network(self):
        provider, session = http()
        for platform, username in (("nintendo", "Player"), ("pc", ""), ("pc", "x" * 65), ("pc", "a\nb")):
            with self.subTest(platform=platform, username=username), self.assertRaises(ProviderError):
                await provider.fetch(platform, username)
        session.get.assert_not_called()

    async def test_demo_is_explicit_and_disabled_mode_is_actionable(self):
        stats = await DemoProvider().fetch("playstation", "Real.Player")
        self.assertTrue(stats.demo)
        self.assertNotEqual(stats.username, "Real.Player")
        self.assertEqual(stats.platform, "playstation")
        with self.assertRaises(ProviderError) as raised:
            await DisabledProvider().fetch("pc", "Player")
        self.assertEqual(raised.exception.title, "R6-Datenquelle fehlt")


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_requests_coalesce_and_cache_expires(self):
        stats = parse_stats(response_payload(), expected_platform="pc")
        release = asyncio.Event()
        started = asyncio.Event()
        async def fetch(*args):
            started.set()
            await release.wait()
            return stats
        provider = SimpleNamespace(fetch=AsyncMock(side_effect=fetch))
        service = StatsService(provider, cache_ttl=60)
        clock = [100.0]
        with patch("statsbot.providers.time", SimpleNamespace(monotonic=lambda: clock[0])):
            first = asyncio.create_task(service.get("pc", "Player"))
            await started.wait()
            second = asyncio.create_task(service.get("pc", " Player "))
            await asyncio.sleep(0)
            release.set()
            results = await asyncio.gather(first, second)
            self.assertEqual(provider.fetch.await_count, 1)
            self.assertTrue(all(not item.cached for item in results))
            self.assertTrue((await service.get("pc", "Player")).cached)
            clock[0] = 161
            self.assertFalse((await service.get("pc", "Player")).cached)
            self.assertEqual(provider.fetch.await_count, 2)
        await service.close()

    async def test_cache_capacity_evicts_oldest_profile(self):
        stats = parse_stats(response_payload(), expected_platform="pc")
        provider = SimpleNamespace(fetch=AsyncMock(return_value=stats))
        service = StatsService(provider, capacity=2)
        for username in ("A", "B", "C"):
            await service.get("pc", username)
        self.assertTrue((await service.get("pc", "B")).cached)
        self.assertFalse((await service.get("pc", "A")).cached)
        self.assertEqual(provider.fetch.await_count, 4)
        await service.close()

    async def test_provider_failures_are_not_cached_and_next_call_can_retry(self):
        stats = parse_stats(response_payload(), expected_platform="pc")
        provider = SimpleNamespace(fetch=AsyncMock(side_effect=[ProviderError("Test", "Try later"), stats]))
        service = StatsService(provider)
        with self.assertRaises(ProviderError):
            await service.get("pc", "Player")
        self.assertEqual((await service.get("pc", "Player")).username, "Example.Player")
        self.assertEqual(provider.fetch.await_count, 2)
        await service.close()

    async def test_cancelled_caller_does_not_cancel_shared_fetch_and_shutdown_cleans_up(self):
        started = asyncio.Event()
        release = asyncio.Event()
        async def fetch(*args):
            started.set()
            await release.wait()
            return parse_stats(response_payload(), expected_platform="pc")
        provider = SimpleNamespace(fetch=AsyncMock(side_effect=fetch))
        service = StatsService(provider)
        first = asyncio.create_task(service.get("pc", "Player"))
        await started.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(service.get("pc", "Player"))
        await asyncio.sleep(0)
        self.assertEqual(provider.fetch.await_count, 1)
        await service.close()
        with self.assertRaises(asyncio.CancelledError):
            await second
        self.assertFalse(service._inflight)
        self.assertFalse(service._cache)


class ConfigTests(unittest.TestCase):
    def test_api_url_requires_https_fixed_host_and_explicit_fields(self):
        validate_api_url("https://api.example.com/stats/{platform}/{username}")
        for url in (
            "http://api.example.com/{platform}/{username}",
            "https://{username}.example.com/{platform}",
            "https://user:secret@api.example.com/{platform}/{username}",
            "https://api.example.com/{platform}/{username!r}",
            "https://api.example.com/{platform}",
            "https://api.example.com/{platform}/{username}#fragment",
            "https://api.tracker.gg/{platform}/{username}",
            "https://api.tracker.network/{platform}/{username}",
        ):
            with self.subTest(url=url), self.assertRaises(ConfigError):
                validate_api_url(url)

    def test_environment_limits_headers_and_secrets(self):
        with patch("statsbot.config.load_dotenv"), patch.dict("os.environ", {}, clear=True):
            config = Config.load(require_token=False)
            self.assertEqual(config.data_mode, "arenyze")
            for name, value in (
                ("DATA_MODE", "unsupported"), ("CACHE_TTL_SECONDS", "-1"),
                ("API_TIMEOUT_SECONDS", "0"), ("API_REQUESTS_PER_MINUTE", "601"),
                ("STATS_PRIVATE", "maybe"), ("STATS_API_KEY_HEADER", "Host"),
                ("STATS_API_KEY_HEADER", "bad\nheader"), ("STATS_API_KEY_PREFIX", "Bearer\n"),
            ):
                with self.subTest(name=name), patch.dict("os.environ", {name: value}), self.assertRaises(ConfigError):
                    Config.load(require_token=False)
            with self.assertRaises(ConfigError):
                Config.load()


if __name__ == "__main__":
    unittest.main()
