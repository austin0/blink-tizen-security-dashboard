"""Arm/disarm API and last-motion lookup, using fake Blink objects only."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from backend import app as bridge_app
from backend import blink_service


class FakeSync:
    def __init__(self, network_id: int, armed: bool, result=None) -> None:
        self.network_id = network_id
        self.network_info = {"network": {"armed": armed}}
        self.result = {"id": 1} if result is None else result
        self.calls: list[bool] = []

    @property
    def arm(self) -> bool:
        return self.network_info["network"]["armed"]

    async def async_arm(self, value: bool):
        self.calls.append(value)
        return self.result


class FakeBlink:
    def __init__(self, sync: dict) -> None:
        self.sync = sync
        self.cameras = {}


class SystemsApiTests(unittest.IsolatedAsyncioTestCase):
    token = ""

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {"BLINK_DATA_DIR": self.temp.name, "BRIDGE_TOKEN": self.token},
            clear=False,
        )
        self.env.start()
        bridge_app.BRIDGE = None
        self.client = TestClient(TestServer(bridge_app.create_app()))
        await self.client.start_server()
        self.sync = FakeSync(10, armed=False)
        manager = bridge_app.BRIDGE.manager
        manager.blink = FakeBlink({"Home": self.sync, "Duplicate": FakeSync(10, armed=False)})
        manager.state = "ready"

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.env.stop()
        self.temp.cleanup()

    async def post_arm(self, payload, name: str = "Home"):
        return await self.client.post(f"/api/systems/{name}/arm", json=payload)

    async def test_lists_each_network_once(self) -> None:
        response = await self.client.get("/api/systems")
        data = await response.json()
        self.assertEqual(data["systems"], [{"name": "Home", "armed": False}])

    async def test_arm_updates_reported_state(self) -> None:
        response = await self.post_arm({"armed": True})
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertEqual(self.sync.calls, [True])
        self.assertEqual(data["systems"][0]["armed"], True)

    async def test_non_boolean_is_rejected(self) -> None:
        response = await self.post_arm({"armed": "false"})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.sync.calls, [])

    async def test_non_json_request_is_rejected(self) -> None:
        response = await self.client.post(
            "/api/systems/Home/arm",
            data='{"armed": false}',
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(response.status, 415)
        self.assertEqual(self.sync.calls, [])

    async def test_unknown_system_is_not_found(self) -> None:
        response = await self.post_arm({"armed": True}, name="Nope")
        self.assertEqual(response.status, 404)

    async def test_failed_command_reports_error_and_keeps_state(self) -> None:
        self.sync.result = {}
        response = await self.post_arm({"armed": True})
        self.assertEqual(response.status, 502)
        self.assertEqual(self.sync.network_info["network"]["armed"], False)


class SystemsTokenTests(SystemsApiTests):
    token = "secret"

    async def test_arm_requires_token(self) -> None:
        response = await self.post_arm({"armed": True})
        self.assertEqual(response.status, 401)
        self.assertEqual(self.sync.calls, [])

    # The inherited tests call the API without a token, so they do not apply.
    test_lists_each_network_once = None
    test_arm_updates_reported_state = None
    test_non_boolean_is_rejected = None
    test_non_json_request_is_rejected = None
    test_unknown_system_is_not_found = None
    test_failed_command_reports_error_and_keeps_state = None


class RecentMotionTests(unittest.IsolatedAsyncioTestCase):
    async def test_keeps_newest_undeleted_clip_per_camera(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            from pathlib import Path

            manager = blink_service.BlinkManager(Path(temp))
            manager.blink = object()
            media = {
                "media": [
                    {"device_name": "Cars", "created_at": "2026-11-08T10:00:00+00:00"},
                    {"device_name": "Cars", "created_at": "2026-11-08T12:00:00+00:00"},
                    {"device_name": "Cars", "created_at": "2026-11-08T13:00:00+00:00", "deleted": True},
                    {"device_name": "Front", "created_at": "2026-11-07T09:00:00+00:00"},
                ]
            }
            with patch.object(
                blink_service.blink_api, "request_videos", AsyncMock(return_value=media)
            ):
                await manager._load_recent_motion()
            self.assertEqual(
                manager._recent_motion,
                {"Cars": "2026-11-08T12:00:00+00:00", "Front": "2026-11-07T09:00:00+00:00"},
            )


if __name__ == "__main__":
    unittest.main()
