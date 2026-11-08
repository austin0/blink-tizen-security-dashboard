"""Per-camera options, using fake Blink responses only."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from backend import app as bridge_app
from backend import blink_service
from backend.camera_settings import (
    SettingsError,
    build_update,
    describe_doorbell,
    describe_standard,
    unconfirmed,
)

DOCUMENT = {
    "enabled": True,
    "illuminator_enable": 2,
    "illuminator_intensity": 7,
    "motion_sensitivity": 6,
    "video_length": 5,
    "alert_interval": 10,
    "video_quality": "best",
    "video_quality_support": ["saver", "standard", "best"],
    "record_audio_enable": True,
    "early_termination": True,
}


class ValidationTests(unittest.TestCase):
    def controls(self) -> dict:
        return {c["id"]: c for c in describe_standard(DOCUMENT)["controls"]}

    def test_describes_values_in_friendly_form(self) -> None:
        controls = self.controls()
        self.assertEqual(controls["night_vision"]["value"], "auto")
        self.assertEqual(controls["ir_intensity"]["value"], 7)
        self.assertEqual(controls["video_quality"]["options"], ["saver", "standard", "best"])

    def test_missing_fields_are_not_offered(self) -> None:
        ids = [c["id"] for c in describe_standard({"illuminator_intensity": 3})["controls"]]
        self.assertEqual(ids, ["ir_intensity"])

    def test_builds_blink_body(self) -> None:
        body, motion = build_update({"night_vision": "off", "ir_intensity": 3}, DOCUMENT)
        self.assertEqual(body, {"illuminator_enable": 0, "illuminator_intensity": 3})
        self.assertIsNone(motion)

    def test_motion_is_separate_from_config_body(self) -> None:
        body, motion = build_update({"motion_enabled": False}, DOCUMENT)
        self.assertEqual(body, {})
        self.assertIs(motion, False)

    def test_rejects_bad_values(self) -> None:
        bad = [
            {"ir_intensity": 11},
            {"ir_intensity": True},
            {"ir_intensity": "5"},
            {"video_length": 7},
            {"night_vision": "dim"},
            {"video_quality": "ultra"},
            {"record_audio": 1},
            {"illuminator_enable": 2},
            {},
            "nope",
        ]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(SettingsError):
                build_update(changes, DOCUMENT)

    def test_reports_values_blink_has_not_applied(self) -> None:
        self.assertEqual(unconfirmed({"illuminator_intensity": 3}, DOCUMENT), ["ir_intensity"])
        self.assertEqual(unconfirmed({"illuminator_intensity": 7}, DOCUMENT), [])

    def test_doorbell_is_read_only(self) -> None:
        described = describe_doorbell({"illuminator_intensity": 1, "led_state": None})
        self.assertFalse(described["editable"])
        self.assertEqual([c["id"] for c in described["controls"]], ["illuminator_intensity"])


class FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status

    async def json(self, content_type=None):
        return {} if self.status == 200 else {"message": "busy"}


class FakeUrls:
    base_url = "https://example.invalid"


class FakeCamera:
    def __init__(self, camera_type: str = "") -> None:
        self.camera_id = "5"
        self.network_id = "9"
        self.name = "Cars"
        self.camera_type = camera_type
        self.product_type = "sedona"
        self.async_arm = AsyncMock(return_value={"id": 1})


class FakeBlink:
    urls = FakeUrls()
    account_id = 3

    def __init__(self, camera: FakeCamera) -> None:
        self.cameras = {"Cars": camera}
        self.sync = {}


class SettingsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ, {"BLINK_DATA_DIR": self.temp.name, "BRIDGE_TOKEN": ""}, clear=False
        )
        self.env.start()
        bridge_app.BRIDGE = None
        self.client = TestClient(TestServer(bridge_app.create_app()))
        await self.client.start_server()
        self.camera = FakeCamera()
        manager = bridge_app.BRIDGE.manager
        manager.blink = FakeBlink(self.camera)
        manager.state = "ready"
        self.document = dict(DOCUMENT)
        self.get = patch.object(
            blink_service.blink_api,
            "request_camera_info",
            AsyncMock(side_effect=lambda *a, **k: {"camera": [dict(self.document)]}),
        )
        self.update = patch.object(
            blink_service.blink_api,
            "request_update_config",
            AsyncMock(side_effect=self.apply_update),
        )
        self.get.start()
        self.update_mock = self.update.start()
        self.sleep = patch.object(blink_service.asyncio, "sleep", AsyncMock())

    async def apply_update(self, blink, network, camera, product_type, data):
        import json

        self.document.update(json.loads(data))
        return FakeResponse()

    async def asyncTearDown(self) -> None:
        patch.stopall()
        await self.client.close()
        self.env.stop()
        self.temp.cleanup()

    async def post(self, changes):
        return await self.client.post("/api/cameras/5/settings", json={"changes": changes})

    async def test_reads_settings(self) -> None:
        response = await self.client.get("/api/cameras/5/settings")
        data = await response.json()
        self.assertTrue(data["editable"])
        self.assertIn("night_vision", [c["id"] for c in data["controls"]])

    async def test_write_uses_classic_route_and_reports_result(self) -> None:
        response = await self.post({"ir_intensity": 3})
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertEqual(self.update_mock.await_args.kwargs["product_type"], "catalina")
        self.assertEqual(data["unconfirmed"], [])
        value = next(c for c in data["controls"] if c["id"] == "ir_intensity")["value"]
        self.assertEqual(value, 3)

    async def test_bad_value_is_never_sent(self) -> None:
        response = await self.post({"ir_intensity": 99})
        self.assertEqual(response.status, 400)
        self.update_mock.assert_not_awaited()

    async def test_blink_rejection_is_reported(self) -> None:
        self.update_mock.side_effect = None
        self.update_mock.return_value = FakeResponse(409)
        response = await self.post({"ir_intensity": 3})
        self.assertEqual(response.status, 502)

    async def test_doorbell_cannot_be_written(self) -> None:
        self.camera.camera_type = "doorbell"
        with patch.object(
            blink_service.blink_api, "http_get", AsyncMock(return_value={"illuminator_intensity": 1})
        ):
            response = await self.post({"ir_intensity": 3})
        self.assertEqual(response.status, 400)
        self.update_mock.assert_not_awaited()

    async def test_motion_toggle_uses_arm_action(self) -> None:
        response = await self.post({"motion_enabled": False})
        self.assertEqual(response.status, 200)
        self.camera.async_arm.assert_awaited_once_with(False)
        self.update_mock.assert_not_awaited()

    async def test_non_json_write_is_rejected(self) -> None:
        response = await self.client.post(
            "/api/cameras/5/settings",
            data='{"changes": {"ir_intensity": 3}}',
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(response.status, 415)


if __name__ == "__main__":
    unittest.main()
