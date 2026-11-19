# Colab Cloud Gaming

High-performance, ultra-low-latency game and desktop streaming on Google Colab (Tesla T4 GPU) using Sunshine, Moonlight, hardware Xorg with NvFBC capture, and Tailscale DERP networking.

---

### Features & Roadmap

- [x] Full Windows 11 Fluent Dark GUI (Centered Tint2 Taskbar, Rofi Start Menu, Desktop Icons, Rounded Windows, Papirus icons, B00merang Windows 11 theme)
- [x] Interactive startup options: Tesla T4 Standard (Default) vs High-RAM & Pure Game Mode (Default) vs Windows 11 GUI
- [x] Region-aware session allocation: Auto (Singapore-first) / US / EU / SG / Any with stop+retry loop (Colab has no region flag); zone is read from the runtime URL, and any live VM is re-adopted instead of destroyed
- [x] 100% Pure native Xorg XTest driverless input pipeline (0ms input latency, freeze-immune mouse & keyboard)
- [x] High-resolution NVIDIA GeForce gaming desktop wallpaper
- [x] Hardware Xorg display server with direct NvFBC VRAM capture at 60 FPS
- [x] Custom 128-byte VESA EDID unlocking native 1920x1080, 1366x768, and 1280x720 resolutions
- [x] Hardware WebGPU and Vulkan acceleration in Google Chrome (Dawn engine)
- [x] Locked Tailscale DERP relay mode eliminating 5000ms saw-tooth UDP ping spikes
- [x] Low-latency NVENC P1 CBR streaming tuned for up to 50 Mbps with zero relay bufferbloat
- [x] Low-latency PulseAudio audio subsystem (2ms fragment buffer, UID 1001 user isolation)
- [x] Public HTTPS access via Tailscale Funnel (no Cloudflare, friends need no client app)
- [x] Native ultra-low-latency X11 XTest mouse and keyboard input (0ms driverless input pipeline)
- [x] Asynchronous multi-threaded parallel deployment engine (50%+ faster bootstrap)
- [x] Official MoonlightWeb web client (`moonlightweb.top`) with one-time Access PIN login per device
- [x] Interactive live CLI console (`/pin`, `/pair`, `/status`, `/restart`, `/links`, `/pulse`, `/accel`, `/unfreeze`)
- [x] Headless PyTorch 12MB VRAM GPU keepalive pulse loop to prevent Colab idle timeouts
- [x] One-click Windows automation launchers (`start.bat`, `logout.bat`, `pair.bat`, `push.bat`)

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
   │     └── Sunshine Server (NvFBC direct VRAM capture -> NVENC P1 CBR 2-50 Mbps)
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
   Direct peer-to-peer UDP hole-punching between Google Cloud Symmetric NAT and mobile Carrier-Grade NAT (CGNAT) fails, causing 5-second connection flapping cycles. By forcing Tailscale into DERP relay mode (`TS_DEBUG_ALWAYS_USE_DERP=true`) and shaping the encoder (`min_bitrate = 2000`, `max_bitrate = 50000`, `packet_size = 1024`), latency stays flat at ~80–110ms. Keep the client bitrate at 20–50 Mbps over DERP and lower it only if you see packet loss.

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
- **Datacenter Region**: `Auto` (Singapore-first) / `Singapore` / `United States` / `Europe` / `Any`. See the region note below.
- **Desktop Environment**: Choose between `Full Windows 11 Fluent GUI` (Centered taskbar, Start Menu, Desktop shortcuts, NVIDIA wallpaper, rounded windows) or `Pure Game Mode` (headless ultra-lean Openbox for maximum FPS and 0ms latency).
- **Optional game**: Install GTA V with Steam integration, or skip.
- Automatically allocate and configure the Colab GPU runtime (`t4`).
- Deploy all drivers, Xorg, audio, Chrome, Steam, Sunshine, and desktop daemons.
- Prompt whether to enable Tailscale for ultra-low latency streaming (Recommended: `Y`).
- After Tailscale authorization, automatically publish MoonlightWeb through Tailscale Funnel and mint a fresh one-time **web Access PIN**.
- Print the live Funnel URL, the Access PIN, and Sunshine credentials as a dashboard.

> [!IMPORTANT]
> **Colab has no region flag.** `colab new` exposes only `--gpu`, `--tpu`, `--high-mem` and
> `--session`. The datacenter zone is assigned by Google. Region preference is therefore
> implemented as *stop → wait ~20 s → re-allocate*, retried up to 8 times until the
> requested family appears.
>
> **The zone is read from the runtime URL, not guessed from the id.** The URL is the only
> trustworthy source:
>
> ```text
> https://8080-gpu-t4-s-ass1c2-222r7xhmk9xse-c.asia-southeast1.prod.r.colab.dev
>                                              ^^^^^^^^^^^^^^^^ zone
> ```
>
> `colab_zone.py` extracts it with `\.([a-z]{2,}-[a-z]+\d)\.prod\.` and maps the family:
> `asia-southeast*` → **sg**, `us-east*` / `us-west*` / `us-central*` → **us**,
> `europe-*` → **eu**.
>
> Earlier versions decoded the abbreviation *inside the runtime id* instead. That was the
> bug behind "a Singapore VM is reported as unknown": `ass1c2` is the real token for
> **asia-southeast1**, and it was absent from the table (which even listed the wrong
> spellings `asse1` / `apse1`). Id-abbreviation guessing has been removed entirely.
>
> If every attempt misses, the launcher **keeps the last working VM** and warns instead of
> exiting. An unnameable zone is treated as acceptable — a preference never tears down a
> live machine.

> [!WARNING]
> **Orphaned server assignments — two shapes.** If an allocation is interrupted, or a
> `colab sessions` call prunes the local record, the Colab server keeps an assignment while
> `~/.config/colab-cli/sessions.json` is missing or stale. `colab exec -s t4` then fails with
> **"Session 't4' not found"** even though the VM is alive and billing, `colab stop` answers
> *"not found"*, and a new allocation is refused with `precondition failed`.
>
> `colab sessions` shows two different orphan shapes, and **both must be adopted**:
>
> | Shape | Cause |
> |---|---|
> | `[?] gpu-t4-s-...` | server holds an assignment the local store cannot name |
> | `[ab12cd] gpu-t4-s-...` | `colab new` without `-s` registered it under a random 6-hex name |
>
> Checking only for `[?]` left the second shape unmatched — that produced the repeating
> `A server assignment exists but cannot be adopted as 't4'` warning. `needs_adoption()` now
> fires whenever any assignment exists whose name is not `t4`.
>
> The server's assignment record carries `runtimeProxyInfo.token`, `.url` and `.expires_at`,
> which is everything a local session entry needs — so the launcher **re-adopts** the VM
> instead of destroying it:
>
> | Helper | Effect |
> |---|---|
> | `colab_adopt.py` | Re-registers **either** orphan shape under `t4` (non-destructive); `--endpoint <id>` targets a specific VM, `--list` shows candidates |
> | `colab_free.py` | `--free --name t4` releases every assignment **except** `t4` (this is what clears a `412`); `--all` releases everything; no flag = dry-run list. **Destroys the released VMs.** |
>
> `adopt_orphans()` runs before every allocation and before deployment (`ensure_deployable()`),
> so a live VM is never orphaned into unusability. `/adopt` does it on demand; `/free` is
> destructive and only for a deliberate discard.
>
> Detection uses the bracketed name (`^\[name\]`), never a substring search — `"t4"` is a
> substring of both `gpu-t4-s-...` and `Hardware: T4`, which previously made the launcher
> believe a session existed when it did not. Crucially, a server row alone is **not** treated
> as ready: `colab exec` only resolves names from the local store, so an un-adopted orphan
> would deploy to nothing.
>
> The row parser also rejects the CLI's own log lines. With no sessions at all,
> `colab sessions` prints `[colab] No sessions found.` — the same bracket shape, which parsed
> as a phantom row named `colab` at endpoint `No`. That kept `has_any_session()` true forever,
> so allocation never ran and `stop_session()` reported failure after a successful stop. A
> genuine row must carry a `Hardware:` field or a `gpu-*`/`tpu-*` runtime id.

> [!WARNING]
> **A silent console = a wedged kernel, not a dead VM.** `colab exec` drives **one
> persistent Jupyter kernel per session**. If that kernel stops responding — a cell that never
> finishes, a daemon holding its output stream open — then *every* later `exec` waits on it
> forever. The launcher goes quiet right after `--- TAILSCALE MESH NETWORK STATUS ---`, prints
> no error, and accepts no input, while the VM stays alive and billing.
>
> Two independent defects made this unrecoverable, both now fixed:
>
> | Defect | Fix |
> |---|---|
> | `run_cmd` had **no timeout** — one wedged exec blocked the launcher permanently | Every call is time-bounded and returns `124` on expiry |
> | `subprocess.run(timeout=)` does not work on Windows — it kills only `cmd.exe`, the orphaned grandchild keeps the pipe open (measured: `timeout=3` still blocked for 30s) | Output is captured into temp **files** (nothing to keep open) and on expiry the whole tree is killed with `taskkill /F /T` |
>
> Measured after the fix: `timeout=3` on a 30-second sleep returns in **3.2s**. `run_exec()`
> also serializes all execs behind a lock — the keepalive thread and the console must never
> drive the same kernel at once, because two concurrent execs queue rather than run in parallel.
> On a wedge `run_exec` clears the cached `kernel_id` (which makes the CLI boot a fresh kernel
> on the same VM) and retries once.
>
> **If the console ever goes silent:** run `/kernel`, or clear `kernel_id` / `session_id` from
> `~/.config/colab-cli/sessions.json`. Verified: a `print('PONG')` that had been timing out for
> minutes returned in 5.4s immediately after the cached `kernel_id` was dropped. Do **not**
> re-allocate — the VM is fine.
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
- `/region`     : Show current datacenter region and zone (read from the runtime URL).
- `/region sg`  : Stop the session and re-allocate until Singapore is assigned.
- `/region us`  : Stop and re-allocate until a US zone is assigned.
- `/region eu`  : Stop and re-allocate until an EU zone is assigned.
- `/region any` : Accept whatever region Colab gives (no retry).
- `/adopt`      : Re-attach a live VM that is registered under another name (non-destructive).
- `/kernel`     : Reset a wedged remote kernel — fixes a console that has gone silent.
- `/free`       : Release every server assignment **except `t4`** — this destroys those VMs.
- `/pulse`      : Manually trigger an immediate PyTorch VRAM pulse.
- `/gtav`       : Install GTA V (Wine + DXVK + Steam shortcut) if not done at startup.
- `/gtav add`   : Re-register the Steam non-Steam shortcut (run once after logging into Steam).
- `/gtav play`  : Launch GTA V on the desktop immediately.
- `/gtav log`   : Tail the GTA V installer log.
- `/help`       : Display the command menu.
- `/exit`       : Cleanly stop keepalive and exit.

### 4b. Installing GTA V (optional)

Startup menu option **3 → 2** installs Grand Theft Auto V automatically after the
desktop is up. It runs these stages (usable individually with `GTA_STEP=`):

| Stage | What it does |
|-------|--------------|
| `wine` | Installs Wine 9.0 + `wine32:i386` + Vulkan ICDs (`vulkan-tools`, mesa drivers) |
| `verify` | Proves hardware acceleration: `nvidia-smi`, `xorg.conf` nvidia driver, `glxinfo` renderer, `vulkaninfo` device list (no lavapipe), a real `h264_nvenc` test encode, and Sunshine `NvFBC`+`nvenc` in its log |
| `download` | Fetches the 13.7 GB repack from Google Drive, resumable, size-verified |
| `extract` | `7z x` to `/content/games/GTAV` (~15 GB, 8–15 min, solid LZMA2) |
| `stage` | Moves the game to `/content/SteamLibrary/steamapps/common/Grand Theft Auto V` and writes `libraryfolders.vdf` |
| `add` | Copies bundled DXVK next to `PlayGTAV.exe`, writes `gta_launch.sh`, registers the Steam shortcut, Sunshine app, and desktop launcher |

Run it standalone on the VM:

```bash
python3 gta_setup.py                # full install
GTA_STEP=verify python3 gta_setup.py     # prove T4 hardware acceleration
GTA_STEP=add  python3 gta_setup.py  # only re-register in Steam
GTA_STEP=download python3 gta_setup.py   # resume an interrupted download
GTA_FILE_ID=<drive_id> python3 gta_setup.py   # use a different Drive copy
GTA_KEEP_7Z=1 python3 gta_setup.py  # keep the archive
```

> [!IMPORTANT]
> Steam must be **logged in at least once** before the shortcut can be written
> into `userdata/<id>/config/shortcuts.vdf`. If you install GTA V before logging
> in, the shortcut is saved to `/content/steam_shortcut_pending/` — log into Steam,
> then run `/gtav add` to apply it.

Then launch it from **Steam → Library → Grand Theft Auto V**, the desktop icon, or
`bash /content/gta_launch.sh`. The Sunshine app list also gets a
`Grand Theft Auto V` entry for streaming.

> [!NOTE]
> **Rendering is genuinely hardware-accelerated on the T4.** The Xorg server is bound to
> the NVIDIA driver (`BusID PCI:0:4:0`), `glxinfo` reports `Tesla T4/PCIe/SSE2` with
> OpenGL 4.6, `vulkaninfo` lists only `Tesla T4` devices, and Sunshine captures via
> `NvFBC` and encodes via `nvenc`. The launcher additionally pins the ICD and filters the
> DXVK device (`VK_ICD_FILENAMES=nvidia_icd.json`, `DXVK_FILTER_DEVICE_NAME='Tesla T4'`,
> `LIBGL_ALWAYS_SOFTWARE=0`) so lavapipe/llvmpipe can never be selected.
> The only real limitation is that the T4 has **no display output** and Colab gives the
> VM a modest CPU/RAM budget, so high in-game settings still drop FPS — lower resolution
> and quality for a smooth stream. Verify any time with `GTA_STEP=verify python3 gta_setup.py`
> or `/accel`.

> [!WARNING]
> **Google Drive quota.** The repack is fetched from a shared Drive file
> (`GTA_FILE_ID`, default `1tifhdY5C7tqpLZ2C_PWYhHMOdq7vUcvT`). If that file has been
> downloaded too often, Drive serves a ~2 KB `Quota exceeded` HTML page instead of the
> 13.7 GB archive. The installer now detects this by checking the 7z magic bytes
> (`37 7A BC AF 27 1C`) and by sizing with a ranged `Content-Range` probe, so it fails
> loudly (`Download FAILED`) instead of trying to `7z x` an HTML page. If you hit it,
> either wait ~24 h or make your own copy in Drive and pass its id:
> `GTA_FILE_ID=<new_id> python3 gta_setup.py`.

### 5. Recommended Moonlight Client Settings
To ensure stutter-free streaming over the relay:
- **Resolution**: `1920x1080` or `1280x720` @ 60 FPS
- **Bitrate**: `20 Mbps` to `50 Mbps` (matches Sunshine's `max_bitrate = 50000`; lower it if the DERP relay shows packet loss)
- **Frame Pacing**: Prefer lowest latency
- **Video Codec**: `HEVC` (H.265) — falls back to `H.264` automatically

---

## Repository Files

- `start.bat`: One-click Windows launcher.
- `logout.bat` / `logout_colab.py`: One-click Google Colab logout & account switcher (clears Keyring OAuth tokens & session cache).
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
- `gta_setup.py`: One-shot GTA V installer — Wine 9 + DXVK, Google Drive download, 7z extract, Steam library staging, and non-Steam shortcut registration.
- `gta_status.py`: Reports GTA V install state (archive, game dir, Wine, DXVK, Steam shortcut, running processes).
- `colab_zone.py`: Prints one JSON line with the real zone, family, endpoint and URL of the registered session — the zone is parsed from the runtime URL (`.asia-southeast1.prod.r.colab.dev`), not from the id abbreviation.
- `colab_adopt.py`: Re-adopts a live VM into the local store under `t4` — handles both orphan shapes (`[?]` and `[leftover-name]`), drops duplicate/stale local names, `--endpoint` targets a specific VM, `--list` shows candidates. Non-destructive.
- `colab_free.py`: Lists server assignments and releases them. `--free --name t4` releases everything **except** `t4` (clears a `412`); `--all` releases everything. **Destroys the released VMs.**
