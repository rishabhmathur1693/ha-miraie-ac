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

import aiohttp
import aiomqtt
import certifi
from miraie_ac import MirAIeBroker, MirAIeHub
from miraie_ac.constants import httpClientId, loginUrl
from miraie_ac.user import User
from miraie_ac.utils import is_valid_email

LOGGER = logging.getLogger(__name__)


class AuthenticationRejected(Exception):
    """The login endpoint explicitly rejected credentials."""


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


class ReliableBroker(MirAIeBroker):
    """One reconnect loop with bounded backoff and message isolation."""

    def __init__(self, on_auth_failure):
        super().__init__()
        self.client = None
        self.connected = False
        self._on_auth_failure = on_auth_failure

    async def on_connect(self):
        for topic in self.commandTopics:
            await self.client.subscribe(topic)

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
                    self.client = client
                    await self.on_connect()
                    self.connected = True
                    started = asyncio.get_running_loop().time()
                    LOGGER.debug("MirAIe MQTT connected")
                    try:
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
                self.client = None
            # Also back off if the message stream ends without an exception.
            await asyncio.sleep(random.uniform(delay / 2, delay))
            delay = min(delay * 2, 120)
            renew = True
