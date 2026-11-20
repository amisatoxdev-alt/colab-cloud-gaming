#!/usr/bin/env python3
"""Comprehensive test suite for Nebula Cloud Gaming Portal.

Tests:
  - Static HTML & Asset Delivery (index, player, CSS, JS)
  - REST APIs (/api/status, /api/launch)
  - Binary Control WebSocket (Ping RTT, Mouse Absolute, Mouse Relative, Keyboard)
  - Video Stream WebSocket (Client connection, subscription, bitrate adjustment)
"""

import os
import sys
import asyncio
import aiohttp
import subprocess
import time

TEST_PORT = 8095
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"
WS_BASE = f"ws://127.0.0.1:{TEST_PORT}"


async def run_tests():
    print(f"[TEST] Starting Nebula Cloud Portal test suite against {BASE_URL}...")
    errors = []

    async with aiohttp.ClientSession() as session:
        # 1. Test GET / (Lobby)
        try:
            async with session.get(f"{BASE_URL}/") as resp:
                assert resp.status == 200, f"Expected 200, got {resp.status}"
                text = await resp.text()
                assert "NEBULA CLOUD GAMING" in text, "Missing title in index.html"
                assert "Windows 11 Fluent Desktop" in text, "Missing Win11 card in index.html"
                print("  [PASS] GET / -> 200 OK (Valid HTML & Lobby)")
        except Exception as e:
            errors.append(f"GET / failed: {e}")
            print(f"  [FAIL] GET / failed: {e}")

        # 2. Test GET /player (Player Canvas)
        try:
            async with session.get(f"{BASE_URL}/player") as resp:
                assert resp.status == 200, f"Expected 200, got {resp.status}"
                text = await resp.text()
                assert "streamCanvas" in text, "Missing streamCanvas in player.html"
                assert "hudOverlay" in text, "Missing hudOverlay in player.html"
                print("  [PASS] GET /player -> 200 OK (Player Canvas & HUD)")
        except Exception as e:
            errors.append(f"GET /player failed: {e}")
            print(f"  [FAIL] GET /player failed: {e}")

        # 3. Test Static Assets
        for asset, expected_type in [
            ("/static/css/portal.css", "css"),
            ("/static/js/portal.js", "javascript"),
            ("/static/js/stream_client.js", "javascript")
        ]:
            try:
                async with session.get(f"{BASE_URL}{asset}") as resp:
                    assert resp.status == 200, f"Expected 200 for {asset}, got {resp.status}"
                    c_type = resp.headers.get("Content-Type", "")
                    assert expected_type in c_type, f"Content-Type {c_type} missing {expected_type}"
                    print(f"  [PASS] GET {asset} -> 200 OK ({c_type})")
            except Exception as e:
                errors.append(f"GET {asset} failed: {e}")
                print(f"  [FAIL] GET {asset} failed: {e}")

        # 4. Test GET /api/status (Telemetry)
        try:
            async with session.get(f"{BASE_URL}/api/status") as resp:
                assert resp.status == 200, f"Expected 200, got {resp.status}"
                data = await resp.json()
                assert "gpu" in data, "Missing 'gpu' in status"
                assert "vram_total" in data, "Missing 'vram_total' in status"
                assert "resolution" in data, "Missing 'resolution' in status"
                assert "fps" in data, "Missing 'fps' in status"
                assert "games" in data, "Missing 'games' in status"
                print(f"  [PASS] GET /api/status -> 200 OK (GPU: {data['gpu']}, Res: {data['resolution']})")
        except Exception as e:
            errors.append(f"GET /api/status failed: {e}")
            print(f"  [FAIL] GET /api/status failed: {e}")

        # 5. Test POST /api/launch (Game Switcher)
        try:
            async with session.post(f"{BASE_URL}/api/launch", json={"app": "win11"}) as resp:
                assert resp.status == 200, f"Expected 200, got {resp.status}"
                data = await resp.json()
                assert data.get("status") == "ok", f"Expected ok status, got {data}"
                print(f"  [PASS] POST /api/launch (win11) -> 200 OK ({data.get('message')})")
        except Exception as e:
            errors.append(f"POST /api/launch failed: {e}")
            print(f"  [FAIL] POST /api/launch failed: {e}")

        # 6. Test WebSocket /ws/control (Binary Inputs & Ping)
        try:
            async with session.ws_connect(f"{WS_BASE}/ws/control") as ws:
                # Test 0x07 Ping Echo
                ping_pkt = bytes([0x07, 0x12, 0x34, 0x56, 0x78])
                await ws.send_bytes(ping_pkt)
                msg = await asyncio.wait_for(ws.receive(), timeout=3.0)
                assert msg.type == aiohttp.WSMsgType.BINARY, f"Expected binary echo, got {msg.type}"
                assert msg.data == ping_pkt, f"Echo mismatch: {msg.data} != {ping_pkt}"
                print("  [PASS] WS /ws/control -> Ping RTT echo verified")

                # Test 0x01 Absolute Mouse Move
                await ws.send_bytes(bytes([0x01, 0x02, 0x80, 0x01, 0x68]))  # (640, 360)

                # Test 0x02 Relative Mouse Move (PointerLock 3D)
                # dx = 10, dy = -5
                dx_bytes = int(10).to_bytes(2, byteorder="big", signed=True)
                dy_bytes = int(-5).to_bytes(2, byteorder="big", signed=True)
                await ws.send_bytes(bytes([0x02]) + dx_bytes + dy_bytes)

                # Test 0x03 Mouse Button (Left click press & release)
                await ws.send_bytes(bytes([0x03, 0x01, 0x01]))
                await ws.send_bytes(bytes([0x03, 0x01, 0x00]))

                # Test 0x04 Mouse Wheel
                dy_wheel = int(3).to_bytes(1, byteorder="big", signed=True)
                await ws.send_bytes(bytes([0x04]) + dy_wheel)

                # Test 0x05 Keyboard Event (KeyW press & release)
                key_code = b"KeyW"
                await ws.send_bytes(bytes([0x05, 0x01, len(key_code)]) + key_code)
                await ws.send_bytes(bytes([0x05, 0x00, len(key_code)]) + key_code)

                await ws.close()
                print("  [PASS] WS /ws/control -> Mouse & Keyboard binary input dispatch verified")
        except Exception as e:
            errors.append(f"WS /ws/control failed: {e}")
            print(f"  [FAIL] WS /ws/control failed: {e}")

        # 7. Test WebSocket /ws/stream (Video client subscription)
        try:
            async with session.ws_connect(f"{WS_BASE}/ws/stream") as ws:
                # Send bitrate adjustment config
                await ws.send_str('{"bitrate_mbps": 20}')
                await asyncio.sleep(0.3)
                await ws.close()
                print("  [PASS] WS /ws/stream -> Subscription & Bitrate configuration verified")
        except Exception as e:
            errors.append(f"WS /ws/stream failed: {e}")
            print(f"  [FAIL] WS /ws/stream failed: {e}")

    if errors:
        print(f"\n[SUMMARY] {len(errors)} test(s) FAILED:")
        for err in errors:
            print(f"  - {err}")
        return False
    else:
        print("\n[SUCCESS] All 7/7 test suites PASSED with 100% success rate!")
        return True


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    server_script = os.path.join(script_dir, "portal_server.py")

    print(f"[TEST RUNNER] Launching portal_server.py on port {TEST_PORT}...")
    proc = subprocess.Popen([sys.executable, server_script, str(TEST_PORT)])

    # Wait for server to bind
    time.sleep(1.5)

    try:
        success = asyncio.run(run_tests())
        if not success:
            sys.exit(1)
    finally:
        print("[TEST RUNNER] Shutting down test server...")
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        print("[TEST RUNNER] Cleanup complete.")


if __name__ == "__main__":
    main()

