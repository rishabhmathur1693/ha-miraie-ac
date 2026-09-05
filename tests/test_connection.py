"""Test adapters against miraie-ac 1.1.2 with mocked network boundaries."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import aiomqtt

spec = importlib.util.spec_from_file_location(
    "connection_under_test",
    Path(__file__).resolve().parents[1] / "custom_components/miraie/connection.py",
)
connection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(connection)


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_status_classification_and_release(self):
        for status in (200, 401, 403, 429, 500):
            with self.subTest(status=status):
                async with connection.ReliableHub() as hub:
                    response = Mock(status=status)
                    response.json = AsyncMock(return_value={
                        "accessToken": "test", "refreshToken": "test",
                        "userId": "test", "expiresIn": 3600,
                    })
                    if status >= 400:
                        response.raise_for_status.side_effect = aiohttp.ClientResponseError(
                            None, (), status=status)
                    context = AsyncMock()
                    context.__aenter__.return_value = response
                    with patch.object(hub.http, "post", return_value=context):
                        if status in (401, 403):
                            with self.assertRaises(connection.AuthenticationRejected):
                                await hub._authenticate("user@example.com", "test")
                        elif status >= 400:
                            with self.assertRaises(aiohttp.ClientError):
                                await hub._authenticate("user@example.com", "test")
                        else:
                            await hub._authenticate("user@example.com", "test")
                            self.assertEqual(hub.user.access_token, "test")
                    context.__aexit__.assert_awaited_once()

    async def test_token_failure_is_not_hidden(self):
        async with connection.ReliableHub() as hub:
            hub.username = hub.password = "test"
            with patch.object(hub, "_authenticate", side_effect=connection.AuthenticationRejected):
                with self.assertRaises(connection.AuthenticationRejected):
                    await hub.get_token()

    async def test_bad_messages_do_not_prevent_other_device_updates(self):
        broker = connection.ReliableBroker(Mock())
        valid = Mock()
        broker.register_device_callback("good", valid)
        broker.register_device_callback("broken", Mock(side_effect=KeyError()))
        for topic, payload in (("missing", b"{}"), ("good", b"not json"),
                               ("good", b"[]"), ("good", b"\xff"),
                               ("broken", b"{}"), ("good", b'{"ps":"on"}')):
            broker.on_message(SimpleNamespace(topic=SimpleNamespace(value=topic), payload=payload))
        valid.assert_called_once_with({"ps": "on"})

    async def test_auth_rejection_stops_retry_and_requests_reauth(self):
        reauth = Mock()
        broker = connection.ReliableBroker(reauth)
        client = AsyncMock()
        client.__aenter__.side_effect = aiomqtt.MqttError("offline")
        renew = AsyncMock(side_effect=connection.AuthenticationRejected)
        with patch.object(connection.aiomqtt, "Client", return_value=client), \
             patch.object(connection.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await broker.connect("test", "test", renew)
        sleep.assert_awaited_once()
        reauth.assert_called_once()
        self.assertFalse(broker.connected)
        self.assertIsNone(broker.client)

    async def test_retries_back_off_and_cancellation_propagates(self):
        broker = connection.ReliableBroker(Mock())
        client = AsyncMock()
        client.__aenter__.side_effect = aiomqtt.MqttError("offline")
        delays = []

        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 7:
                raise asyncio.CancelledError()
        with patch.object(connection.aiomqtt, "Client", return_value=client), \
             patch.object(connection.random, "uniform", side_effect=lambda low, high: high), \
             patch.object(connection.asyncio, "sleep", side_effect=sleep):
            with self.assertRaises(asyncio.CancelledError):
                await broker.connect("test", "test", AsyncMock(return_value="new"))
        self.assertEqual(delays, [5, 10, 20, 40, 80, 120, 120])
        self.assertIsNone(broker.client)

    async def test_subscription_does_not_print_device_topics(self):
        broker = connection.ReliableBroker(Mock())
        broker.client = AsyncMock()
        broker.set_topics(["one", "two"])
        with patch("builtins.print") as output:
            await broker.on_connect()
        self.assertEqual(broker.client.subscribe.await_count, 2)
        output.assert_not_called()
