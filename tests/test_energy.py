"""Energy validation tests with Home Assistant imports substituted."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp


def load_sensor():
    names = ("homeassistant", "homeassistant.components",
             "homeassistant.components.sensor", "homeassistant.config_entries",
             "homeassistant.const", "homeassistant.core", "homeassistant.helpers",
             "homeassistant.helpers.device_registry", "homeassistant.helpers.entity_platform",
             "homeassistant.helpers.event", "energy_test", "energy_test.const",
             "energy_test.logger", "energy_test.utils")
    modules = {name: ModuleType(name) for name in names}
    for module in modules.values():
        module.__getattr__ = lambda name: Mock()
    modules["homeassistant.components.sensor"].SensorEntity = type("SensorEntity", (), {})
    modules["energy_test.const"].DOMAIN = "miraie"
    spec = importlib.util.spec_from_file_location(
        "energy_test.sensor",
        Path(__file__).resolve().parents[1] / "custom_components/miraie/sensor.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


sensor_module = load_sensor()


class EnergyTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_readings_preserve_value_and_reset_timestamp(self):
        for value in (float("nan"), float("inf"), -1, True, "bad"):
            sensor = SimpleNamespace(
                get_energy_consumption=AsyncMock(return_value=value),
                _set_last_reset_time=AsyncMock(), _attr_native_value=2.5)
            await sensor_module.MirAIeEnergySensor.async_update(sensor)
            self.assertFalse(sensor._attr_available)
            self.assertEqual(sensor._attr_native_value, 2.5)
            sensor._set_last_reset_time.assert_not_awaited()

    async def test_request_failure_then_recovery(self):
        sensor = SimpleNamespace(
            get_energy_consumption=AsyncMock(side_effect=[aiohttp.ClientConnectionError(), 3.5]),
            _set_last_reset_time=AsyncMock(), _attr_native_value=2.5)
        await sensor_module.MirAIeEnergySensor.async_update(sensor)
        self.assertFalse(sensor._attr_available)
        await sensor_module.MirAIeEnergySensor.async_update(sensor)
        self.assertTrue(sensor._attr_available)
        self.assertEqual(sensor._attr_native_value, 3.5)
        sensor._set_last_reset_time.assert_awaited_once()
