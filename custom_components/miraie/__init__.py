"""The mirAIe integration."""
from __future__ import annotations

import asyncio

from aiohttp import ClientError
from miraie_ac import MirAIeHub

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import DOMAIN
from .connection import AuthenticationRejected, ReliableBroker, ReliableHub

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.SWITCH, Platform.SENSOR]


async def _async_close_hub(hub: MirAIeHub) -> None:
    """Stop the library's MQTT tasks before closing their HTTP session."""
    tasks = list(hub.background_tasks)
    for task in tasks:
        task.cancel()
    try:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if not hub.http.closed:
            await hub.http.close()

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up mirAIe from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    hub = ReliableHub()
    def request_reauth():
        if not hub.auth_required:
            hub.auth_required = True
            entry.async_start_reauth(hass)

    try:
        broker = ReliableBroker(
            request_reauth, hub.get_all_device_status, hub.notify_connection_changed
        )
        await hub.init(entry.data["username"], entry.data["password"], broker)
    except AuthenticationRejected as err:
        await _async_close_hub(hub)
        raise ConfigEntryAuthFailed("MirAIe credentials were rejected") from err
    except (ClientError, asyncio.TimeoutError) as err:
        await _async_close_hub(hub)
        raise ConfigEntryNotReady("Unable to connect to MirAIe") from err
    except BaseException:
        # Cancellation also needs to release a partially initialized hub.
        await _async_close_hub(hub)
        raise

    try:
        hass.data[DOMAIN][entry.entry_id] = hub
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        hub.start_reconciliation(request_reauth)
    except BaseException:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await _async_close_hub(hub)
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hub = hass.data[DOMAIN].pop(entry.entry_id)
        await _async_close_hub(hub)

    return unload_ok
