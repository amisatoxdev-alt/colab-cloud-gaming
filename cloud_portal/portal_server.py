#!/usr/bin/env python3
"""Unified Cloud Gaming Web Portal & Ultra-Low Latency Streaming Server.

Exposes a modern web client over Tailscale Funnel on port 8090.
Combines:
  1. GeForce NOW style Web Lobby (Game selection & launch).
  2. WebCodecs + WebGPU / WebGL2 low-latency streaming canvas.
  3. Binary WebSocket input dispatcher with sub-millisecond XTest injection.
  4. Real-time GPU telemetry and game management APIs.
"""

import os
import sys
import json
import time
import asyncio
import logging
import subprocess
from aiohttp import web, WSMsgType

from input_driver import driver
from stream_engine import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("portal_server")

PORTAL_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(PORTAL_DIR, "static")

# -------------------------------------------------------------
# REST API Endpoints
# -------------------------------------------------------------
async def api_status(request):
    """Query live GPU, memory, and game status."""
    telemetry = {
        "gpu": "NVIDIA GPU",
        "vram_total": "15360 MiB",
        "vram_used": "0 MiB",
        "temp": "N/A",
        "util": "0 %",
        "subscribers": len(engine.subscribers),
        "resolution": f"{engine.width}x{engine.height}",
        "fps": engine.fps,
        "games": {
            "win11": {"installed": True, "name": "Windows 11 Fluent Desktop"},
            "steam": {"installed": os.path.exists("/usr/games/steam") or os.path.exists("/usr/bin/steam"), "name": "Steam Big Picture"},
            "gtav": {"installed": os.path.exists("/content/gta_launch.sh") or os.path.exists("/opt/gtav"), "name": "Grand Theft Auto V"},
            "chrome": {"installed": os.path.exists("/usr/bin/google-chrome"), "name": "Google Chrome Cloud"},
            "retroarch": {"installed": os.path.exists("/usr/bin/retroarch"), "name": "RetroArch Emulation"}
        }
    }
    try:
        res = subprocess.run(
            "nvidia-smi --query-gpu=name,memory.total,memory.used,temperature.gpu,utilization.gpu --format=csv,noheader",
            shell=True, text=True, capture_output=True
        )
        if res.stdout.strip():
            p = [x.strip() for x in res.stdout.strip().split(",")]
            telemetry["gpu"] = p[0] if len(p) > 0 else telemetry["gpu"]
            telemetry["vram_total"] = p[1] if len(p) > 1 else telemetry["vram_total"]
            telemetry["vram_used"] = p[2] if len(p) > 2 else telemetry["vram_used"]
            telemetry["temp"] = p[3] if len(p) > 3 else telemetry["temp"]
            telemetry["util"] = p[4] if len(p) > 4 else telemetry["util"]
    except Exception as e:
        log.warning(f"Failed to query nvidia-smi: {e}")

    return web.json_response(telemetry)


async def api_launch(request):
    """Launch an application or game on Xorg display :1."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    app_id = data.get("app", "win11").lower()
    log.info(f"API Launch requested for app: {app_id}")

    if app_id in ("win11", "desktop"):
        # Minimize or bring desktop to focus
        subprocess.run("DISPLAY=:1 xdotool key super+d 2>/dev/null || true", shell=True)
        return web.json_response({"status": "ok", "message": "Switched to Windows 11 Desktop"})

    elif app_id == "steam":
        cmd = "DISPLAY=:1 nohup setsid steam -bigpicture -gamepadui >/var/log/services/steam.log 2>&1 &"
        subprocess.run(cmd, shell=True)
        return web.json_response({"status": "ok", "message": "Launching Steam Big Picture Mode..."})

    elif app_id == "gtav":
        if os.path.exists("/content/gta_launch.sh"):
            cmd = "DISPLAY=:1 nohup setsid bash /content/gta_launch.sh >/var/log/services/gta_run.log 2>&1 &"
            subprocess.run(cmd, shell=True)
            return web.json_response({"status": "ok", "message": "Launching Grand Theft Auto V..."})
        else:
            return web.json_response({"status": "error", "message": "GTA V is not installed yet. Run /gtav in launcher."})

    elif app_id == "chrome":
        cmd = "DISPLAY=:1 nohup setsid google-chrome --no-sandbox --no-first-run >/dev/null 2>&1 &"
        subprocess.run(cmd, shell=True)
        return web.json_response({"status": "ok", "message": "Launching Google Chrome..."})

    elif app_id == "retroarch":
        cmd = "DISPLAY=:1 nohup setsid retroarch -f >/dev/null 2>&1 &"
        subprocess.run(cmd, shell=True)
        return web.json_response({"status": "ok", "message": "Launching RetroArch..."})

    elif app_id == "terminal":
        cmd = "DISPLAY=:1 nohup setsid rofi -show run >/dev/null 2>&1 &"
        subprocess.run(cmd, shell=True)
        return web.json_response({"status": "ok", "message": "Opened application launcher"})

    return web.json_response({"status": "error", "message": f"Unknown application '{app_id}'"})


# -------------------------------------------------------------
# WebSocket Streaming (Video & Audio Out)
# -------------------------------------------------------------
async def ws_stream_handler(request):
    """Streams live Annex-B video & Opus audio packets to the browser."""
    ws = web.WebSocketResponse(heartbeat=15.0)
    await ws.prepare(request)

    engine.add_client(ws)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                # Client config message (e.g. bitrate request)
                try:
                    cfg = json.loads(msg.data)
                    if "bitrate_mbps" in cfg:
                        engine.bitrate_mbps = float(cfg["bitrate_mbps"])
                except Exception:
                    pass
            elif msg.type == WSMsgType.ERROR:
                log.warning(f"WebSocket error: {ws.exception()}")
    finally:
        engine.remove_client(ws)

    return ws


# -------------------------------------------------------------
# WebSocket Control & Input Injection (Keyboard, Mouse, Gamepad)
# -------------------------------------------------------------
async def ws_control_handler(request):
    """Receives binary control packets from browser and injects via XTest."""
    ws = web.WebSocketResponse(heartbeat=15.0)
    await ws.prepare(request)

    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                data = msg.data
                if not data:
                    continue
                tag = data[0]

                # Tag 0x01: Mouse Move Absolute [0x01, x_hi, x_lo, y_hi, y_lo]
                if tag == 0x01 and len(data) >= 5:
                    x = (data[1] << 8) | data[2]
                    y = (data[3] << 8) | data[4]
                    driver.mouse_move(x, y)

                # Tag 0x02: Mouse Move Relative (PointerLock) [0x02, dx_hi, dx_lo, dy_hi, dy_lo, sign_bits]
                elif tag == 0x02 and len(data) >= 5:
                    dx = int.from_bytes(data[1:3], byteorder="big", signed=True)
                    dy = int.from_bytes(data[3:5], byteorder="big", signed=True)
                    driver.mouse_rel_move(dx, dy)

                # Tag 0x03: Mouse Button [0x03, button, is_press]
                elif tag == 0x03 and len(data) >= 3:
                    btn = data[1]
                    is_press = bool(data[2])
                    driver.mouse_button(btn, is_press)

                # Tag 0x04: Mouse Wheel [0x04, dy_int8]
                elif tag == 0x04 and len(data) >= 2:
                    dy = int.from_bytes(data[1:2], byteorder="big", signed=True)
                    driver.mouse_wheel(dy)

                # Tag 0x05: Keyboard Event [0x05, is_press, code_len, code_utf8...]
                elif tag == 0x05 and len(data) >= 3:
                    is_press = bool(data[1])
                    code_len = data[2]
                    code_str = data[3:3 + code_len].decode("utf-8", errors="ignore")
                    driver.key_event(code_str, is_press)

                # Tag 0x07: Latency Ping Echo [0x07, timestamp_8bytes...]
                elif tag == 0x07:
                    # Echo directly back to client for RTT ping measurement
                    await ws.send_bytes(data)

            elif msg.type == WSMsgType.TEXT:
                # Text input or clipboard string
                try:
                    payload = json.loads(msg.data)
                    if payload.get("type") == "text":
                        text = payload.get("text", "")
                        # Type text cleanly using xdotool
                        env = os.environ.copy()
                        env["DISPLAY"] = ":1"
                        subprocess.run(["xdotool", "type", "--clearmodifiers", "--", text], env=env, check=False)
                except Exception:
                    pass
    finally:
        pass

    return ws


# -------------------------------------------------------------
# Static HTML Serves
# -------------------------------------------------------------
async def index_handler(request):
    return web.FileResponse(os.path.join(STATIC_DIR, "index.html"))

async def player_handler(request):
    return web.FileResponse(os.path.join(STATIC_DIR, "player.html"))


def build_app():
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_get("/player", player_handler)
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/launch", api_launch)
    app.router.add_get("/ws/stream", ws_stream_handler)
    app.router.add_get("/ws/control", ws_control_handler)
    app.router.add_static("/static/", STATIC_DIR, show_index=False)
    return app


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    log.info(f"Starting Colab Cloud Gaming Portal on http://0.0.0.0:{port}...")
    app = build_app()
    web.run_app(app, host="0.0.0.0", port=port, print=None)


if __name__ == "__main__":
    main()

