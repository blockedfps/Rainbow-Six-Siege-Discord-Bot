from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import aiohttp

from statsbot.arenyze import ArenyzeProvider, parse_arenyze
from statsbot.config import Config
from statsbot.models import DataError
from statsbot.providers import ProviderError


def fixture() -> dict:
    path = Path(__file__).resolve().parents[1] / "examples" / "arenyze-response.json"
    return json.loads(path.read_text(encoding="utf-8"))


def board(payload: dict) -> dict:
    return payload["stats"]["platform_families_full_profiles"][0]["board_ids_full_profiles"][0]["full_profiles"][0]


class ArenyzeParserTests(unittest.TestCase):
    def test_current_board_beats_historical_and_peak_rank_points(self):
        payload = fixture()
        historical = copy.deepcopy(board(payload))
        historical["season_id"] = 42
        historical["profile"]["rank_points"] = 6000
        historical["season_statistics"] = {"kills": 9999, "deaths": 1}
        payload["stats"]["platform_families_full_profiles"][0]["board_ids_full_profiles"][0]["full_profiles"].insert(0, historical)
        stats = parse_arenyze(payload, expected_platform="pc")
        self.assertEqual(stats.rank_points, 3300)
        self.assertEqual(stats.rank_name, "PLATINUM II")
        self.assertEqual(stats.kd, 1.5)
        self.assertEqual(stats.season, "Y11S3")
        self.assertEqual(stats.updated_at.isoformat(), "2026-09-23T12:00:00+00:00")
        self.assertEqual(stats.matches, ())
        self.assertEqual(stats.matches_status, "unavailable")

    def test_history_mismatch_never_replaces_current_points_or_uses_old_matching_point(self):
        payload = fixture()
        history = payload["history"]["data"]["history"]["data"]
        history.append(["2026-09-23T13:00:00Z", {"value": 3500, "metadata": {"rank": "EMERALD V", "imageUrl": "https://example.com/new.png"}}])
        stats = parse_arenyze(payload, expected_platform="pc")
        self.assertEqual(stats.rank_points, 3300)
        self.assertEqual(stats.rank_name, "Rang 18")
        self.assertIsNone(stats.rank_icon_url)

    def test_latest_history_selected_by_timestamp_not_array_order_or_highest_points(self):
        payload = fixture()
        payload["history"]["data"]["history"]["data"].reverse()
        self.assertEqual(parse_arenyze(payload, expected_platform="pc").rank_name, "PLATINUM II")

    def test_correct_console_family_and_current_season_are_selected(self):
        payload = fixture()
        payload["player"].update(platformType="psn", platformFamilies="console")
        console = copy.deepcopy(payload["stats"]["platform_families_full_profiles"][0])
        console["platform_family"] = "console"
        console["board_ids_full_profiles"][0]["full_profiles"][0]["profile"]["rank_points"] = 1800
        console["board_ids_full_profiles"][0]["full_profiles"][0]["season_statistics"] = {"kills": 60, "deaths": 100}
        payload["stats"]["platform_families_full_profiles"].append(console)
        stats = parse_arenyze(payload, expected_platform="playstation")
        self.assertEqual(stats.rank_points, 1800)
        self.assertEqual(stats.kd, 0.6)
        self.assertEqual(stats.season, "Saison 43")

    def test_documented_single_unlabelled_family_requires_response_confirmation(self):
        payload = fixture()
        del payload["stats"]["platform_families_full_profiles"][0]["platform_family"]
        self.assertEqual(parse_arenyze(payload, expected_platform="pc").rank_points, 3300)
        del payload["player"]["platformFamilies"]
        stats = parse_arenyze(payload, expected_platform="pc")
        self.assertIsNone(stats.rank_points)
        self.assertIsNone(stats.rank_name)
        self.assertEqual(stats.kd, 1.5)

    def test_segment_only_uses_current_family_ranked_kd_but_never_peak_as_current_rank(self):
        payload = fixture()
        payload["stats"] = {}
        wrong = copy.deepcopy(payload["seasons"]["data"]["segments"][-1])
        wrong["attributes"]["platform"] = "console"
        wrong["stats"]["kdRatio"]["value"] = 7.5
        payload["seasons"]["data"]["segments"].insert(0, wrong)
        stats = parse_arenyze(payload, expected_platform="pc")
        self.assertEqual(stats.kd, 1.5)
        self.assertIsNone(stats.rank_points)
        self.assertIsNone(stats.rank_name)
        self.assertIsNone(stats.rank_icon_url)
        self.assertIsNone(stats.updated_at)

    def test_missing_current_season_never_uses_old_or_highest_available_season(self):
        payload = fixture()
        payload["seasons"]["data"]["metadata"] = {}
        with self.assertRaises(DataError):
            parse_arenyze(payload, expected_platform="pc")
        payload["seasons"]["data"]["metadata"] = {"currentSeason": 44}
        with self.assertRaises(DataError):
            parse_arenyze(payload, expected_platform="pc")

    def test_unknown_timestamp_stays_unknown_and_zero_deaths_stays_missing(self):
        payload = fixture()
        board(payload)["profile"]["update_time"] = "not-a-date"
        board(payload)["season_statistics"]["deaths"] = 0
        stats = parse_arenyze(payload, expected_platform="pc")
        self.assertIsNone(stats.updated_at)
        self.assertIsNone(stats.kd)

    def test_partial_optional_failure_drops_history_without_losing_valid_core(self):
        payload = fixture()
        payload["meta"].update(partial=True, errors={"history": "failed upstream body containing SECRET"})
        stats = parse_arenyze(payload, expected_platform="pc")
        self.assertEqual(stats.kd, 1.5)
        self.assertEqual(stats.rank_points, 3300)
        self.assertEqual(stats.rank_name, "Rang 18")
        self.assertIsNone(stats.rank_icon_url)

    def test_failed_core_datasets_not_used_even_if_stale_data_remains(self):
        payload = fixture()
        payload["meta"].update(partial=True, errors={"stats": "failed"})
        stats = parse_arenyze(payload, expected_platform="pc")
        self.assertIsNone(stats.rank_points)
        self.assertEqual(stats.kd, 1.5)
        payload["meta"]["errors"]["seasons"] = "failed"
        with self.assertRaises(DataError):
            parse_arenyze(payload, expected_platform="pc")

    def test_response_wrapper_and_unexpected_matches_do_not_fabricate_history(self):
        payload = fixture()
        payload["matches"] = [{"id": "not-a-documented-match-source"}]
        stats = parse_arenyze({"data": payload}, expected_platform="pc")
        self.assertEqual(stats.matches, ())
        self.assertEqual(stats.matches_status, "unavailable")

    def test_ambiguous_segments_not_resolved_by_highest_points(self):
        payload = fixture()
        payload["stats"] = {}
        duplicate = copy.deepcopy(payload["seasons"]["data"]["segments"][-1])
        duplicate["stats"]["rankPoints"]["value"] = 7000
        duplicate["stats"]["kdRatio"]["value"] = 8
        payload["seasons"]["data"]["segments"].append(duplicate)
        with self.assertRaises(DataError):
            parse_arenyze(payload, expected_platform="pc")

    def test_invalid_shapes_platform_numbers_are_rejected(self):
        malformed = [None, [], {}, {"success": False, "message": "SECRET"}]
        for path, value in (("platformType", "xbl"), ("platformFamilies", "console")):
            payload = fixture()
            payload["player"][path] = value
            malformed.append(payload)
        payload = fixture()
        board(payload)["season_statistics"]["kills"] = -1
        malformed.append(payload)
        payload = fixture()
        payload["meta"]["partial"] = "yes"
        malformed.append(payload)
        payload = fixture()
        payload["stats"]["platform_families_full_profiles"] = "unexpected"
        malformed.append(payload)
        for payload in malformed:
            with self.subTest(payload=str(payload)[:100]), self.assertRaises(DataError):
                parse_arenyze(payload, expected_platform="pc")


class ArenyzeProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_platform_mapping_encoding_and_key_header(self):
        for platform, upstream, family in (("pc", "uplay", "pc"), ("playstation", "psn", "console"), ("xbox", "xbl", "console")):
            with self.subTest(platform=platform):
                payload = fixture()
                payload["player"].update(platformType=upstream, platformFamilies=family)
                payload["stats"]["platform_families_full_profiles"][0]["platform_family"] = family
                provider = ArenyzeProvider(MagicMock(spec=aiohttp.ClientSession), Config(arenyze_key="TEST_ONLY_SECRET"))
                provider.transport.request_json = AsyncMock(return_value=payload)
                await provider.fetch(platform, " Name /?# ü ")
                url, headers = provider.transport.request_json.call_args.args
                self.assertEqual(urlsplit(url).netloc, "public-api.arenyze.com")
                self.assertEqual(urlsplit(url).path, "/r6/api/v2/profile")
                self.assertEqual(parse_qs(urlsplit(url).query), {"nameOnPlatform": ["Name /?# ü"], "platformType": [upstream], "platform_families": [family]})
                self.assertEqual(headers["api-key"], "TEST_ONLY_SECRET")
                self.assertNotIn("TEST_ONLY_SECRET", url)
                self.assertEqual(provider.transport.request_json.await_count, 1)

    async def test_malformed_payload_is_safe_provider_error(self):
        provider = ArenyzeProvider(MagicMock(spec=aiohttp.ClientSession), Config(arenyze_key="TEST_ONLY_SECRET"))
        provider.transport.request_json = AsyncMock(return_value={"error": "SECRET upstream detail"})
        with self.assertRaises(ProviderError) as raised:
            await provider.fetch("pc", "Player")
        self.assertEqual(raised.exception.title, "R6-Daten unvollständig")
        self.assertNotIn("SECRET", str(raised.exception))

    async def test_invalid_query_does_not_make_network_request(self):
        provider = ArenyzeProvider(MagicMock(spec=aiohttp.ClientSession), Config(arenyze_key="TEST_ONLY_SECRET"))
        provider.transport.request_json = AsyncMock()
        with self.assertRaises(ProviderError):
            await provider.fetch("nintendo", "Player")
        provider.transport.request_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
