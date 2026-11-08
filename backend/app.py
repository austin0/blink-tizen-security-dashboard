"""Blink Live Bridge: local HTTP/WebSocket relay for the Tizen TV app."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import shutil
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from .blink_service import BlinkManager
from .hls import HlsManager, serve_hls
from .live import LiveManager
from .ts import TsManager
from .setup_page import render_setup_page

VERSION = "1.0.0"
LOGGER = logging.getLogger("blink_bridge")
ROOT = Path(__file__).resolve().parent.parent
TIZEN_DIR = ROOT / "tizen"
DEFAULT_DATA_DIR = ROOT / "data"


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


class Bridge:
    def __init__(self) -> None:
        data_dir = Path(os.environ.get("BLINK_DATA_DIR", str(DEFAULT_DATA_DIR)))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = data_dir
        self.token = os.environ.get("BRIDGE_TOKEN", "").strip()
        self.max_live_seconds = int(os.environ.get("MAX_LIVE_SECONDS", "285"))
        self.manager = BlinkManager(data_dir)
        self.live = LiveManager(self.manager.camera_lookup, self.max_live_seconds)
        self.hls = HlsManager(self.manager.camera_lookup, self.max_live_seconds)
        self.ts = TsManager(self.manager.camera_lookup, self.max_live_seconds)

    def health(self) -> dict:
        # This endpoint is public, and HLS/TS session IDs grant media access.
        active = {**self.live.active(), **self.hls.active(), **self.ts.active()}
        return {
            "ok": True,
            "app": "blink-live-bridge",
            "version": VERSION,
            "server_time": int(time.time()),
            "setup_path": "/setup",
            "bridge": {
                "lan_only": True,
                "auth_token_required": bool(self.token),
                "data_dir": str(self.data_dir),
            },
            "blink": self.manager.status(),
            "live": {
                f"stream-{index}": state
                for index, state in enumerate(active.values(), start=1)
            },
            "ffmpeg": shutil.which("ffmpeg"),
        }


BRIDGE: Bridge | None = None


def _bridge() -> Bridge:
    assert BRIDGE is not None
    return BRIDGE


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - API errors must stay JSON
        LOGGER.exception("Unhandled error on %s %s", request.method, request.path)
        return web.json_response(
            {"ok": False, "error": "The bridge hit an internal error."}, status=500
        )


@web.middleware
async def token_middleware(request: web.Request, handler):
    token = _bridge().token
    if (
        token
        and request.path.startswith("/api/")
        and request.path != "/api/health"
        and not request.path.startswith("/api/hls/")
        and not request.path.startswith("/api/ts/")
    ):
        supplied = request.headers.get("X-Bridge-Token") or request.query.get("token", "")
        if not supplied or not hmac.compare_digest(supplied, token):
            return web.json_response(
                {"ok": False, "error": "Missing or invalid bridge token."}, status=401
            )
    return await handler(request)


def _same_tv_origin(origin: str, request: web.Request) -> bool:
    """Allow the Tizen file origin, localhost dev, and this exact host."""

    if not origin or origin == "null" or origin.startswith("file:"):
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.netloc == request.headers.get("Host", ""):
        return True
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


async def _cors_prepare(request: web.Request, response: web.StreamResponse) -> None:
    origin = request.headers.get("Origin", "")
    if origin and _same_tv_origin(origin, request):
        response.headers["Access-Control-Allow-Origin"] = (
            "null" if origin == "null" or origin.startswith("file:") else origin
        )
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Bridge-Token"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"


async def options_handler(request: web.Request) -> web.Response:
    return web.Response(status=204)


async def root(request: web.Request) -> web.Response:
    raise web.HTTPFound("/setup")


async def app_index(request: web.Request) -> web.StreamResponse:
    index = TIZEN_DIR / "index.html"
    if not index.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(index, headers={"Cache-Control": "no-store"})


async def setup_page(request: web.Request) -> web.Response:
    return web.Response(
        text=render_setup_page(),
        content_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
            ),
        },
    )


async def health(request: web.Request) -> web.Response:
    return web.json_response(_bridge().health())


async def doctor(request: web.Request) -> web.Response:
    bridge = _bridge()
    return web.json_response(
        {
            "ok": True,
            "version": VERSION,
            "data_dir": str(bridge.data_dir),
            "data_dir_writable": os.access(bridge.data_dir, os.W_OK),
            "session_file": bridge.manager.session_file.exists(),
            "ffmpeg": shutil.which("ffmpeg"),
            "audio": _env_bool("BLINK_AUDIO", True),
            "default_audio_track": int(os.environ.get("BLINK_AUDIO_TRACK", "0")),
            "max_live_seconds": bridge.max_live_seconds,
            "blink": bridge.manager.status(),
            "live": {
                **bridge.live.active(),
                **bridge.hls.active(),
                **bridge.ts.active(),
            },
            "last_live_error": bridge.live.last_error,
        }
    )


async def cameras(request: web.Request) -> web.Response:
    manager = _bridge().manager
    if manager.state != "ready":
        return web.json_response(
            {
                "ok": False,
                "error": "Blink account is not linked.",
                "blink": manager.status(),
            },
            status=409,
        )
    return web.json_response({"ok": True, "cameras": manager.cameras()})


async def camera_thumbnail(request: web.Request) -> web.Response:
    camera_id = request.match_info["camera_id"]
    image = await _bridge().manager.thumbnail(camera_id)
    if not image:
        return web.Response(status=404, text="No thumbnail")
    return web.Response(
        body=image,
        content_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


async def auth_start(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return web.json_response({"status": "error", "message": "Invalid JSON."}, status=400)
    email = str(payload.get("email", ""))
    password = str(payload.get("password", ""))
    result = await _bridge().manager.begin_login(email, password)
    return web.json_response(result, status=200 if result["status"] != "error" else 400)


async def auth_verify(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return web.json_response({"status": "error", "message": "Invalid JSON."}, status=400)
    result = await _bridge().manager.verify_2fa(str(payload.get("code", "")))
    return web.json_response(result, status=200 if result["status"] != "error" else 400)


async def auth_reset(request: web.Request) -> web.Response:
    await _bridge().live.close()
    await _bridge().hls.close()
    await _bridge().ts.close()
    await _bridge().manager.reset()
    return web.json_response({"ok": True})


async def hls_start(request: web.Request) -> web.Response:
    bridge = _bridge()
    camera_id = request.match_info["camera_id"]
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    try:
        audio_track = int(payload.get("audio_track", 0))
    except (TypeError, ValueError):
        audio_track = 0
    if bridge.manager.camera_lookup(camera_id) is None:
        return web.json_response(
            {"ok": False, "error": "Camera not found."}, status=404
        )
    # Blink serialises live views.  Never leave another rendition running while
    # AVPlay opens the HLS rendition.
    await bridge.live.close()
    await bridge.ts.close()
    try:
        session = await bridge.hls.get_or_create(camera_id, audio_track)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Could not start HLS live view: %s", exc)
        return web.json_response(
            {"ok": False, "error": str(exc) or "Could not start HLS live view."},
            status=503,
        )
    return web.json_response(
        {
            "ok": True,
            "session_id": session.id,
            "playlist_url": session.playlist_url,
            "audio_track": session.audio_track,
            "camera": session.camera.name,
        }
    )


async def hls_file(request: web.Request) -> web.StreamResponse:
    bridge = _bridge()
    session = bridge.hls.get(request.match_info["session_id"])
    if session is None:
        raise web.HTTPNotFound()
    return await serve_hls(request, session)


async def ts_start(request: web.Request) -> web.Response:
    bridge = _bridge()
    camera_id = request.match_info["camera_id"]
    if bridge.manager.camera_lookup(camera_id) is None:
        return web.json_response(
            {"ok": False, "error": "Camera not found."}, status=404
        )
    await bridge.live.close()
    await bridge.hls.close()
    try:
        session = await bridge.ts.get_or_create(camera_id)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Could not start raw TS live view: %s", exc)
        return web.json_response(
            {"ok": False, "error": str(exc) or "Could not start raw TS."},
            status=503,
        )
    return web.json_response(
        {
            "ok": True,
            "session_id": session.id,
            "stream_url": session.stream_url,
            "camera": session.camera.name,
        }
    )


async def ts_stream(request: web.Request) -> web.StreamResponse:
    bridge = _bridge()
    session = bridge.ts.get(request.match_info["session_id"])
    if session is None:
        raise web.HTTPNotFound()
    return await session.serve(request)


async def live_ws(request: web.Request) -> web.StreamResponse:
    bridge = _bridge()
    origin = request.headers.get("Origin", "")
    if origin and not _same_tv_origin(origin, request):
        return web.json_response(
            {"ok": False, "error": "This origin is not allowed to open a live view."},
            status=403,
        )
    camera_id = request.match_info["camera_id"]
    try:
        audio_track = int(request.query.get("audio_track", os.environ.get("BLINK_AUDIO_TRACK", "0")))
    except ValueError:
        audio_track = 0
    audio_track = 1 if audio_track else 0

    ws = web.WebSocketResponse(heartbeat=20, compress=False, max_msg_size=0)
    await ws.prepare(request)

    camera = bridge.manager.camera_lookup(camera_id)
    if camera is None:
        await ws.send_json(
            {
                "type": "error",
                "message": "Camera not found, or the Blink account is not linked.",
                "retryable": False,
            }
        )
        await ws.close(code=1008)
        return ws

    await bridge.hls.close()
    await bridge.ts.close()
    try:
        session = await bridge.live.get_or_create(camera_id, audio_track)
    except Exception as exc:  # noqa: BLE001 - report to the TV
        LOGGER.warning("Could not create live session: %s", exc)
        await ws.send_json(
            {
                "type": "error",
                "message": str(exc) or "Could not start the live view.",
                "retryable": True,
            }
        )
        await ws.close(code=1011)
        return ws

    client = await session.add_client(ws)
    if client is None:
        await ws.close(code=1012)
        return ws

    try:
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                try:
                    payload = json.loads(message.data)
                except ValueError:
                    continue
                if payload.get("type") == "stop":
                    break
            elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                break
    finally:
        await session.remove_client(client)
        if not ws.closed:
            await ws.close()

    return ws


async def live_stop(request: web.Request) -> web.Response:
    camera_id = request.match_info["camera_id"]
    bridge = _bridge()
    stopped_ws = await bridge.live.stop_camera(camera_id)
    stopped_hls = await bridge.hls.stop_camera(camera_id)
    stopped_ts = await bridge.ts.stop_camera(camera_id)
    return web.json_response(
        {"ok": True, "stopped": stopped_ws or stopped_hls or stopped_ts}
    )


async def on_startup(app: web.Application) -> None:
    global BRIDGE
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if not shutil.which("ffmpeg"):
        LOGGER.error("ffmpeg is missing; live view will not work")
    BRIDGE = Bridge()
    app["bridge"] = BRIDGE
    LOGGER.info("Blink Live Bridge %s starting (data: %s)", VERSION, BRIDGE.data_dir)
    await BRIDGE.manager.initialize()
    LOGGER.info("Blink state: %s", BRIDGE.manager.state)


async def on_cleanup(app: web.Application) -> None:
    if BRIDGE is None:
        return
    await BRIDGE.live.close()
    await BRIDGE.hls.close()
    await BRIDGE.ts.close()
    await BRIDGE.manager.close()


def create_app() -> web.Application:
    app = web.Application(middlewares=[error_middleware, token_middleware])
    app.on_response_prepare.append(_cors_prepare)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_route("OPTIONS", "/{tail:.*}", options_handler)
    app.router.add_get("/", root)
    app.router.add_get("/setup", setup_page)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/doctor", doctor)
    app.router.add_get("/api/cameras", cameras)
    app.router.add_get("/api/cameras/{camera_id}/thumbnail", camera_thumbnail)
    app.router.add_post("/api/auth/start", auth_start)
    app.router.add_post("/api/auth/verify", auth_verify)
    app.router.add_post("/api/auth/reset", auth_reset)
    app.router.add_post("/api/live/{camera_id}/hls", hls_start)
    app.router.add_get("/api/hls/{session_id}/{filename}", hls_file)
    app.router.add_post("/api/live/{camera_id}/ts", ts_start)
    app.router.add_get("/api/ts/{session_id}/stream.ts", ts_stream)
    app.router.add_get("/api/live/{camera_id}", live_ws)
    app.router.add_post("/api/live/{camera_id}/stop", live_stop)
    if TIZEN_DIR.is_dir():
        app.router.add_get("/app/", app_index)
        app.router.add_static("/app/", TIZEN_DIR, show_index=False)
    return app


if __name__ == "__main__":
    host = os.environ.get("BLINK_BIND", "0.0.0.0")
    port = int(os.environ.get("PORT", "8099"))
    web.run_app(create_app(), host=host, port=port, access_log=LOGGER)
