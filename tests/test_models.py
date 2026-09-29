from __future__ import annotations

from datetime import datetime, timezone
import unittest

from statsbot.models import DataError, https_url, parse_stats


def payload() -> dict:
    return {
        "schema_version": 1,
        "updated_at": "2026-09-23T16:10:00+02:00",
        "profile": {
            "username": "  Example.Player  ", "platform": "pc", "season": "Test-Saison",
            "kd": 1.42, "kd_scope": "Ranked · aktuelle Saison",
            "rank": {"name": "Gold II", "points": 2700, "icon_url": None},
        },
        "matches_status": "available",
        "matches": [{
            "id": "newest", "played_at": "2026-09-23T14:00:00Z", "map": "Clubhaus",
            "mode": "Ranked", "result": "win", "kills": 8, "deaths": 4, "assists": 2,
        }],
    }


class ModelTests(unittest.TestCase):
    def parse(self, data):
        return parse_stats(data, expected_platform="pc")

    def test_normalized_profile_and_utc_timestamp(self):
        stats = self.parse(payload())
        self.assertEqual(stats.username, "Example.Player")
        self.assertEqual(stats.updated_at, datetime(2026, 9, 23, 14, 10, tzinfo=timezone.utc))
        self.assertEqual(stats.kd, 1.42)
        self.assertEqual(stats.rank_name, "Gold II")
        self.assertEqual(stats.matches[0].kd, "2.00")

    def test_newest_five_distinct_matches_even_when_provider_is_unsorted(self):
        data = payload()
        prototype = data["matches"][0]
        data["matches"] = [dict(prototype, id=str(i), played_at=f"2026-09-23T0{i}:00:00Z") for i in range(8)]
        data["matches"].insert(2, dict(prototype, id="7", played_at="2026-09-23T08:00:00Z", kills=20))
        stats = self.parse(data)
        self.assertEqual([m.id for m in stats.matches], ["7", "6", "5", "4", "3"])
        self.assertEqual(stats.matches[0].kills, 20)

    def test_missing_rank_numbers_and_zero_deaths_are_not_invented(self):
        data = payload()
        data["profile"]["rank"] = None
        data["profile"]["kd"] = None
        data["matches"][0].update(kills=None, deaths=0, assists=None)
        stats = self.parse(data)
        self.assertIsNone(stats.rank_name)
        self.assertIsNone(stats.rank_points)
        self.assertIsNone(stats.kd)
        self.assertEqual(stats.matches[0].kd, "—")
        data["matches"][0]["kills"] = 8
        self.assertEqual(self.parse(data).matches[0].kd, "∞")
        data["matches"][0]["kills"] = 0
        self.assertEqual(self.parse(data).matches[0].kd, "—")
        data["profile"]["rank"] = {"points": 2700}
        with self.assertRaises(DataError):
            self.parse(data)

    def test_unavailable_private_and_empty_history_remain_distinct(self):
        for status in ("available", "private", "unavailable"):
            with self.subTest(status=status):
                data = payload()
                data.update(matches_status=status, matches=[])
                stats = self.parse(data)
                self.assertEqual(stats.matches_status, status)
                self.assertEqual(stats.matches, ())
                if status != "available":
                    data["matches"] = payload()["matches"]
                    with self.assertRaises(DataError):
                        self.parse(data)

    def test_invalid_numbers_are_rejected_as_data_errors(self):
        for bad in (True, "1.5", -1, float("inf"), float("nan"), 10**500):
            with self.subTest(bad_type=type(bad).__name__):
                data = payload()
                data["profile"]["kd"] = bad
                with self.assertRaises(DataError):
                    self.parse(data)
        data = payload()
        data["matches"][0]["kills"] = 1.5
        with self.assertRaises(DataError):
            self.parse(data)

    def test_malformed_nested_types_always_raise_data_error(self):
        cases = [None, [], {}, dict(payload(), profile=[]), dict(payload(), matches={})]
        for bad in ([], {}, 1):
            data = payload()
            data["matches_status"] = bad
            cases.append(data)
            data = payload()
            data["matches"][0]["result"] = bad
            cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(DataError):
                self.parse(data)
        data = payload()
        data["profile"]["platform"] = "xbox"
        with self.assertRaises(DataError):
            self.parse(data)

    def test_schema_version_is_integer_one_not_boolean_or_float(self):
        for version in (True, False, 1.0, "1", 2, None):
            data = payload()
            data["schema_version"] = version
            with self.subTest(version=version), self.assertRaises(DataError):
                self.parse(data)

    def test_invalid_or_timezone_naive_dates_are_rejected(self):
        for date in (
            "yesterday", "2026-09-23T14:00:00", None, 123,
            "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:00-01:00",
        ):
            data = payload()
            data["updated_at"] = date
            with self.subTest(date=date), self.assertRaises(DataError):
                self.parse(data)

    def test_rank_icon_requires_safe_https_and_defaults_to_placeholder(self):
        for url in ("http://example.com/rank.png", "javascript:bad", "https://user:secret@example.com/a", "https://example.com/a\n", None):
            with self.subTest(url=url):
                self.assertIsNone(https_url(url))
                data = payload()
                data["profile"]["rank"]["icon_url"] = url
                self.assertIsNone(self.parse(data).rank_icon_url)
        self.assertEqual(https_url("https://example.com/rank.png"), "https://example.com/rank.png")


if __name__ == "__main__":
    unittest.main()
