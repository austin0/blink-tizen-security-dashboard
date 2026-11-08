"""Blink account lifecycle and camera metadata for the local bridge."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import aiohttp
from blinkpy import api as blink_api
from blinkpy.auth import Auth, BlinkTwoFARequiredError
from blinkpy.blinkpy import Blink

from .camera_settings import (
    build_update,
    describe_doorbell,
    describe_standard,
    SettingsError,
    standard_document,
    unconfirmed,
)

LOGGER = logging.getLogger(__name__)

REFRESH_SECONDS = 60
RECENT_MOTION_SECONDS = 7 * 24 * 3600
SETTINGS_COMMAND_SECONDS = 15
SETTINGS_READ_BACK_SECONDS = 1.5
MODEL_NAMES = {
    "orchid": "Blink Video Doorbell 2K",
    "sonoran": "Blink Outdoor 4 2K",
    "catalina": "Blink Outdoor 4",
    "hawk": "Blink Outdoor",
    "owl": "Blink Mini",
    "lotus": "Blink Video Doorbell",
    "doorbell": "Blink Video Doorbell",
}


class BlinkManager:
    """Owns the Blink session and converts blinkpy objects to a small API."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.session_file = data_dir / "blink_session.json"
        self.state = "starting"
        self.last_error: str | None = None
        self.blink: Blink | None = None
        self.pending: Blink | None = None
        self.http: aiohttp.ClientSession | None = None
        self._refresh_task: asyncio.Task | None = None
        self._motion_task: asyncio.Task | None = None
        self._recent_motion: dict[str, str] = {}
        self._login_lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()
        self._closed = False

    # ---------------------------------------------------------------- lifecycle
    async def initialize(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=90, connect=20)
        )
        if not self.session_file.is_file():
            self.state = "login_required"
            LOGGER.info("No Blink session yet; setup is required")
            return

        self.state = "starting"
        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.state = "login_required"
            self.last_error = f"Session file could not be read: {exc}"
            LOGGER.warning(self.last_error)
            return

        try:
            blink = self._new_blink(data)
            ok = await asyncio.wait_for(blink.start(), timeout=75)
            if not ok:
                raise RuntimeError("Blink did not complete the saved session.")
            await self._adopt(blink)
            LOGGER.info("Blink session restored")
        except BlinkTwoFARequiredError:
            self.state = "login_required"
            self.last_error = "Blink requires a fresh sign-in code."
            LOGGER.info("Blink session requires 2FA; setup required")
        except asyncio.TimeoutError:
            self.state = "error"
            self.last_error = "Blink took too long to restore the saved session."
            LOGGER.warning(self.last_error)
        except Exception as exc:  # noqa: BLE001 - bad token/network/format
            self.state = "login_required"
            self.last_error = _short_error(exc)
            LOGGER.warning("Blink session could not be restored: %s", exc)

    def _new_blink(self, login_data: dict[str, Any]) -> Blink:
        if self.http is None:
            raise RuntimeError("HTTP session is not ready")
        blink = Blink(session=self.http, refresh_rate=REFRESH_SECONDS)
        blink.auth = Auth(
            login_data,
            no_prompt=True,
            session=self.http,
            callback=self._token_refreshed,
        )
        return blink

    async def _adopt(self, blink: Blink) -> None:
        self.blink = blink
        self.pending = None
        self.state = "ready"
        self.last_error = None
        await self._persist()
        self._start_refresh_loop()
        self._motion_task = asyncio.create_task(
            self._load_recent_motion(), name="blink-recent-motion"
        )

    def _start_refresh_loop(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = asyncio.create_task(
            self._refresh_loop(), name="blink-refresh"
        )

    async def _refresh_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(REFRESH_SECONDS)
                if self.state != "ready" or self.blink is None:
                    continue
                await self.blink.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the last good state
                self.last_error = _short_error(exc)
                LOGGER.warning("Blink refresh failed: %s", exc)
                if "auth" in type(exc).__name__.lower() or "token" in str(exc).lower():
                    self.state = "login_required"

    def _token_refreshed(self) -> None:
        if self._closed:
            return
        with contextlib.suppress(RuntimeError):
            asyncio.create_task(self._persist(), name="blink-persist-token")

    async def _persist(self) -> None:
        blink = self.blink
        if blink is None:
            return
        data = dict(blink.auth.login_attributes)
        # Never write the password or transient 2FA state to disk.  A refresh
        # token is all that is needed after the first successful sign-in.
        data.pop("password", None)
        data.pop("2fa_code", None)
        async with self._persist_lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.session_file.with_suffix(".tmp")
            payload = json.dumps(data, indent=2, sort_keys=True)
            tmp.write_text(payload, encoding="utf-8")
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            os.replace(tmp, self.session_file)

    async def close(self) -> None:
        self._closed = True
        for task in (self._refresh_task, self._motion_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self.http and not self.http.closed:
            await self.http.close()

    async def _load_recent_motion(self) -> None:
        """Find each camera's newest clip, which blinkpy only tracks going forward."""

        try:
            response = await blink_api.request_videos(
                self.blink, time=time.time() - RECENT_MOTION_SECONDS, page=1
            )
            newest: dict[str, str] = {}
            for entry in response.get("media", []):
                name = entry.get("device_name")
                created = entry.get("created_at")
                if name and created and not entry.get("deleted") and created > newest.get(name, ""):
                    newest[name] = created
            self._recent_motion = newest
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - last-motion times are best-effort
            LOGGER.debug("Could not load recent motion: %s", exc)

    # ------------------------------------------------------------------- login
    async def begin_login(self, email: str, password: str) -> dict[str, Any]:
        email = email.strip()
        if not email or not password:
            return {"status": "error", "message": "Email and password are required."}
        async with self._login_lock:
            self.state = "logging_in"
            self.last_error = None
            await self._discard_pending()
            try:
                if self.http is None or self.http.closed:
                    self.http = aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=90, connect=20)
                    )
                blink = self._new_blink({"username": email, "password": password})
                self.pending = blink
                ok = await asyncio.wait_for(blink.start(), timeout=75)
                if not ok:
                    raise RuntimeError("Blink rejected the sign-in.")
                await self._adopt(blink)
                # The password is not needed once tokens exist.
                blink.auth.data.pop("password", None)
                return {"status": "ready"}
            except BlinkTwoFARequiredError:
                self.state = "2fa_pending"
                return {
                    "status": "2fa_required",
                    "message": "Enter the code Blink sent to your email.",
                }
            except asyncio.TimeoutError:
                self.state = "login_required"
                self.last_error = "Blink did not answer in time."
                return {"status": "error", "message": self.last_error}
            except Exception as exc:  # noqa: BLE001 - report login failure
                self.state = "login_required"
                self.last_error = _short_error(exc)
                LOGGER.warning("Blink login failed: %s", exc)
                return {
                    "status": "error",
                    "message": "Sign-in failed. Check the email, password and 2FA code.",
                }

    async def verify_2fa(self, code: str) -> dict[str, Any]:
        code = "".join(ch for ch in code.strip() if ch.isdigit())
        if len(code) < 4:
            return {"status": "error", "message": "Enter the code from your email."}
        async with self._login_lock:
            if self.pending is None:
                self.state = "login_required"
                return {
                    "status": "error",
                    "message": "The sign-in expired. Start again.",
                }
            self.state = "2fa_pending"
            try:
                ok = await self.pending.send_2fa_code(code)
                if not ok:
                    raise RuntimeError("The code was not accepted.")
                blink = self.pending
                await self._adopt(blink)
                blink.auth.data.pop("password", None)
                return {"status": "ready"}
            except Exception as exc:  # noqa: BLE001 - code may be wrong/expired
                self.state = "2fa_pending"
                self.last_error = _short_error(exc)
                LOGGER.warning("Blink 2FA failed: %s", exc)
                return {
                    "status": "error",
                    "message": "That code was not accepted. Check it and try again.",
                }

    async def reset(self) -> None:
        async with self._login_lock:
            await self._discard_pending()
            self.blink = None
            self.state = "login_required"
            self.last_error = None
            if self.session_file.exists():
                with contextlib.suppress(OSError):
                    self.session_file.unlink()

    async def _discard_pending(self) -> None:
        self.pending = None

    # ----------------------------------------------------------------- cameras
    def camera_lookup(self, camera_id: str):
        blink = self.blink
        if blink is None or self.state != "ready":
            return None
        for camera in blink.cameras.values():
            if str(camera.camera_id) == str(camera_id):
                return camera
        return None

    def camera_payload(self, camera) -> dict[str, Any]:
        kind = _kind(camera)
        product = (camera.product_type or "").lower()
        model = MODEL_NAMES.get(product) or _pretty_model(camera.product_type)
        thumb_rev = hashlib.sha1(
            str(camera.thumbnail or camera.camera_id).encode("utf-8")
        ).hexdigest()[:10]
        temperature = _temperature_c(camera)
        wifi = camera.wifi_strength
        return {
            "id": str(camera.camera_id),
            "name": camera.name or "Camera",
            "display_name": _display_name(camera, kind),
            "kind": kind,
            "model": model,
            "online": bool(camera.online),
            "status": camera.status,
            "battery": _battery(camera),
            "battery_voltage": camera.battery_voltage,
            "wifi_strength": wifi if isinstance(wifi, (int, float)) else None,
            "wifi_bars": _wifi_bars(wifi),
            "temperature_c": round(temperature, 1)
            if isinstance(temperature, (int, float))
            else None,
            "motion": bool(camera.motion_detected),
            "armed": bool(camera.motion_enabled),
            "last_record": camera.last_record,
            "last_motion": camera.last_record or self._recent_motion.get(camera.name),
            "thumbnail_path": f"/api/cameras/{camera.camera_id}/thumbnail",
            "thumbnail_rev": thumb_rev,
            "stream_path": f"/api/live/{camera.camera_id}",
        }

    def cameras(self) -> list[dict[str, Any]]:
        blink = self.blink
        if blink is None or self.state != "ready":
            return []
        cameras = [self.camera_payload(cam) for cam in blink.cameras.values()]
        rank = {"doorbell": 0, "outdoor": 1, "mini": 2, "camera": 3}
        cameras.sort(key=lambda cam: (rank.get(cam["kind"], 9), cam["name"].lower()))
        return cameras

    def systems(self) -> list[dict[str, Any]]:
        blink = self.blink
        if blink is None or self.state != "ready":
            return []
        seen = set()
        systems = []
        for name, sync in blink.sync.items():
            # Mini and doorbell entries can share a network with a sync module.
            if sync.network_id in seen:
                continue
            try:
                armed = sync.arm
            except (KeyError, TypeError):
                continue
            if armed is None:
                continue
            seen.add(sync.network_id)
            systems.append({"name": name, "armed": bool(armed)})
        return systems

    async def set_armed(self, name: str, armed: bool) -> bool | None:
        """Arm or disarm a system. Returns None if the system does not exist."""

        blink = self.blink
        if blink is None or self.state != "ready" or name not in blink.sync:
            return None
        sync = blink.sync[name]
        if not await sync.async_arm(armed):
            return False
        # Show the new state now; the next refresh reports Blink's own value.
        with contextlib.suppress(KeyError, TypeError):
            sync.network_info["network"]["armed"] = armed
        return True

    async def _settings_document(self, camera) -> dict[str, Any]:
        if camera.camera_type == "doorbell":
            blink = self.blink
            url = (
                f"{blink.urls.base_url}/api/v1/accounts/{blink.account_id}"
                f"/networks/{camera.network_id}/doorbells/{camera.camera_id}/config"
            )
            document = await blink_api.http_get(blink, url)
            return document if isinstance(document, dict) else {}
        config = await blink_api.request_camera_info(
            self.blink, camera.network_id, camera.camera_id
        )
        return standard_document(config)

    async def camera_settings(self, camera_id: str) -> dict[str, Any] | None:
        """Describe a camera's options. Returns None if it does not exist."""

        camera = self.camera_lookup(camera_id)
        if camera is None:
            return None
        if camera.camera_type == "mini":
            return {
                "editable": False,
                "note": "Options are not available for this camera model yet.",
                "controls": [],
            }
        document = await self._settings_document(camera)
        if camera.camera_type == "doorbell":
            return describe_doorbell(document)
        return describe_standard(document)

    async def update_camera_settings(
        self, camera_id: str, changes: Any
    ) -> dict[str, Any] | None:
        """Apply validated changes. Raises SettingsError or RuntimeError on failure."""

        camera = self.camera_lookup(camera_id)
        if camera is None:
            return None
        if camera.camera_type in ("doorbell", "mini"):
            raise SettingsError("This camera's options can't be changed here.")
        document = await self._settings_document(camera)
        body, motion = build_update(changes, document)

        if body:
            # blinkpy keys routes by model; "catalina" selects the classic route.
            response = await blink_api.request_update_config(
                self.blink,
                camera.network_id,
                camera.camera_id,
                product_type="catalina",
                data=json.dumps(body),
            )
            if response is None or response.status != 200:
                raise RuntimeError(
                    "Blink did not accept that change. If this camera is live, "
                    "close the live view and try again."
                )
            answer = await response.json(content_type=None)
            if isinstance(answer, dict) and answer.get("id"):
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        blink_api.wait_for_command(self.blink, answer),
                        timeout=SETTINGS_COMMAND_SECONDS,
                    )
        if motion is not None and not await camera.async_arm(motion):
            raise RuntimeError("Blink did not accept the motion detection change.")

        await asyncio.sleep(SETTINGS_READ_BACK_SECONDS)
        latest = await self._settings_document(camera)
        result = describe_standard(latest)
        result["unconfirmed"] = unconfirmed(body, latest)
        return result

    async def thumbnail(self, camera_id: str) -> bytes | None:
        camera = self.camera_lookup(camera_id)
        if camera is None:
            return None
        image = camera.image_from_cache
        if image:
            return image
        try:
            response = await camera.get_media()
            if response is not None and getattr(response, "status", 0) == 200:
                image = await response.read()
                if image:
                    return image
        except Exception as exc:  # noqa: BLE001 - thumbnails are best-effort
            LOGGER.debug("Thumbnail fetch failed for %s: %s", camera_id, exc)
        return None

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        blink = self.blink
        account = None
        if blink is not None:
            email = blink.auth.data.get("username") or ""
            account = _mask_email(email)
        return {
            "state": self.state,
            "ready": self.state == "ready" and blink is not None,
            "account": account,
            "camera_count": len(blink.cameras) if blink else 0,
            "last_error": self.last_error,
            "setup_required": self.state != "ready",
            "blinkpy_version": getattr(blink, "version", None) if blink else None,
        }


def _kind(camera) -> str:
    text = f"{camera.camera_type or ''} {camera.product_type or ''} {camera.name or ''}".lower()
    if any(word in text for word in ("doorbell", "orchid", "lotus")):
        return "doorbell"
    if any(word in text for word in ("mini", "owl")):
        return "mini"
    if any(
        word in text
        for word in ("outdoor", "sonoran", "catalina", "hawk", "xt2")
    ):
        return "outdoor"
    return "camera"


def _temperature_c(camera) -> float | None:
    """Blink mixes calibrated Celsius and raw Fahrenheit across models."""

    value = camera.temperature_calibrated
    if not isinstance(value, (int, float)):
        value = camera.temperature
    if not isinstance(value, (int, float)):
        return None
    # The Sonoran endpoint returns 75 (Fahrenheit) while other endpoints
    # already return Celsius. Anything above 50 is not a room temperature.
    if value > 50:
        value = (value - 32) * 5.0 / 9.0
    if value < -40 or value > 60:
        return None
    return round(value, 1)


def _display_name(camera, kind: str) -> str:
    name = (camera.name or "").strip()
    if name:
        return name
    return {
        "doorbell": "Front Door",
        "outdoor": "Outdoor Camera",
        "mini": "Indoor Camera",
    }.get(kind, "Blink Camera")


def _pretty_model(value: str | None) -> str:
    if not value:
        return "Blink Camera"
    cleaned = value.replace("_", " ").replace("-", " ").strip()
    return "Blink " + cleaned.title() if cleaned else "Blink Camera"


def _battery(camera) -> str | None:
    value = camera.battery or camera.battery_state
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _wifi_bars(value) -> int | None:
    if not isinstance(value, (int, float)):
        return None
    # Blink reports a small 0..5-ish strength; some models report dBm.
    if value < 0:
        if value >= -50:
            return 4
        if value >= -60:
            return 3
        if value >= -70:
            return 2
        return 1
    return max(0, min(4, int(round(value))))


def _mask_email(email: str) -> str | None:
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"


def _short_error(exc: BaseException) -> str:
    return (str(exc).strip() or exc.__class__.__name__)[:300]
