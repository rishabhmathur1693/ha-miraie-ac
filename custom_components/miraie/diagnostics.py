"""Allowlisted connection diagnostics; never export raw cloud responses."""
from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    hub = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if hub is None:
        return {"loaded": False}
    broker = hub.broker
    return {
        "loaded": True,
        "mqtt_connected": bool(broker.connected),
        "mqtt_connections": int(broker.reconnects),
        "invalid_mqtt_messages": int(broker.invalid_messages),
        "status_refreshes": int(hub.status_refreshes),
        "status_errors": int(hub.status_errors),
        "reauthentication_required": bool(hub.auth_required),
        "device_count": len(hub.home.devices),
        "reported_online_devices": sum(bool(d.status.is_online) for d in hub.home.devices),
        "background_tasks": len(hub.background_tasks),
        "http_session_closed": bool(hub.http.closed),
    }
