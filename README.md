# Colab Cloud Gaming

High-performance, ultra-low-latency game and desktop streaming on Google Colab (Tesla T4 GPU) using Sunshine, Moonlight, hardware Xorg with NvFBC capture, and Tailscale DERP networking.

---

### Features & Roadmap

- [x] Full Windows 11 Fluent Dark GUI (Centered Tint2 Taskbar, Rofi Start Menu, Desktop Icons, Rounded Windows, Papirus icons, B00merang Windows 11 theme)
- [x] Interactive startup options: Tesla T4 Standard (Default) vs High-RAM & Pure Game Mode (Default) vs Windows 11 GUI
- [x] 100% Pure native Xorg XTest driverless input pipeline (0ms input latency, freeze-immune mouse & keyboard)
- [x] High-resolution NVIDIA GeForce gaming desktop wallpaper
- [x] Hardware Xorg display server with direct NvFBC VRAM capture at 60 FPS
- [x] Custom 128-byte VESA EDID unlocking native 1920x1080, 1366x768, and 1280x720 resolutions
- [x] Hardware WebGPU and Vulkan acceleration in Google Chrome (Dawn engine)
- [x] Locked Tailscale DERP relay mode eliminating 5000ms saw-tooth UDP ping spikes
- [x] Low-latency NVENC P1 CBR streaming tuned for 6–8 Mbps with zero relay bufferbloat
- [x] Low-latency PulseAudio audio subsystem (2ms fragment buffer, UID 1001 user isolation)
- [x] Public HTTPS access via Tailscale Funnel (no Cloudflare, friends need no client app)
- [x] Native ultra-low-latency X11 XTest mouse and keyboard input (0ms driverless input pipeline)
- [x] Asynchronous multi-threaded parallel deployment engine (50%+ faster bootstrap)
- [x] Official MoonlightWeb web client (`moonlightweb.top`) with one-time Access PIN login per device
- [x] Interactive live CLI console (`/pin`, `/pair`, `/status`, `/restart`, `/links`, `/pulse`, `/accel`, `/unfreeze`)
- [x] Headless PyTorch 12MB VRAM GPU keepalive pulse loop to prevent Colab idle timeouts
- [x] One-click Windows automation launchers (`start.bat`, `pair.bat`, `push.bat`)

---

## How It Works

```
[Windows Host Machine]
   │
   ├── start.bat ──> runner.py
   │                    │
   │                    ├── 1. Provisions / Attaches to Colab GPU session ('t4')
   │                    ├── 2. Executes setup_remote.py on the instance
   │                    ├── 3. Reads Tailscale Funnel URL + MoonlightWeb Access PIN
   │                    └── 4. Runs background PyTorch keepalive pulse (12MB VRAM / 120s)
   │
[Google Colab Instance (Tesla T4 GPU, Ubuntu 24.04)]
   │
   ├── Hardware Xorg (:1) ─── Custom EDID (1080p / 768p / 720p)
   │     │
   │     ├── Openbox Window Manager (<5MB RAM overhead)
   │     ├── Google Chrome (Dawn WebGPU Engine over Vulkan ANGLE)
   │     └── Steam 32-bit
   │
   ├── Audio Subsystem
   │     └── PulseAudio (sink-sunshine-stereo, 2ms buffer, non-root user)
   │
   ├── Video Capture & Encoding
   │     └── Sunshine Server (NvFBC direct VRAM capture -> NVENC P1 CBR 6-8 Mbps)
   │
   └── Network & Tunnels
         ├── Tailscale WireGuard (Locked to DERP relay for stable 80-110ms latency)
         ├── Tailscale Funnel (public HTTPS -> MoonlightWeb :8090)
         └── MoonlightWeb (:8090, official moonlightweb.top client)
```

### Technical Details

1. **Hardware Xorg & NvFBC Capture**  
   Instead of using software framebuffers (`Xvfb`) which suffer from clock drift and CPU-bound `XGetImage` capture, the instance runs a real hardware Xorg server bound to the Tesla T4 GPU. Sunshine captures video directly from GPU memory using NVIDIA FrameBuffer Capture (NvFBC) with locked 60 FPS VSync.

2. **Custom VESA EDID**  
   Headless GPUs default to fallback 4:3 1024x768 resolutions without an attached monitor. A custom 128-byte VESA EDID binary is injected at `/etc/X11/edid.bin` and assigned to virtual display `DVI-D-0`. This exposes native 1920x1080 (FHD), 1366x768 (WXGA), and 1280x720 (HD) 16:9 modes.

3. **Chrome WebGPU & Vulkan Acceleration**  
   Chromium's Linux driver blocklist normally forces software rendering on server GPUs. The Chrome launcher is patched with hardware flags (`--enable-features=Vulkan,DefaultANGLEVulkan,WebGPUService --enable-unsafe-webgpu --use-angle=vulkan`), enabling the Dawn WebGPU engine directly on Tesla T4 Vulkan 1.3 drivers.

4. **Anti-Jitter DERP Relay Streaming**  
   Direct peer-to-peer UDP hole-punching between Google Cloud Symmetric NAT and mobile Carrier-Grade NAT (CGNAT) fails, causing 5-second connection flapping cycles. By forcing Tailscale into DERP relay mode (`TS_DEBUG_ALWAYS_USE_DERP=true`) and capping the encoder bitrate to 6–8 Mbps (`min_bitrate = 2000`, `max_bitrate = 8000`, `packet_size = 1200`), latency stays flat at ~80–110ms with zero packet loss or bufferbloat.

5. **Tailscale Funnel & MoonlightWeb Access PIN**  
   Instead of a Cloudflare quick tunnel (which hands out a random subdomain on every boot and breaks Sunshine's
   `csrf_allowed_origins`), the instance publishes MoonlightWeb through `tailscale funnel --bg --yes 8090`. The
   funnel URL is stable for the node (`https://<node>.<tailnet>.ts.net`) and is injected into
   `csrf_allowed_origins` in `sunshine.conf` before Sunshine starts. Remote browsers must enter a single-use
   Access PIN (issued by `MoonlightWeb --new-pin`) once per device.

6. **Automated Keepalive Pulse**  
   To prevent Google Colab from terminating the instance due to kernel inactivity, `runner.py` executes a minimal PyTorch tensor computation (~12MB VRAM) every 120 seconds.

---

## Components Used

| Component | Role | Details |
| :--- | :--- | :--- |
| **Sunshine** | Game Stream Server | Low-latency NVENC P1 CBR encoding with NvFBC VRAM capture |
| **MoonlightWeb** | Web Stream Client | Official `moonlightweb.top` client served over Tailscale Funnel on `:8090` |
| **Tailscale** | Low-Latency Mesh | DERP WireGuard relay mode for stable cross-NAT streaming |
| **Tailscale Funnel** | Public Access | Public HTTPS endpoint that publishes MoonlightWeb on port 8090 |
| **Openbox** | Window Manager | Ultra-lightweight X11 WM (<5 MB RAM usage) |
| **Google Chrome** | Web Browser | Hardware-accelerated Vulkan and WebGPU Dawn engine |
| **Steam** | Game Platform | 32-bit native Linux client |
| **PulseAudio** | Audio Engine | Low-latency 2ms virtual sink (`sink-sunshine-stereo`) |
| **PyTorch** | Keepalive | Lightweight 12MB VRAM tensor loop every 2 minutes |

---

## Installation & Requirements

### Prerequisites
1. **Windows Host PC**: Python 3.10+ installed and added to your system PATH.
2. **Colab CLI**: Installed and authenticated with your Google account:
   ```cmd
   pip install colab-cli
   colab login
   ```
3. **Moonlight Client**: Download [Moonlight](https://moonlight-stream.org/) on your phone, tablet, or secondary PC.

---

## Usage

### 1. Launch the Cloud Gaming Instance
Double-click `start.bat` (or execute in terminal):
```cmd
start.bat
```
The script will interactively guide you:
- **Hardware Profile**: Choose between `Tesla T4 High-RAM` (~54GB RAM for accounts with Colab Compute Units) or `Tesla T4 Standard` (~12GB RAM for free accounts).
- **Desktop Environment**: Choose between `Full Windows 11 Fluent GUI` (Centered taskbar, Start Menu, Desktop shortcuts, NVIDIA wallpaper, rounded windows) or `Pure Game Mode` (headless ultra-lean Openbox for maximum FPS and 0ms latency).
- Automatically allocate and configure the Colab GPU runtime (`t4`).
- Deploy all drivers, Xorg, audio, Chrome, Steam, Sunshine, and desktop daemons.
- Print the live Tailscale Funnel URL, the MoonlightWeb Access PIN, and Moonlight pairing codes in the terminal.
- Prompt whether to enable Tailscale for ultra-low latency streaming (Recommended: `Y`).
- Start the automated keepalive pulse loop.

### 2. Connect via Moonlight

#### Option A: Ultra-Low Latency via Tailscale (Recommended)
1. Install Tailscale on your client device (phone or PC) under the same Tailscale account.
2. When prompted by `start.bat`, authorize the Colab node if needed.
3. Open Moonlight on your client device and enter the Colab Tailscale IP (printed in the terminal, e.g. `100.x.y.z`).

#### Option B: Browser-Based Streaming (official MoonlightWeb)
- Open the **Tailscale Funnel** URL printed by the launcher (or run `/funnel`) in any modern browser — no app and no account needed.
- The page asks for a **one-time Access PIN**: type `/pin` in the launcher to read it, or `/pin new` to mint a fresh PIN for each new device.
- The PIN is single-use: each browser/device consumes one PIN and stays authorized afterwards.
- This is the official MoonlightWeb client from [moonlightweb.top](https://moonlightweb.top/) (`linckosz/moonlight-web`) on port 8090.

### 3. Pair Moonlight with Sunshine
1. When you select the host in Moonlight, a 4-digit PIN is displayed.
2. In the active launcher window, simply type:
   ```text
   /pair 1234
   ```
   *(Or double-click `pair.bat 1234` from Windows Explorer)*.
3. Moonlight will pair immediately and display your desktop and applications.

### 4. Interactive Live Terminal Commands
While the background keep-alive pulse keeps your GPU warm, you can run commands directly in the launcher prompt:
- `/pair <PIN>` : Pair a new Moonlight client device instantly (e.g. `/pair 1234`).
- `/pin`        : Show the current MoonlightWeb one-time Access PIN for web login.
- `/pin new`    : Mint a fresh single-use Access PIN for a new browser or device.
- `/funnel`     : Publish MoonlightWeb publicly through Tailscale Funnel.
- `/accel`      : Live GPU verification card (OpenGL 4.6, Vulkan 1.4, NVENC, NvFBC).
- `/unfreeze`   : Release any stuck Shift/Ctrl/Alt keys and unfreeze X11 input queue.
- `/links`      : Re-print the dashboard of active streaming & desktop URLs.
- `/status`     : Query real-time GPU telemetry, VRAM usage, and streaming sessions.
- `/restart`    : Cleanly restart Sunshine & Openbox without resetting the Colab VM.
- `/tailscale`  : Query Tailscale connection state and Colab node IP.
- `/pulse`      : Manually trigger an immediate PyTorch VRAM pulse.
- `/help`       : Display the command menu.
- `/exit`       : Cleanly stop keepalive and exit.

### 5. Recommended Moonlight Client Settings
To ensure stutter-free streaming over the relay:
- **Resolution**: `1920x1080` or `1280x720` @ 60 FPS
- **Bitrate**: `6.0 Mbps` to `8.0 Mbps` (avoids relay bufferbloat)
- **Frame Pacing**: Prefer lowest latency
- **Video Codec**: H.264 or HEVC (H.265)

---

## Repository Files

- `start.bat`: One-click Windows launcher.
- `runner.py`: Host orchestrator, URL parser, Tailscale watcher, and keepalive loop with hardware and desktop selectors.
- `setup_remote.py`: Automated remote installation, Windows 11 Fluent desktop, and Sunshine daemon configuration.
- `pair.bat` / `pair.py`: One-click Moonlight pairing utility.
- `check_ts.py`: Helper script to query remote Tailscale status and IP.
- `sync_ts_ip.py`: Helper script to synchronize active Tailscale IP into Sunshine.
- `read_links.py`: Helper to read the Tailscale Funnel URL, node IP and MoonlightWeb PIN.
- `mw_pin.py`: Generates / reads the MoonlightWeb one-time Access PIN (also starts the server if needed).
- `start_ts_funnel.py`: Enables and reports the Tailscale Funnel endpoint.
- `keepalive_code.py`: Headless PyTorch GPU keepalive snippet.
- `push.bat`: One-click Git push script for repo maintenance.
