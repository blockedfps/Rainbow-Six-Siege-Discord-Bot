from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

import discord

from statsbot.models import Match, PlayerStats
from statsbot.views import FOOTER, PLACEHOLDER_URL, build_error_view, build_stats_view


NOW = datetime(2026, 1, 2, 15, 4, tzinfo=timezone.utc)


def profile(**changes) -> PlayerStats:
    base = PlayerStats(
        username="Testspieler",
        platform="pc",
        season="Test-Saison",
        kd=1.25,
        kd_scope="Ranked · aktuelle Saison",
        rank_name="Gold II",
        rank_points=2700,
        rank_icon_url="https://example.com/rank.png",
        updated_at=NOW,
        matches=tuple(
            Match(str(i), NOW - timedelta(hours=i), "Clubhaus", "Ranked", "win", 8, 4, 2, "4:2")
            for i in range(5)
        ),
    )
    return replace(base, **changes)


def content(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)
    )


class ViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_serialized_components_v2_contract(self):
        view = build_stats_view(profile())
        serialized = view.to_components()
        self.assertIsInstance(view, discord.ui.LayoutView)
        self.assertIsNone(view.timeout)
        self.assertEqual(len(serialized), 1)
        self.assertEqual(serialized[0]["type"], 17)
        self.assertEqual(serialized[0]["accent_color"], 0)
        header = serialized[0]["components"][0]
        self.assertEqual(header["type"], 9)
        self.assertEqual(header["accessory"]["type"], 11)
        self.assertEqual(header["accessory"]["media"]["url"], "https://example.com/rank.png")
        self.assertEqual(sum(c["type"] == 14 for c in serialized[0]["components"]), 3)
        text = content(view)
        self.assertIn("1.25", text)
        self.assertIn("Gold II", text)
        self.assertIn("2700 RP", text)
        self.assertEqual(text.count("· Sieg**"), 5)
        self.assertIn("8 K / 4 D / 2 A", text)
        self.assertIn("<t:1767366240:f>", text)
        self.assertTrue(text.endswith(FOOTER))
        self.assertLessEqual(view.content_length(), 4000)
        self.assertLessEqual(len(list(view.walk_children())), 40)

    async def test_partial_history_missing_rank_and_cache(self):
        view = build_stats_view(profile(
            rank_name=None, rank_points=None, rank_icon_url=None, kd=None,
            matches=profile().matches[:2], cached=True,
        ))
        text = content(view)
        self.assertIn("Nicht verfügbar", text)
        self.assertNotIn("Unranked", text)
        self.assertIn("— RP", text)
        self.assertIn("Nur 2 von 5 Matches verfügbar", text)
        self.assertIn("Aus dem Cache", text)
        self.assertEqual(view.to_components()[0]["components"][0]["accessory"]["media"]["url"], PLACEHOLDER_URL)

    async def test_history_states_are_distinct(self):
        for state, expected in (
            ("private", "ist privat"),
            ("unavailable", "stellt keine Match-Historie bereit"),
            ("available", "Keine Matches in der verfügbaren Historie"),
        ):
            with self.subTest(state=state):
                text = content(build_stats_view(profile(matches_status=state, matches=())))
                self.assertIn(expected, text)

    async def test_demo_and_latest_five(self):
        extra = replace(profile().matches[0], id="new", played_at=NOW + timedelta(hours=1), result="draw")
        view = build_stats_view(profile(matches=profile().matches + (extra,), demo=True))
        text = content(view)
        self.assertTrue(text.startswith("**DEMO · Beispieldaten"))
        self.assertIn("**01 · Unentschieden**", text)
        self.assertEqual(text.count("· Sieg**"), 4)

    async def test_hostile_text_stays_in_budget_and_cannot_ping(self):
        hostile = "@everyone <@123456789> **bold**\n## forged " * 1000
        matches = tuple(replace(m, map_name=hostile, mode=hostile, score=hostile) for m in profile().matches)
        view = build_stats_view(profile(
            username=hostile, season=hostile, kd_scope=hostile,
            rank_name=hostile, matches=matches,
        ))
        text = content(view)
        self.assertNotIn("@everyone", text)
        self.assertNotIn("<@123456789>", text)
        self.assertNotIn("\n## forged", text)
        self.assertNotIn("**bold**", text)
        self.assertLessEqual(view.content_length(), 4000)
        self.assertLessEqual(len(list(view.walk_children())), 40)

    async def test_error_uses_black_components_v2_and_escaped_text(self):
        view = build_error_view("Fehler @everyone", "**Provider** <@1234> " * 1000)
        serialized = view.to_components()
        self.assertEqual(serialized[0]["accent_color"], 0)
        self.assertEqual(serialized[0]["type"], 17)
        self.assertTrue(content(view).endswith(FOOTER))
        self.assertNotIn("@everyone", content(view))
        self.assertLessEqual(view.content_length(), 4000)

    async def test_zero_deaths_remains_mathematically_honest(self):
        match = replace(profile().matches[0], kills=8, deaths=0, assists=None)
        text = content(build_stats_view(profile(matches=(match,))))
        self.assertIn("K/D ∞", text)
        self.assertIn("8 K / 0 D / — A", text)


if __name__ == "__main__":
    unittest.main()
