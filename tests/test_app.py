import json
import unittest
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import discord
from discord import app_commands
from discord.http import handle_message_parameters

from bot import installation_links
from statsbot.app import StatsClient, use_private_response
from statsbot.config import Config
from statsbot.providers import DemoProvider, ProviderError


def interaction(*, guild=True, user_install=True, guild_install=False, external_allowed=True):
    obj = MagicMock()
    obj.id = 123456789012345678
    obj.guild_id = 123 if guild else None
    obj.is_user_integration.return_value = user_install
    obj.is_guild_integration.return_value = guild_install
    obj.app_permissions = discord.Permissions(use_external_apps=external_allowed)
    obj.response.defer = AsyncMock()
    obj.response.send_message = AsyncMock()
    obj.response.is_done.return_value = True
    obj.edit_original_response = AsyncMock()
    return obj


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = StatsClient(Config(data_mode="demo"))
        self.command = self.client.tree.get_command("stats")

    async def asyncTearDown(self):
        await self.client.close()

    async def test_global_command_choices_and_all_install_contexts(self):
        payload = self.command.to_dict(self.client.tree)
        self.assertEqual(payload["integration_types"], [0, 1])
        self.assertEqual(payload["contexts"], [0, 1, 2])
        platform, username = payload["options"]
        self.assertEqual(platform["choices"], [
            {"name": "PlayStation", "value": "playstation"},
            {"name": "Xbox", "value": "xbox"},
            {"name": "PC", "value": "pc"},
        ])
        self.assertTrue(platform["required"])
        self.assertEqual((username["min_length"], username["max_length"]), (1, 64))

    async def test_defer_before_fetch_and_real_v2_message_encoding(self):
        target = interaction()
        async def fetch(platform, username):
            target.response.defer.assert_awaited_once()
            return await DemoProvider().fetch(platform, username)
        self.client.service = MagicMock(get=AsyncMock(side_effect=fetch), close=AsyncMock())
        await self.command.callback(target, app_commands.Choice(name="PC", value="pc"), "Test.Player")
        target.response.defer.assert_awaited_once_with(thinking=True, ephemeral=False)
        sent = target.edit_original_response.call_args.kwargs
        self.assertIsNone(sent["content"])
        self.assertEqual(sent["embeds"], [])
        self.assertEqual(sent["allowed_mentions"].to_dict()["parse"], [])
        self.assertEqual(sent["attachments"][0].filename, "rank-placeholder.png")
        with handle_message_parameters(view=sent["view"], content=None, embeds=[], attachments=[]) as params:
            self.assertEqual(params.payload["flags"] & (1 << 15), 1 << 15)
            self.assertIn("blockedfps | Stats", json.dumps(params.payload))

    async def test_provider_error_replaces_deferred_response_with_v2(self):
        target = interaction()
        self.client.service = MagicMock(
            get=AsyncMock(side_effect=ProviderError("Nicht gefunden", "Prüfe den Namen.")),
            close=AsyncMock(),
        )
        await self.command.callback(target, app_commands.Choice(name="Xbox", value="xbox"), "Test")
        payload = target.edit_original_response.call_args.kwargs
        self.assertEqual(payload["attachments"], [])
        self.assertIn("Nicht gefunden", json.dumps(payload["view"].to_components()))

    async def test_external_app_permission_and_private_contexts(self):
        self.assertTrue(use_private_response(interaction(external_allowed=False), False))
        self.assertFalse(use_private_response(interaction(external_allowed=False, guild_install=True), False))
        self.assertFalse(use_private_response(interaction(guild=False, external_allowed=False), False))
        self.assertTrue(use_private_response(interaction(), True))

    async def test_cooldown_gets_private_error_card(self):
        target = interaction()
        target.response.is_done.return_value = False
        error = app_commands.CommandOnCooldown(app_commands.Cooldown(1, 10), 3.2)
        await self.client.tree.on_error(target, error)
        sent = target.response.send_message.call_args.kwargs
        self.assertTrue(sent["ephemeral"])
        self.assertIn("4 Sekunden", json.dumps(sent["view"].to_components()))

    async def test_installation_links_include_required_scopes(self):
        user_link, guild_link = installation_links("123456789012345678").values()
        user = parse_qs(urlsplit(user_link).query)
        guild = parse_qs(urlsplit(guild_link).query)
        self.assertEqual(user["integration_type"], ["1"])
        self.assertEqual(user["scope"], ["applications.commands"])
        self.assertEqual(guild["integration_type"], ["0"])
        self.assertEqual(guild["scope"], ["bot applications.commands"])
        permissions = discord.Permissions(int(guild["permissions"][0]))
        self.assertTrue(permissions.attach_files)
        self.assertFalse(permissions.administrator)
