"""Development-only mock of the bridge, used for UI and pipeline checks.

Run from the repository root:
    python tools/mock_bridge.py
Then open http://127.0.0.1:8098/app/ (it injects a localhost config.js).
"""

from __future__ import annotations

import os
import asyncio
import contextlib
import shutil
import time
import subprocess
import sys
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.camera_settings import build_update, describe_doorbell, describe_standard  # noqa: E402
from backend.mp4_codec import describe_init  # noqa: E402

TIZEN = ROOT / "tizen"
TMP = ROOT / ".tmp"
FRONT = TMP / "cam-front.jpg"
GARDEN = TMP / "cam-garden.jpg"

def ensure_demo_frames() -> None:
    """Create stand-ins when the optional demo photos are absent."""

    TMP.mkdir(exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    specs = [
        (FRONT, "testsrc2=size=1280x720:rate=1,eq=saturation=0.45:contrast=1.05"),
        (GARDEN, "testsrc2=size=1280x720:rate=1,eq=saturation=0.28:contrast=1.1,hue=h=80"),
    ]
    for path, video_filter in specs:
        if path.is_file():
            continue
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", video_filter,
                "-frames:v", "1", str(path),
            ],
            check=False,
        )


ensure_demo_frames()

CAMERAS = [
    {
        "id": "1",
        "last_motion": "2026-01-01T12:00:00+00:00",
        "name": "Front Door",
        "display_name": "FRONT DOOR",
        "kind": "doorbell",
        "model": "Blink Video Doorbell",
        "online": True,
        "status": "online",
        "battery": "ok",
        "wifi_bars": 4,
        "temperature_c": 18.4,
        "motion": False,
        "armed": True,
        "thumbnail_path": "/mock/thumb/front",
        "thumbnail_rev": "demo1",
        "stream_path": "/api/live/1",
    },
    {
        "id": "2",
        "name": "Garden",
        "display_name": "GARDEN",
        "kind": "outdoor",
        "model": "Blink Outdoor 4",
        "online": True,
        "status": "online",
        "battery": "ok",
        "wifi_bars": 3,
        "temperature_c": 12.1,
        "motion": False,
        "armed": True,
        "thumbnail_path": "/mock/thumb/garden",
        "thumbnail_rev": "demo2",
        "stream_path": "/api/live/2",
    },
]


async def health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "version": "mock",
            "blink": {
                "state": "ready",
                "ready": True,
                "account": "p***@gmail.com",
                "camera_count": 2,
                "last_error": None,
            },
            "live": {},
        }
    )


async def cameras(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "cameras": CAMERAS})


async def setup(request: web.Request) -> web.Response:
    return web.Response(text="Mock bridge — no account setup required.", content_type="text/plain")


async def thumbnail(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    path = FRONT if name == "front" else GARDEN
    if not path.is_file():
        return web.Response(status=404)
    return web.Response(body=path.read_bytes(), content_type="image/jpeg")


async def index(request: web.Request) -> web.Response:
    return web.FileResponse(TIZEN / "index.html")


async def config_js(request: web.Request) -> web.Response:
    body = (
        "window.BLINK_LIVE_CONFIG = {bridgeUrl: 'http://127.0.0.1:8098',"
        " apiToken: '', audioTrack: 0};"
    )
    return web.Response(text=body, content_type="application/javascript")


async def live_ws(request: web.Request) -> web.WebSocketResponse:
    camera_id = request.match_info["camera_id"]
    ws = web.WebSocketResponse(heartbeat=20, compress=False)
    await ws.prepare(request)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        await ws.close(code=1011)
        return ws

    frame = FRONT if camera_id == "1" else GARDEN
    args = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-re", "-loop", "1", "-framerate", "30", "-i", str(frame),
        "-re", "-f", "lavfi", "-i", "sine=frequency=523:sample_rate=48000:duration=300",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
        "-g", "30", "-pix_fmt", "yuv420p", "-t", "300",
        "-c:a", "aac", "-b:a", "64k",
        "-f", "mp4",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof+delay_moov",
        "-",
    ]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    buffer = b""
    info = None
    # Set these to rehearse Blink's session limit and the camera wake delay.
    session_seconds = float(os.environ.get("MOCK_SESSION_SECONDS", "0"))
    wake_seconds = float(os.environ.get("MOCK_WAKE_SECONDS", "0"))
    try:
        await asyncio.sleep(wake_seconds)
        deadline = time.monotonic() + session_seconds
        while len(buffer) < 4 * 1024 * 1024:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            buffer += chunk
            info = describe_init(buffer)
            if info:
                break
        if not info:
            await ws.close(code=1011)
            return ws
        camera = next((item for item in CAMERAS if item["id"] == camera_id), None)
        await ws.send_json(
            {
                "type": "ready",
                "video_codec": info["video_codec"],
                "audio_codec": info["audio_codec"],
                "audio_track": 0,
                "camera": camera["name"] if camera else "Demo",
            }
        )
        await ws.send_bytes(buffer)
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            if ws.closed:
                break
            await ws.send_bytes(chunk)
            if session_seconds and time.monotonic() >= deadline:
                await ws.send_json(
                    {"type": "ended", "message": "Reconnecting…", "retryable": True}
                )
                await ws.close()
                break
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=3)


SYSTEMS = [{"name": "Home", "armed": True}]
SETTINGS = {
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


async def camera_settings(request: web.Request) -> web.Response:
    if request.match_info["camera_id"] == "2":
        return web.json_response(
            {"ok": True, **describe_doorbell({"illuminator_intensity": 1, "led_state": "recording"})}
        )
    return web.json_response({"ok": True, **describe_standard(SETTINGS)})


async def camera_settings_update(request: web.Request) -> web.Response:
    body, motion = build_update((await request.json())["changes"], SETTINGS)
    SETTINGS.update(body)
    if motion is not None:
        SETTINGS["enabled"] = motion
    return web.json_response({"ok": True, "unconfirmed": [], **describe_standard(SETTINGS)})


async def systems(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "systems": SYSTEMS})


async def system_arm(request: web.Request) -> web.Response:
    SYSTEMS[0]["armed"] = (await request.json())["armed"]
    return web.json_response({"ok": True, "systems": SYSTEMS})


async def on_prepare(request: web.Request, response: web.StreamResponse) -> None:
    response.headers["Access-Control-Allow-Origin"] = "*"


def create_app() -> web.Application:
    app = web.Application()
    app.on_response_prepare.append(on_prepare)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/cameras", cameras)
    app.router.add_get("/api/cameras/{camera_id}/settings", camera_settings)
    app.router.add_post("/api/cameras/{camera_id}/settings", camera_settings_update)
    app.router.add_get("/api/systems", systems)
    app.router.add_post("/api/systems/{name}/arm", system_arm)
    app.router.add_get("/api/live/{camera_id}", live_ws)
    app.router.add_get("/mock/thumb/{name}", thumbnail)
    app.router.add_get("/setup", setup)
    app.router.add_get("/app/", index)
    app.router.add_get("/app/config.js", config_js)
    app.router.add_static("/app/", TIZEN, show_index=False)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="127.0.0.1", port=8098)
