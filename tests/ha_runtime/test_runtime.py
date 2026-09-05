"""Run with Home Assistant 2026.9.0 installed; only Panasonic IO is mocked."""
import asyncio
from datetime import datetime
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import loader
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, ConfigEntries, ConfigEntryState
from homeassistant.setup import async_setup_component
from homeassistant.helpers import area_registry, device_registry, entity_registry, floor_registry, label_registry
from miraie_ac import Device

from custom_components.miraie.connection import ReliableHub, ReliableBroker
from custom_components.miraie.sensor import (
    MirAIeDailyEnergySensor, MirAIeWeeklyEnergySensor, MirAIeMonthlyEnergySensor,
)


async def fake_init(hub, username, password, broker):
    hub._broker = broker
    hub.username, hub.password = username, password
    hub.user = SimpleNamespace(access_token="test")
    broker.connected = True
    devices = [Device(str(i), f"ac{i}", f"AC {i}", f"{i}/control",
                      f"{i}/status", f"{i}/online", broker) for i in range(2)]
    for device in devices:
        device.details = SimpleNamespace(brand="Panasonic", model_number="test", firmware_version="test")
    hub.home = SimpleNamespace(devices=devices)
    with patch.object(hub, "_status_payload", new_callable=AsyncMock) as status:
        status.return_value = {"onlineStatus": "true", "actmp": "24", "rmtmp": "28",
                               "ps": "on", "acfs": "auto", "acvs": 0, "achs": 0,
                               "acdc": "on", "acmd": "cool", "acpm": "off", "acem": "off"}
        await hub.get_all_device_status()


@pytest.mark.asyncio
async def test_real_platform_setup_reload_and_unload(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config.skip_pip = True
    loader.async_setup(hass)
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    device_registry.async_setup(hass)
    for registry in (floor_registry, label_registry, area_registry, device_registry, entity_registry):
        await registry.async_load(hass)
    entry = ConfigEntry(
        domain="miraie", title="Test", version=1, minor_version=1,
        data={"username": "test", "password": "test"}, options={},
        source="user", unique_id=None, discovery_keys=MappingProxyType({}), subentries_data=[],
    )
    try:
        with patch.object(ReliableHub, "init", fake_init), \
             patch.object(ReliableHub, "get_energy_consumption", new_callable=AsyncMock, return_value={}):
            await hass.config_entries.async_add(entry)
            assert await async_setup_component(hass, "miraie", {})
            await hass.async_block_till_done()
            assert entry.state is ConfigEntryState.LOADED
            assert len(hass.states.async_all("climate")) == 2
            assert len(hass.states.async_all("switch")) == 2
            assert len(hass.states.async_all("sensor")) == 6
            entity_ids = set(hass.states.async_entity_ids())
            reauth = await hass.config_entries.flow.async_init(
                "miraie", context={"source": "reauth", "entry_id": entry.entry_id}, data=entry.data
            )
            assert reauth["step_id"] == "reauth_confirm"
            with patch.object(ReliableHub, "_authenticate", new_callable=AsyncMock):
                result = await hass.config_entries.flow.async_configure(
                    reauth["flow_id"], {"password": "replacement"}
                )
            assert result["reason"] == "reauth_successful"
            await hass.async_block_till_done()
            assert entry.data["password"] == "replacement"
            assert set(hass.states.async_entity_ids()) == entity_ids
            first = hass.data["miraie"][entry.entry_id]
            assert not first.http.closed
            first.broker._set_connected(False)
            await hass.async_block_till_done()
            assert all(state.state == "unavailable" for state in hass.states.async_all("climate"))
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert first.http.closed
            assert not first.background_tasks
            second = hass.data["miraie"][entry.entry_id]
            assert second is not first
            assert len(hass.states.async_all("climate")) == 2
            assert await hass.config_entries.async_unload(entry.entry_id)
            assert second.http.closed
            assert not second.background_tasks
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            third = hass.data["miraie"][entry.entry_id]
            await hass.async_stop(force=True)
            assert third.http.closed
            assert not third.background_tasks
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_sensor_removal_cancels_in_flight_poll():
    hub = SimpleNamespace(background_tasks=set())
    device = SimpleNamespace(name="test", id="test")
    sensor = MirAIeDailyEnergySensor(hub, device)
    started = asyncio.Event()

    async def update():
        started.set()
        await asyncio.Event().wait()
    with patch.object(sensor, "async_update", side_effect=update), \
         patch.object(sensor, "async_write_ha_state") as write:
        task = asyncio.create_task(sensor._async_poll())
        await started.wait()
        await sensor.async_will_remove_from_hass()
        assert task.cancelled()
        assert not sensor._poll_tasks
        write.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("cls,now,key,reset", [
    (MirAIeDailyEnergySensor, datetime(2026, 9, 1, 13), "31082026", datetime(2026, 8, 31)),
    (MirAIeMonthlyEnergySensor, datetime(2026, 9, 1, 13), "082026", datetime(2026, 8, 1)),
    (MirAIeMonthlyEnergySensor, datetime(2026, 9, 2, 13), "092026", datetime(2026, 9, 1)),
    (MirAIeWeeklyEnergySensor, datetime(2026, 9, 6, 13), "30082026", datetime(2026, 8, 30)),
])
async def test_energy_reset_matches_requested_period(cls, now, key, reset):
    class Clock(datetime):
        @classmethod
        def today(cls):
            return now

        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)
    hub = SimpleNamespace(get_energy_consumption=AsyncMock(return_value={key: 2.5}))
    sensor = cls(hub, SimpleNamespace(name="test", id="test"))
    with patch("custom_components.miraie.sensor.datetime", Clock), \
         patch("custom_components.miraie.utils.datetime", Clock):
        await sensor.async_update()
        assert sensor.native_value == 2.5
        assert sensor.last_reset == reset.astimezone()
        previous_reset = sensor.last_reset
        hub.get_energy_consumption.return_value = {}
        await sensor.async_update()
        assert not sensor.available
        assert sensor.native_value == 2.5
        assert sensor.last_reset == previous_reset
