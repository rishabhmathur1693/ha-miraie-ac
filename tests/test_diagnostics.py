"""Ensure diagnostics expose only explicitly allowed aggregate fields."""
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "diagnostics_test.diagnostics",
    Path(__file__).resolve().parents[1] / "custom_components/miraie/diagnostics.py")
diagnostics = importlib.util.module_from_spec(spec)
constants = ModuleType("diagnostics_test.const")
constants.DOMAIN = "miraie"
with patch.dict(sys.modules, {
    "diagnostics_test": ModuleType("diagnostics_test"),
    "diagnostics_test.const": constants,
}):
    spec.loader.exec_module(diagnostics)


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_secrets_and_device_metadata_are_never_serialized(self):
        secret = "DO-NOT-EXPORT-THIS"
        entry = SimpleNamespace(entry_id=secret, data={"password": secret})
        hub = SimpleNamespace(
            username=secret, password=secret, user=SimpleNamespace(access_token=secret),
            status_refreshes=2, status_errors=1, auth_required=False,
            home=SimpleNamespace(id=secret, devices=[SimpleNamespace(
                id=secret, friendly_name=secret, status=SimpleNamespace(is_online=True))]),
            background_tasks=set(), http=SimpleNamespace(closed=False),
            broker=SimpleNamespace(connected=True, reconnects=1, invalid_messages=0,
                                   revisions={secret: 10}),
        )
        result = await diagnostics.async_get_config_entry_diagnostics(
            SimpleNamespace(data={"miraie": {secret: hub}}), entry)
        self.assertNotIn(secret, json.dumps(result))
        self.assertTrue(all(type(value) in (bool, int) for value in result.values()))
        self.assertEqual(result["device_count"], 1)
        self.assertEqual(result["reported_online_devices"], 1)

    async def test_unloaded_entry(self):
        result = await diagnostics.async_get_config_entry_diagnostics(
            SimpleNamespace(data={}), SimpleNamespace(entry_id="test"))
        self.assertEqual(result, {"loaded": False})
