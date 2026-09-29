"""Run the bot, inspect its command offline, or print installation links."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from urllib.parse import urlencode

import discord

from statsbot.app import StatsClient
from statsbot.config import Config, ConfigError, ROOT
from statsbot.providers import DemoProvider
from statsbot.views import build_error_view, build_stats_view


def installation_links(application_id: str) -> dict[str, str]:
    if not application_id.isascii() or not application_id.isdigit() or not 15 <= len(application_id) <= 22:
        raise ConfigError("APP_ID muss die numerische Application ID aus dem Discord Developer Portal sein.")
    server_permissions = discord.Permissions(
        view_channel=True, send_messages=True, embed_links=True,
        attach_files=True, send_messages_in_threads=True,
    )
    base = "https://discord.com/oauth2/authorize?"
    return {
        "Zum Konto hinzufügen (Server, DMs, Gruppenchats)": base + urlencode({
            "client_id": application_id, "scope": "applications.commands", "integration_type": 1,
        }),
        "Zum Server hinzufügen": base + urlencode({
            "client_id": application_id, "scope": "bot applications.commands",
            "integration_type": 0, "permissions": server_permissions.value,
        }),
    }


async def check() -> None:
    from statsbot.arenyze import parse_arenyze

    config = Config.load(require_token=False)
    async with StatsClient(config) as client:
        command = client.tree.get_command("stats")
        data = command.to_dict(client.tree)
        assert data["contexts"] == [0, 1, 2]
        assert data["integration_types"] == [0, 1]
        assert [o["name"] for o in data["options"]] == ["platform", "username"]
        assert data["options"][0]["choices"] == [
            {"name": "PlayStation", "value": "playstation"},
            {"name": "Xbox", "value": "xbox"},
            {"name": "PC", "value": "pc"},
        ]
        result = await DemoProvider().fetch("pc", "Test")
        fixture = json.loads((ROOT / "examples" / "arenyze-response.json").read_text(encoding="utf-8"))
        arenyze = parse_arenyze(fixture, expected_platform="pc")
        assert arenyze.kd == 1.5 and arenyze.rank_points == 3300
        assert arenyze.matches_status == "unavailable" and not arenyze.matches
        for view in (build_stats_view(result), build_error_view("Test", "Fehlerhinweis")):
            payload = view.to_components()
            assert payload[0]["type"] == 17 and payload[0]["accent_color"] == 0
            assert view.content_length() <= 4000 and view.total_children_count <= 40
            assert view.has_components_v2()
        assert (ROOT / "assets" / "rank-placeholder.png").read_bytes().startswith(b"\x89PNG")
    print("OK: /stats, PlayStation/Xbox/PC, alle Kontexte, V2-Layouts und Arenyze-Beispielantwort.")
    print("Offline geprüft. Live-Daten und Discord-Zugang wurden nicht getestet.")
    print(f"Konfigurierter Datenmodus: {config.data_mode}")


def main() -> int:
    parser = argparse.ArgumentParser(description="blockedfps | Stats — R6 Discord-Bot")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Offline-Prüfung ohne Token und ohne API-Anfragen")
    group.add_argument("--links", metavar="APP_ID", help="Discord-Einladungslinks ausgeben")
    args = parser.parse_args()
    try:
        if args.links:
            for title, url in installation_links(args.links).items():
                print(f"{title}\n{url}\n")
            return 0
        if args.check:
            asyncio.run(check())
            return 0
        config = Config.load()
        logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
        logging.getLogger("discord").setLevel(logging.WARNING)
        if config.data_mode == "demo":
            logging.info("DEMO-Modus: ausschließlich erfundene Beispieldaten; keine R6-Live-Verbindung.")
        client = StatsClient(config)
        client.run(config.discord_token, log_handler=None)
        return 0
    except ConfigError as exc:
        print(f"Konfiguration: {exc}")
        return 2
    except discord.LoginFailure:
        print("Discord hat den Bot-Token abgelehnt. Prüfe DISCORD_TOKEN in .env.")
        return 2
    except discord.HTTPException as exc:
        print(f"Discord-Einrichtung fehlgeschlagen (HTTP {exc.status}). Prüfe docs/DISCORD_SETUP.md.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
