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
    async def test_reconnect_refreshes_status_and_clears_transport(self):
        refresh = AsyncMock()
        broker = connection.ReliableBroker(Mock(), refresh)
        broker.set_topics(["test"])
        transport = AsyncMock()
        transport.messages.__aiter__.return_value = []
        client = AsyncMock()
        client.__aenter__.return_value = transport
        sleeps = 0

        async def sleep(delay):
            nonlocal sleeps
            sleeps += 1
            if sleeps == 2:
                raise asyncio.CancelledError()
        with patch.object(connection.aiomqtt, "Client", return_value=client), \
             patch.object(connection.asyncio, "sleep", side_effect=sleep):
            with self.assertRaises(asyncio.CancelledError):
                await broker.connect("test", "test", AsyncMock(return_value="renewed"))
        self.assertEqual(refresh.await_count, 2)
        self.assertEqual(transport.subscribe.await_count, 2)
        self.assertFalse(broker.connected)
        self.assertIsNone(broker.transport)

    async def test_offline_commands_are_not_queued(self):
        broker = connection.ReliableBroker(Mock())
        with self.assertRaises(connection.CommandUnavailable):
            await broker.set_temperature("test", 24)
        broker.transport = AsyncMock()
        broker.connected = True
        await broker.set_temperature("test", 25)
        broker.transport.publish.assert_awaited_once()
        payload = broker.transport.publish.call_args.args[1]
        self.assertIn('"25"', payload)

    async def test_publish_failure_is_actionable(self):
        broker = connection.ReliableBroker(Mock())
        broker.connected = True
        broker.transport = AsyncMock()
        broker.transport.publish.side_effect = aiomqtt.MqttError("network failure")
        with self.assertRaises(connection.CommandUnavailable):
            await broker.set_temperature("test", 24)

    async def test_rest_failure_does_not_block_second_ac(self):
        from miraie_ac import Device
        broker = connection.ReliableBroker(Mock())
        devices = [Device(str(i), "test", "test", str(i), str(i), str(i), broker)
                   for i in range(2)]
        payload = {"onlineStatus": "true", "actmp": "25", "rmtmp": "28",
                   "ps": "on", "acfs": "auto", "acvs": 0, "achs": 0,
                   "acdc": "on", "acmd": "cool", "acpm": "off", "acem": "off"}
        failed = AsyncMock()
        failed.__aenter__.side_effect = aiohttp.ClientConnectionError()
        success = AsyncMock()
        response = Mock()
        response.json = AsyncMock(return_value=payload)
        success.__aenter__.return_value = response
        async with connection.ReliableHub() as hub:
            hub.home = SimpleNamespace(devices=devices)
            hub.user = SimpleNamespace(access_token="test")
            with patch.object(hub.http, "get", side_effect=[failed, success]):
                await hub.get_all_device_status()
        self.assertFalse(devices[0].status.is_online)
        self.assertTrue(devices[1].status.is_online)
        self.assertEqual(devices[1].status.temperature, 25)

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
        self.assertIsNone(broker.transport)

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
        self.assertIsNone(broker.transport)

    async def test_subscription_does_not_print_device_topics(self):
        broker = connection.ReliableBroker(Mock())
        broker.transport = AsyncMock()
        broker.set_topics(["one", "two"])
        with patch("builtins.print") as output:
            await broker.on_connect()
        self.assertEqual(broker.transport.subscribe.await_count, 2)
        output.assert_not_called()
