#!/usr/bin/env python3
"""
ColabPad — Userspace Virtual Xbox 360 Controller Driver for Google Colab

Architecture:
  - Runs as ROOT so it can write to /dev/uinput
  - /dev/uinput is chmod 600 (root-only) so Sunshine never sees it
  - Sunshine stays on native Xorg XTest for mouse & keyboard (0ms latency)
  - Creates a real Xbox 360 Controller at /dev/input/event* readable by all users
  - Games, Steam, Proton, SDL2 all detect it as a standard Xbox 360 pad
  - Receives gamepad state via HTTP POST from the web interface (ColabPad Web)

Endpoints:
  GET  /             → Web gamepad UI (HTML5 Gamepad API + on-screen controls)
  POST /api/state    → Receive gamepad state JSON {buttons: {}, axes: {}}
  GET  /api/status   → Health check

Usage:
  python3 colabpad.py          # Starts on port 9876
  python3 colabpad.py --port 9876
"""

import os
import sys
import json
import time
import signal
import struct
import threading
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler

# ---------------------------------------------------------------------------
# 1. Virtual Gamepad via evdev UInput
# ---------------------------------------------------------------------------
GAMEPAD = None
GAMEPAD_LOCK = threading.Lock()

def create_gamepad():
    """Create a virtual Xbox 360 Controller via /dev/uinput"""
    global GAMEPAD

    # Clean any stale device nodes before UInput init
    os.system("rm -f /dev/input/event* /dev/input/js* 2>/dev/null || true")

    # Ensure /dev/uinput exists and is root-only
    os.system("mknod /dev/uinput c 10 223 2>/dev/null || true")
    os.system("chmod 600 /dev/uinput && chown root:root /dev/uinput")
    os.system("mkdir -p /dev/input")

    import evdev
    from evdev import UInput, AbsInfo, ecodes

    capabilities = {
        ecodes.EV_KEY: [
            ecodes.BTN_A,       # A / Cross
            ecodes.BTN_B,       # B / Circle
            ecodes.BTN_X,       # X / Square
            ecodes.BTN_Y,       # Y / Triangle
            ecodes.BTN_TL,      # Left Bumper (LB)
            ecodes.BTN_TR,      # Right Bumper (RB)
            ecodes.BTN_SELECT,  # Back / Select
            ecodes.BTN_START,   # Start / Menu
            ecodes.BTN_MODE,    # Xbox / Guide button
            ecodes.BTN_THUMBL,  # Left Stick Click (L3)
            ecodes.BTN_THUMBR,  # Right Stick Click (R3)
        ],
        ecodes.EV_ABS: [
            (ecodes.ABS_X,     AbsInfo(0, -32768, 32767, 16, 128, 0)),   # Left Stick X
            (ecodes.ABS_Y,     AbsInfo(0, -32768, 32767, 16, 128, 0)),   # Left Stick Y
            (ecodes.ABS_RX,    AbsInfo(0, -32768, 32767, 16, 128, 0)),   # Right Stick X
            (ecodes.ABS_RY,    AbsInfo(0, -32768, 32767, 16, 128, 0)),   # Right Stick Y
            (ecodes.ABS_Z,     AbsInfo(0, 0, 255, 0, 0, 0)),            # Left Trigger (LT)
            (ecodes.ABS_RZ,    AbsInfo(0, 0, 255, 0, 0, 0)),            # Right Trigger (RT)
            (ecodes.ABS_HAT0X, AbsInfo(0, -1, 1, 0, 0, 0)),             # D-Pad X
            (ecodes.ABS_HAT0Y, AbsInfo(0, -1, 1, 0, 0, 0)),             # D-Pad Y
        ],
    }

    GAMEPAD = UInput(
        capabilities,
        name="Xbox 360 Controller",
        vendor=0x045e,   # Microsoft
        product=0x028e,  # Xbox 360 Controller
        version=0x0110,
        bustype=ecodes.BUS_USB,
    )
    time.sleep(0.3)

    # Create /dev/input/event* nodes from sysfs
    for entry in os.listdir("/sys/class/input/"):
        dev_file = f"/sys/class/input/{entry}/dev"
        if os.path.exists(dev_file):
            with open(dev_file) as f:
                mm = f.read().strip()
            major, minor = mm.split(":")
            dev_path = f"/dev/input/{entry}"
            if not os.path.exists(dev_path):
                os.system(f"mknod {dev_path} c {major} {minor} 2>/dev/null || true")
            os.system(f"chmod 666 {dev_path}")

    # Find our device
    our_device = None
    for dev_path in evdev.list_devices():
        try:
            d = evdev.InputDevice(dev_path)
            if d.name == "Xbox 360 Controller" and d.info.vendor == 0x045e:
                our_device = dev_path
                break
        except Exception:
            pass

    return our_device


# Button name → evdev keycode mapping
BUTTON_MAP = {
    "a": 304,       # BTN_A / BTN_SOUTH
    "b": 305,       # BTN_B / BTN_EAST
    "x": 307,       # BTN_X / BTN_NORTH  (note: evdev swaps X/Y names)
    "y": 308,       # BTN_Y / BTN_WEST
    "lb": 310,      # BTN_TL
    "rb": 311,      # BTN_TR
    "back": 314,    # BTN_SELECT
    "start": 315,   # BTN_START
    "guide": 316,   # BTN_MODE
    "ls": 317,      # BTN_THUMBL
    "rs": 318,      # BTN_THUMBR
}

# Axis name → evdev code mapping
AXIS_MAP = {
    "lx": 0,    # ABS_X
    "ly": 1,    # ABS_Y
    "rx": 3,    # ABS_RX
    "ry": 4,    # ABS_RY
    "lt": 2,    # ABS_Z
    "rt": 5,    # ABS_RZ
    "dx": 16,   # ABS_HAT0X
    "dy": 17,   # ABS_HAT0Y
}


def apply_state(state):
    """Apply a gamepad state dict to the virtual controller"""
    global GAMEPAD
    if not GAMEPAD:
        return

    from evdev import ecodes

    with GAMEPAD_LOCK:
        # Buttons: {"a": 1, "b": 0, ...}
        buttons = state.get("buttons", {})
        for name, value in buttons.items():
            code = BUTTON_MAP.get(name.lower())
            if code is not None:
                GAMEPAD.write(ecodes.EV_KEY, code, int(bool(value)))

        # Axes: {"lx": 0.5, "ly": -0.3, "lt": 0.8, ...}
        axes = state.get("axes", {})
        for name, value in axes.items():
            code = AXIS_MAP.get(name.lower())
            if code is not None:
                if name.lower() in ("lt", "rt"):
                    # Triggers: 0.0–1.0 → 0–255
                    int_val = int(max(0, min(1, float(value))) * 255)
                elif name.lower() in ("dx", "dy"):
                    # D-pad: -1, 0, 1
                    int_val = int(max(-1, min(1, float(value))))
                else:
                    # Sticks: -1.0–1.0 → -32768–32767
                    int_val = int(max(-1, min(1, float(value))) * 32767)
                GAMEPAD.write(ecodes.EV_ABS, code, int_val)

        GAMEPAD.syn()


# ---------------------------------------------------------------------------
# 2. Web Gamepad UI (HTML)
# ---------------------------------------------------------------------------
WEB_UI = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>ColabPad — Virtual Gamepad</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0a0a0a; color:#e0e0e0; font-family:'Segoe UI',system-ui,sans-serif;
       display:flex; flex-direction:column; align-items:center; min-height:100vh;
       touch-action:none; user-select:none; -webkit-user-select:none; }
h1 { color:#00d4ff; font-size:1.4em; margin:12px 0 4px; text-shadow:0 0 10px #00d4ff40; }
.status { font-size:0.85em; margin-bottom:10px; padding:4px 12px; border-radius:12px; }
.status.connected { background:#00ff4420; color:#00ff44; border:1px solid #00ff4440; }
.status.disconnected { background:#ff444420; color:#ff4444; border:1px solid #ff444440; }
.status.physical { background:#ffaa0020; color:#ffaa00; border:1px solid #ffaa0040; }

.gamepad { position:relative; width:min(95vw, 700px); height:min(60vh, 400px);
           background:#1a1a2e; border-radius:24px; border:2px solid #333;
           box-shadow:0 0 30px #00000080; margin:8px; }

/* Analog sticks */
.stick-zone { position:absolute; width:120px; height:120px; border-radius:50%;
              background:#22223a; border:2px solid #444; }
.stick-zone.left { bottom:20px; left:30px; }
.stick-zone.right { bottom:20px; right:30px; }
.stick-knob { position:absolute; width:50px; height:50px; border-radius:50%;
              background:radial-gradient(circle, #555, #333); border:2px solid #666;
              top:50%; left:50%; transform:translate(-50%,-50%); pointer-events:none;
              transition: none; }

/* Face buttons */
.btn { position:absolute; border-radius:50%; border:2px solid #555; cursor:pointer;
       display:flex; align-items:center; justify-content:center; font-weight:bold;
       font-size:0.9em; transition: background 0.05s; }
.btn:active, .btn.pressed { transform:scale(0.92); }
.btn-a { width:44px; height:44px; right:40px; top:50%; background:#2d7a2d; color:#5f5; }
.btn-a.pressed { background:#4a4; }
.btn-b { width:44px; height:44px; right:10px; top:36%; background:#7a2d2d; color:#f55; }
.btn-b.pressed { background:#a44; }
.btn-x { width:44px; height:44px; right:70px; top:36%; background:#2d2d7a; color:#55f; }
.btn-x.pressed { background:#44a; }
.btn-y { width:44px; height:44px; right:40px; top:22%; background:#7a7a2d; color:#ff5; }
.btn-y.pressed { background:#aa4; }

/* Shoulder buttons */
.shoulder { position:absolute; top:0; width:100px; height:32px; background:#333;
            border-radius:0 0 12px 12px; border:1px solid #555; display:flex;
            align-items:center; justify-content:center; font-size:0.8em; cursor:pointer; }
.shoulder.pressed { background:#555; }
.lb { left:30px; }
.rb { right:30px; }

/* Triggers */
.trigger { position:absolute; top:-8px; width:70px; height:24px; background:#222;
           border-radius:12px; border:1px solid #444; overflow:hidden; cursor:pointer; }
.trigger-fill { height:100%; background:#00d4ff; width:0%; transition:width 0.05s; }
.lt { left:50px; }
.rt { right:50px; }

/* D-Pad */
.dpad { position:absolute; left:50px; top:28%; width:80px; height:80px; }
.dpad-btn { position:absolute; width:28px; height:28px; background:#333;
            border:1px solid #555; cursor:pointer; display:flex; align-items:center;
            justify-content:center; font-size:0.7em; }
.dpad-btn.pressed { background:#666; }
.dpad-up { top:0; left:50%; transform:translateX(-50%); border-radius:4px 4px 0 0; }
.dpad-down { bottom:0; left:50%; transform:translateX(-50%); border-radius:0 0 4px 4px; }
.dpad-left { left:0; top:50%; transform:translateY(-50%); border-radius:4px 0 0 4px; }
.dpad-right { right:0; top:50%; transform:translateY(-50%); border-radius:0 4px 4px 0; }

/* Center buttons */
.center-btn { position:absolute; top:45%; width:40px; height:20px; background:#333;
              border:1px solid #555; border-radius:4px; cursor:pointer; font-size:0.6em;
              display:flex; align-items:center; justify-content:center; }
.center-btn.pressed { background:#666; }
.btn-back { left:38%; }
.btn-start { right:38%; }
.btn-guide { left:50%; transform:translateX(-50%); top:38%; width:30px; height:30px;
             border-radius:50%; background:#333; font-size:1.2em; }

.info { font-size:0.75em; color:#666; margin:8px; text-align:center; }
</style>
</head>
<body>
<h1>🎮 ColabPad</h1>
<div class="status disconnected" id="status">Connecting...</div>

<div class="gamepad" id="gamepad">
  <!-- Triggers -->
  <div class="trigger lt" id="lt"><div class="trigger-fill" id="lt-fill"></div></div>
  <div class="trigger rt" id="rt"><div class="trigger-fill" id="rt-fill"></div></div>

  <!-- Shoulders -->
  <div class="shoulder lb" id="lb">LB</div>
  <div class="shoulder rb" id="rb">RB</div>

  <!-- D-Pad -->
  <div class="dpad">
    <div class="dpad-btn dpad-up" id="dup">▲</div>
    <div class="dpad-btn dpad-down" id="ddown">▼</div>
    <div class="dpad-btn dpad-left" id="dleft">◄</div>
    <div class="dpad-btn dpad-right" id="dright">►</div>
  </div>

  <!-- Face buttons -->
  <div class="btn btn-a" id="btn-a">A</div>
  <div class="btn btn-b" id="btn-b">B</div>
  <div class="btn btn-x" id="btn-x">X</div>
  <div class="btn btn-y" id="btn-y">Y</div>

  <!-- Center -->
  <div class="center-btn btn-back" id="btn-back">⏪</div>
  <div class="center-btn btn-guide" id="btn-guide">⊕</div>
  <div class="center-btn btn-start" id="btn-start">⏩</div>

  <!-- Sticks -->
  <div class="stick-zone left" id="stick-left"><div class="stick-knob" id="knob-left"></div></div>
  <div class="stick-zone right" id="stick-right"><div class="stick-knob" id="knob-right"></div></div>
</div>

<div class="info">
  Connect a physical controller (Xbox/PS/Switch) for auto-detect, or use on-screen controls.<br>
  Polling at <span id="hz">0</span> Hz | Latency: <span id="latency">-</span>ms
</div>

<script>
const API = window.location.origin + '/api/state';
const state = {
  buttons: { a:0, b:0, x:0, y:0, lb:0, rb:0, back:0, start:0, guide:0, ls:0, rs:0 },
  axes: { lx:0, ly:0, rx:0, ry:0, lt:0, rt:0, dx:0, dy:0 }
};
let sendCount = 0, lastHz = Date.now(), physicalConnected = false;

// --- Physical Gamepad (HTML5 Gamepad API) ---
function pollPhysical() {
  const gps = navigator.getGamepads ? navigator.getGamepads() : [];
  for (const gp of gps) {
    if (!gp) continue;
    physicalConnected = true;
    // Standard mapping
    state.buttons.a = gp.buttons[0]?.pressed ? 1 : 0;
    state.buttons.b = gp.buttons[1]?.pressed ? 1 : 0;
    state.buttons.x = gp.buttons[2]?.pressed ? 1 : 0;
    state.buttons.y = gp.buttons[3]?.pressed ? 1 : 0;
    state.buttons.lb = gp.buttons[4]?.pressed ? 1 : 0;
    state.buttons.rb = gp.buttons[5]?.pressed ? 1 : 0;
    state.axes.lt = gp.buttons[6]?.value || 0;
    state.axes.rt = gp.buttons[7]?.value || 0;
    state.buttons.back = gp.buttons[8]?.pressed ? 1 : 0;
    state.buttons.start = gp.buttons[9]?.pressed ? 1 : 0;
    state.buttons.guide = gp.buttons[16]?.pressed ? 1 : 0;
    state.buttons.ls = gp.buttons[10]?.pressed ? 1 : 0;
    state.buttons.rs = gp.buttons[11]?.pressed ? 1 : 0;
    // D-pad
    state.axes.dx = (gp.buttons[15]?.pressed ? 1 : 0) - (gp.buttons[14]?.pressed ? 1 : 0);
    state.axes.dy = (gp.buttons[13]?.pressed ? 1 : 0) - (gp.buttons[12]?.pressed ? 1 : 0);
    // Sticks (apply deadzone)
    const dz = 0.08;
    state.axes.lx = Math.abs(gp.axes[0]) > dz ? gp.axes[0] : 0;
    state.axes.ly = Math.abs(gp.axes[1]) > dz ? gp.axes[1] : 0;
    state.axes.rx = Math.abs(gp.axes[2]) > dz ? gp.axes[2] : 0;
    state.axes.ry = Math.abs(gp.axes[3]) > dz ? gp.axes[3] : 0;
    break;
  }
}

// --- On-screen controls ---
function setupTouch(id, btnName) {
  const el = document.getElementById(id);
  if (!el) return;
  const press = () => { state.buttons[btnName] = 1; el.classList.add('pressed'); };
  const release = () => { state.buttons[btnName] = 0; el.classList.remove('pressed'); };
  el.addEventListener('touchstart', e => { e.preventDefault(); press(); });
  el.addEventListener('touchend', e => { e.preventDefault(); release(); });
  el.addEventListener('mousedown', press);
  el.addEventListener('mouseup', release);
  el.addEventListener('mouseleave', release);
}

function setupDpad(id, axis, val) {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('touchstart', e => { e.preventDefault(); state.axes[axis] = val; el.classList.add('pressed'); });
  el.addEventListener('touchend', e => { e.preventDefault(); state.axes[axis] = 0; el.classList.remove('pressed'); });
  el.addEventListener('mousedown', () => { state.axes[axis] = val; el.classList.add('pressed'); });
  el.addEventListener('mouseup', () => { state.axes[axis] = 0; el.classList.remove('pressed'); });
  el.addEventListener('mouseleave', () => { state.axes[axis] = 0; el.classList.remove('pressed'); });
}

function setupStick(zoneId, knobId, axisX, axisY) {
  const zone = document.getElementById(zoneId);
  const knob = document.getElementById(knobId);
  if (!zone || !knob) return;
  let active = false, touchId = null;
  const rect = () => zone.getBoundingClientRect();
  const update = (cx, cy) => {
    const r = rect();
    let dx = (cx - r.left - r.width/2) / (r.width/2);
    let dy = (cy - r.top - r.height/2) / (r.height/2);
    const mag = Math.sqrt(dx*dx + dy*dy);
    if (mag > 1) { dx /= mag; dy /= mag; }
    state.axes[axisX] = Math.round(dx * 100) / 100;
    state.axes[axisY] = Math.round(dy * 100) / 100;
    knob.style.transform = `translate(calc(-50% + ${dx*35}px), calc(-50% + ${dy*35}px))`;
  };
  const reset = () => {
    active = false; touchId = null;
    state.axes[axisX] = 0; state.axes[axisY] = 0;
    knob.style.transform = 'translate(-50%, -50%)';
  };
  zone.addEventListener('touchstart', e => { e.preventDefault(); active = true; touchId = e.changedTouches[0].identifier; update(e.changedTouches[0].clientX, e.changedTouches[0].clientY); });
  zone.addEventListener('touchmove', e => { e.preventDefault(); for (const t of e.changedTouches) { if (t.identifier === touchId) update(t.clientX, t.clientY); }});
  zone.addEventListener('touchend', e => { e.preventDefault(); for (const t of e.changedTouches) { if (t.identifier === touchId) reset(); }});
  zone.addEventListener('mousedown', e => { active = true; update(e.clientX, e.clientY); });
  document.addEventListener('mousemove', e => { if (active) update(e.clientX, e.clientY); });
  document.addEventListener('mouseup', () => { if (active) reset(); });
}

// --- Setup ---
setupTouch('btn-a', 'a'); setupTouch('btn-b', 'b');
setupTouch('btn-x', 'x'); setupTouch('btn-y', 'y');
setupTouch('lb', 'lb'); setupTouch('rb', 'rb');
setupTouch('btn-back', 'back'); setupTouch('btn-start', 'start');
setupTouch('btn-guide', 'guide');
setupDpad('dup', 'dy', -1); setupDpad('ddown', 'dy', 1);
setupDpad('dleft', 'dx', -1); setupDpad('dright', 'dx', 1);
setupStick('stick-left', 'knob-left', 'lx', 'ly');
setupStick('stick-right', 'knob-right', 'rx', 'ry');

// --- Trigger touch (slide) ---
['lt','rt'].forEach(id => {
  const el = document.getElementById(id);
  const fill = document.getElementById(id+'-fill');
  if (!el) return;
  const axis = id;
  el.addEventListener('touchstart', e => { e.preventDefault(); state.axes[axis] = 1; fill.style.width='100%'; });
  el.addEventListener('touchend', e => { e.preventDefault(); state.axes[axis] = 0; fill.style.width='0%'; });
  el.addEventListener('mousedown', () => { state.axes[axis] = 1; fill.style.width='100%'; });
  el.addEventListener('mouseup', () => { state.axes[axis] = 0; fill.style.width='0%'; });
  el.addEventListener('mouseleave', () => { state.axes[axis] = 0; fill.style.width='0%'; });
});

// --- Send loop ---
async function sendLoop() {
  while (true) {
    pollPhysical();

    // Update visual for trigger fills from physical
    document.getElementById('lt-fill').style.width = (state.axes.lt * 100) + '%';
    document.getElementById('rt-fill').style.width = (state.axes.rt * 100) + '%';

    const t0 = performance.now();
    try {
      const r = await fetch(API, { method:'POST', headers:{'Content-Type':'application/json'},
                                    body:JSON.stringify(state), keepalive:true });
      if (r.ok) {
        const lat = Math.round(performance.now() - t0);
        document.getElementById('latency').textContent = lat;
        const el = document.getElementById('status');
        el.className = 'status ' + (physicalConnected ? 'physical' : 'connected');
        el.textContent = physicalConnected ? '🎮 Physical Controller Connected' : '✅ On-Screen Controls Active';
      }
    } catch(e) {
      document.getElementById('status').className = 'status disconnected';
      document.getElementById('status').textContent = '❌ Disconnected';
    }

    sendCount++;
    if (Date.now() - lastHz > 1000) {
      document.getElementById('hz').textContent = sendCount;
      sendCount = 0; lastHz = Date.now();
    }

    await new Promise(r => setTimeout(r, 8)); // ~120Hz
  }
}

// Gamepad connection events
window.addEventListener('gamepadconnected', e => {
  physicalConnected = true;
  console.log('Gamepad connected:', e.gamepad.id);
});

sendLoop();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# 3. HTTP Server
# ---------------------------------------------------------------------------
class ColabPadHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Silence request logs

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(WEB_UI.encode("utf-8"))
        elif self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "gamepad": GAMEPAD is not None}).encode())
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/state":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body)
                apply_state(data)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_response(400)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ---------------------------------------------------------------------------
# 4. Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ColabPad — Virtual Xbox 360 Controller")
    parser.add_argument("--port", type=int, default=9876, help="HTTP port (default: 9876)")
    args = parser.parse_args()

    print("[ColabPad] Creating virtual Xbox 360 Controller...", flush=True)
    dev_path = create_gamepad()
    if dev_path:
        print(f"[ColabPad] ✓ Gamepad active at {dev_path}", flush=True)
    else:
        print("[ColabPad] ✓ Gamepad created (device node pending)", flush=True)

    # Verify Sunshine can't see uinput
    import subprocess
    p = subprocess.run("su - user -c 'test -r /dev/uinput && echo YES || echo NO'",
                      shell=True, stdout=subprocess.PIPE)
    uinput_visible = p.stdout.decode().strip()
    if uinput_visible == "NO":
        print("[ColabPad] ✓ /dev/uinput hidden from Sunshine (XTest mouse/keyboard safe)", flush=True)
    else:
        print("[ColabPad] ⚠ WARNING: Sunshine can see /dev/uinput!", flush=True)

    server = HTTPServer(("0.0.0.0", args.port), ColabPadHandler)
    print(f"[ColabPad] ✓ Web gamepad UI on http://0.0.0.0:{args.port}/", flush=True)
    print(f"[ColabPad] Ready — connect a controller or use on-screen controls", flush=True)

    def cleanup(sig, frame):
        print("\n[ColabPad] Shutting down...", flush=True)
        if GAMEPAD:
            GAMEPAD.close()
        os.system("rm -f /dev/uinput")
        os.system("rm -f /dev/input/event* /dev/input/js* 2>/dev/null || true")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        cleanup(None, None)


if __name__ == "__main__":
    main()

