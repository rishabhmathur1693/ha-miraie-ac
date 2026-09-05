"""Verify transport errors become Home Assistant action errors."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from test_connection import connection


class ActionError(Exception):
    pass


spec = importlib.util.spec_from_file_location(
    "command_test.commands",
    Path(__file__).resolve().parents[1] / "custom_components/miraie/commands.py")
commands = importlib.util.module_from_spec(spec)
exceptions = ModuleType("homeassistant.exceptions")
exceptions.HomeAssistantError = ActionError
with patch.dict(sys.modules, {
    "homeassistant": ModuleType("homeassistant"),
    "homeassistant.exceptions": exceptions,
    "command_test": ModuleType("command_test"),
    "command_test.connection": connection,
}):
    spec.loader.exec_module(commands)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_translation(self):
        @commands.handle_command_errors
        async def action():
            raise connection.CommandUnavailable("Disconnected")
        with self.assertRaisesRegex(ActionError, "Disconnected"):
            await action()

    async def test_cancellation_is_not_converted(self):
        @commands.handle_command_errors
        async def action():
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await action()
