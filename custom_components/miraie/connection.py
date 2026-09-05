"""Connection adapters for the pinned miraie-ac 1.1.2 API.

Keep device commands and models in the library. These overrides preserve its
login protocol while exposing failures and isolating broken MQTT messages.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import ssl
from urllib.parse import quote
from types import SimpleNamespace

import aiohttp
import aiomqtt
import certifi
from miraie_ac import MirAIeBroker, MirAIeHub
from miraie_ac.constants import httpClientId, loginUrl, statusUrl
from miraie_ac.device import DeviceStatus
from miraie_ac.enums import PowerMode, FanMode, SwingMode, DisplayMode, HVACMode, PresetMode, ConvertiMode
from miraie_ac.user import User
from miraie_ac.utils import is_valid_email

LOGGER = logging.getLogger(__name__)


class AuthenticationRejected(Exception):
    """The login endpoint explicitly rejected credentials."""


class CommandUnavailable(aiomqtt.MqttError):
    """A command could not be sent; it has not been queued for later."""


class CommandClient:
    """Keep the library's command builders, but guard the publish boundary."""

    def __init__(self, broker):
        self.broker = broker

    async def publish(self, *args, **kwargs):
        transport = self.broker.transport
        if not self.broker.connected or transport is None:
            raise CommandUnavailable("MirAIe is disconnected. Try again after reconnecting.")
        try:
            await transport.publish(*args, **kwargs)
        except (aiomqtt.MqttError, asyncio.TimeoutError) as err:
            raise CommandUnavailable("MirAIe could not send the command. Check its state before retrying.") from err


class ReliableHub(MirAIeHub):
    """Retain HTTP status information instead of hiding login errors."""

    async def _authenticate(self, username, password):
        account_field = "email" if is_valid_email(username) else "mobile"
        payload = {"clientId": httpClientId, "scope": "an_14214235325",
                   account_field: username, "password": password}
        async with self.http.post(
            loginUrl, json=payload, timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            if response.status in (401, 403):
                raise AuthenticationRejected("MirAIe rejected credentials")
            response.raise_for_status()
            data = await response.json()
        self.user = User(
            access_token=data["accessToken"], refresh_token=data["refreshToken"],
            user_id=data["userId"], expires_in=data["expiresIn"],
        )
        self.username, self.password = username, password
        return True

    async def get_token(self):
        await self._authenticate(self.username, self.password)
        return self.user.access_token

    async def get_all_device_status(self):
        """Refresh each AC independently, including initial offline devices."""
        for device in self.home.devices:
            if not hasattr(device, "status"):
                device.set_status(DeviceStatus(
                    is_online=False, temperature=24.0, room_temperature=24.0,
                    power_mode=PowerMode.OFF, fan_mode=FanMode.AUTO,
                    v_swing_mode=SwingMode.AUTO, h_swing_mode=SwingMode.AUTO,
                    display_mode=DisplayMode.ON, hvac_mode=HVACMode.AUTO,
                    preset_mode=PresetMode.NONE, converti_mode=ConvertiMode.OFF,
                ))
            try:
                url = statusUrl.replace("{deviceId}", quote(device.id, safe=""))
                async with self.http.get(
                    url, headers={"Authorization": f"Bearer {self.user.access_token}"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Invalid status")
                if str(payload.get("onlineStatus", "false")).lower() != "true":
                    device.status.is_online = False
                    device.refresh()
                    continue
                # Parse into a temporary holder first: no partial state writes.
                holder = SimpleNamespace(status=device.status, refresh=lambda: None)
                holder.set_status = lambda value: setattr(holder, "status", value)
                type(device).status_handler(holder, {"acec": "off", **payload})
                holder.status.is_online = True
                device.set_status(holder.status)
                device.refresh()
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError):
                LOGGER.debug("MirAIe status refresh failed for one AC")


class ReliableBroker(MirAIeBroker):
    """One reconnect loop with bounded backoff and message isolation."""

    def __init__(self, on_auth_failure, refresh_status=None):
        super().__init__()
        self.transport = None
        self.client = CommandClient(self)
        self.connected = False
        self._on_auth_failure = on_auth_failure
        self._refresh_status = refresh_status

    async def on_connect(self):
        for topic in self.commandTopics:
            await self.transport.subscribe(topic)

    def on_message(self, message):
        callback = self.status_callbacks.get(message.topic.value)
        if callback is None:
            return
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Expected object")
            callback(payload)
        except Exception:
            # Payloads, topics and exception text can contain identifiers.
            LOGGER.debug("Ignored an invalid MirAIe MQTT update")

    async def connect(self, username, access_token, get_token):
        context = ssl.create_default_context(cafile=certifi.where()) if self.use_ssl else None
        password = access_token
        delay = 5
        renew = False
        while True:
            try:
                if renew:
                    password = await get_token()
                async with aiomqtt.Client(
                    hostname=self.host, port=self.port, username=username,
                    password=password, tls_context=context,
                ) as client:
                    self.transport = client
                    await self.on_connect()
                    self.connected = True
                    started = asyncio.get_running_loop().time()
                    LOGGER.debug("MirAIe MQTT connected")
                    try:
                        if self._refresh_status is not None:
                            await self._refresh_status()
                        async for message in client.messages:
                            self.on_message(message)
                    finally:
                        if asyncio.get_running_loop().time() - started >= 60:
                            delay = 5
            except AuthenticationRejected:
                self._on_auth_failure()
                return
            except (aiomqtt.MqttError, aiohttp.ClientError, asyncio.TimeoutError):
                LOGGER.debug("MirAIe connection interrupted; retrying")
            finally:
                self.connected = False
                self.transport = None
            # Also back off if the message stream ends without an exception.
            await asyncio.sleep(random.uniform(delay / 2, delay))
            delay = min(delay * 2, 120)
            renew = True
