"""Isolated lifecycle regression tests; HA/API boundaries are test doubles.

Run: python3 -m unittest discover -s tests -v
These do not replace tests in a real Home Assistant runtime.
"""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


class ConnectionFailure(Exception):
    pass


class NotReady(Exception):
    pass


def load_integration():
    modules = {}
    for name in ("aiohttp", "miraie_ac", "homeassistant",
                 "homeassistant.config_entries", "homeassistant.const",
                 "homeassistant.core", "homeassistant.exceptions"):
        modules[name] = ModuleType(name)
    modules["aiohttp"].ClientError = ConnectionFailure
    modules["miraie_ac"].MirAIeHub = object
    modules["miraie_ac"].MirAIeBroker = object
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.const"].Platform = SimpleNamespace(
        CLIMATE="climate", SWITCH="switch", SENSOR="sensor")
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.exceptions"].ConfigEntryNotReady = NotReady
    modules["homeassistant.exceptions"].ConfigEntryAuthFailed = type("AuthFailed", (Exception,), {})
    connection = ModuleType("lifecycle_under_test.connection")
    connection.AuthenticationRejected = type("Rejected", (Exception,), {})
    connection.ReliableHub = object
    connection.ReliableBroker = lambda *args: object()
    modules[connection.__name__] = connection
    path = Path(__file__).resolve().parents[1] / "custom_components/miraie/__init__.py"
    spec = importlib.util.spec_from_file_location("lifecycle_under_test", path)
    module = importlib.util.module_from_spec(spec)
    modules[spec.name] = module
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


integration = load_integration()


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hub = SimpleNamespace(background_tasks=set(), init=AsyncMock())
        self.hub.get_all_device_status = AsyncMock()
        self.hub.http = SimpleNamespace(closed=False)

        async def close():
            self.hub.http.closed = True
        self.hub.http.close = AsyncMock(side_effect=close)
        self.entry = SimpleNamespace(entry_id="test", data={
            "username": "test", "password": "test"})
        self.hass = SimpleNamespace(data={}, config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True)))
        self.factory = patch.object(integration, "ReliableHub", return_value=self.hub)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    async def test_session_survives_setup_and_failed_unload(self):
        await integration.async_setup_entry(self.hass, self.entry)
        self.hub.http.close.assert_not_awaited()
        self.hass.config_entries.async_unload_platforms.return_value = False
        self.assertFalse(await integration.async_unload_entry(self.hass, self.entry))
        self.assertIs(self.hass.data["miraie"]["test"], self.hub)
        self.hub.http.close.assert_not_awaited()

    async def test_successful_unload_stops_listener_before_close(self):
        stopped = asyncio.Event()

        async def listener():
            try:
                await asyncio.Event().wait()
            finally:
                self.assertFalse(self.hub.http.closed)
                stopped.set()
        task = asyncio.create_task(listener())
        self.hub.background_tasks.add(task)
        task.add_done_callback(self.hub.background_tasks.discard)
        await asyncio.sleep(0)
        await integration.async_setup_entry(self.hass, self.entry)
        self.assertTrue(await integration.async_unload_entry(self.hass, self.entry))
        self.assertTrue(stopped.is_set())
        self.assertTrue(task.cancelled())
        self.hub.http.close.assert_awaited_once()
        self.assertNotIn("test", self.hass.data["miraie"])

    async def test_transient_failure_requests_retry_and_closes_session(self):
        self.hub.init.side_effect = ConnectionFailure()
        with self.assertRaises(NotReady):
            await integration.async_setup_entry(self.hass, self.entry)
        self.hub.http.close.assert_awaited_once()

    async def test_unexpected_failure_preserved_and_cleaned_up(self):
        self.hub.init.side_effect = ValueError("bad response")
        with self.assertRaises(ValueError):
            await integration.async_setup_entry(self.hass, self.entry)
        self.hub.http.close.assert_awaited_once()

    async def test_cancelled_setup_closes_session(self):
        self.hub.init.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await integration.async_setup_entry(self.hass, self.entry)
        self.hub.http.close.assert_awaited_once()

    async def test_platform_failure_removes_runtime(self):
        self.hass.config_entries.async_forward_entry_setups.side_effect = RuntimeError()
        with self.assertRaises(RuntimeError):
            await integration.async_setup_entry(self.hass, self.entry)
        self.assertNotIn("test", self.hass.data["miraie"])
        self.hub.http.close.assert_awaited_once()

    async def test_close_is_idempotent(self):
        await integration._async_close_hub(self.hub)
        await integration._async_close_hub(self.hub)
        self.hub.http.close.assert_awaited_once()
