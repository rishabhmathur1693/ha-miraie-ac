"""Exercise flow logic with a minimal Home Assistant boundary double."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_connection import connection


class FlowBase:
    def __init_subclass__(cls, **kwargs):
        pass


def load_flow():
    modules = {name: ModuleType(name) for name in (
        "homeassistant", "homeassistant.config_entries", "homeassistant.core",
        "homeassistant.data_entry_flow", "homeassistant.exceptions", "voluptuous",
        "flow_under_test", "flow_under_test.const")}
    modules["homeassistant.config_entries"].ConfigFlow = FlowBase
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.data_entry_flow"].FlowResult = dict
    modules["homeassistant.exceptions"].HomeAssistantError = Exception
    modules["voluptuous"].Schema = lambda value: value
    modules["voluptuous"].Required = lambda value: value
    modules["flow_under_test.const"].DOMAIN = "miraie"
    modules["flow_under_test.connection"] = connection
    spec = importlib.util.spec_from_file_location(
        "flow_under_test.config_flow",
        Path(__file__).resolve().parents[1] / "custom_components/miraie/config_flow.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


flow_module = load_flow()


class ReauthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flow = flow_module.ConfigFlow()
        self.flow.hass = object()
        self.entry = SimpleNamespace(data={"username": "existing", "password": "old"})
        self.flow._get_reauth_entry = Mock(return_value=self.entry)
        self.flow.async_show_form = Mock(side_effect=lambda **kwargs: kwargs)
        self.flow.async_update_reload_and_abort = Mock(return_value="updated")

    async def test_success_updates_existing_entry_without_changing_account(self):
        with patch.object(flow_module, "validate_input", new_callable=AsyncMock) as validate:
            result = await self.flow.async_step_reauth_confirm({"password": "new"})
        self.assertEqual(result, "updated")
        expected = {"username": "existing", "password": "new"}
        validate.assert_awaited_once_with(self.flow.hass, expected)
        self.flow.async_update_reload_and_abort.assert_called_once_with(
            self.entry, data_updates=expected)
        self.assertEqual(self.entry.data["password"], "old")

    async def test_failures_keep_old_credentials_and_show_specific_error(self):
        for error, key in ((flow_module.InvalidAuth, "invalid_auth"),
                           (flow_module.CannotConnect, "cannot_connect"),
                           (ValueError, "unknown")):
            with patch.object(flow_module, "validate_input", side_effect=error):
                result = await self.flow.async_step_reauth_confirm({"password": "new"})
            self.assertEqual(result["errors"], {"base": key})
            self.flow.async_update_reload_and_abort.assert_not_called()
            self.assertEqual(self.entry.data["password"], "old")

    async def test_reauth_prompts_without_exposing_old_password(self):
        result = await self.flow.async_step_reauth(self.entry.data)
        self.assertEqual(result["step_id"], "reauth_confirm")
        self.assertEqual(result["data_schema"], {"password": str})
