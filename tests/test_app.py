"""HTTP surface of the bridge, without touching Blink's servers."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from backend import app as bridge_app


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "BLINK_DATA_DIR": self.temp.name,
                "BRIDGE_TOKEN": "",
            },
            clear=False,
        )
        self.env.start()
        bridge_app.BRIDGE = None
        self.server = TestServer(bridge_app.create_app())
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.env.stop()
        self.temp.cleanup()

    async def test_health_is_public(self) -> None:
        response = await self.client.get("/api/health")
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["blink"]["ready"])
        self.assertEqual(data["bridge"]["lan_only"], True)

    async def test_health_does_not_expose_stream_session_ids(self) -> None:
        bridge = bridge_app.BRIDGE
        bridge.hls._sessions["secret-hls-session"] = object()
        bridge.ts._sessions["secret-ts-session"] = object()
        try:
            response = await self.client.get("/api/health")
            body = await response.text()
            data = await response.json()
        finally:
            bridge.hls._sessions.clear()
            bridge.ts._sessions.clear()
        self.assertNotIn("secret-hls-session", body)
        self.assertNotIn("secret-ts-session", body)
        self.assertEqual(len(data["live"]), 2)

    async def test_setup_page_and_embedded_app_are_served(self) -> None:
        setup = await self.client.get("/setup")
        self.assertEqual(setup.status, 200)
        self.assertIn("Link your Blink account", await setup.text())
        app_page = await self.client.get("/app/")
        self.assertEqual(app_page.status, 200)
        self.assertIn("BLINK / LIVE", await app_page.text())

    async def test_cameras_requires_account(self) -> None:
        response = await self.client.get("/api/cameras")
        self.assertEqual(response.status, 409)
        data = await response.json()
        self.assertFalse(data["ok"])

    async def test_blank_login_is_rejected_without_network(self) -> None:
        response = await self.client.post(
            "/api/auth/start", json={"email": "", "password": ""}
        )
        self.assertEqual(response.status, 400)
        data = await response.json()
        self.assertEqual(data["status"], "error")

    async def test_foreign_websocket_origin_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            await self.client.ws_connect(
                "/api/live/1", headers={"Origin": "https://evil.example"}
            )


if __name__ == "__main__":
    unittest.main()
