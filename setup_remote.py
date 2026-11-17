import subprocess
import os
import sys
import time
import json
import re

def run(cmd, check=True):
    print(f"--> [CMD] {cmd}", flush=True)
    res = subprocess.run(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print(res.stdout, flush=True)
    if check and res.returncode != 0:
        print(f"WARNING: Command failed with code {res.returncode}: {cmd}", flush=True)
    return res

os.makedirs("/var/log/services", exist_ok=True)
os.makedirs("/var/log/tunnels", exist_ok=True)

# 0. Anti-Jitter, Network Buffers & Ultra-Fast DNS
print("=== 0. Configuring Network Buffer Tuning & Fast DNS ===", flush=True)
try:
    with open('/etc/resolv.conf', 'w') as f:
        f.write("nameserver 1.1.1.1\nnameserver 1.0.0.1\nnameserver 8.8.8.8\noptions timeout:1 attempts:2 rotate\n")
    print("Configured ultra-fast Cloudflare & Google DNS in /etc/resolv.conf", flush=True)
except Exception as e:
    print(f"resolv.conf notice: {e}", flush=True)

sysctl_settings = [
    ("net.core.rmem_max", "33554432"),
    ("net.core.wmem_max", "33554432"),
    ("net.core.rmem_default", "8388608"),
    ("net.core.wmem_default", "8388608"),
    ("net.core.netdev_max_backlog", "10000"),
    ("net.ipv4.udp_rmem_min", "16384"),
    ("net.ipv4.udp_wmem_min", "16384"),
    ("net.core.default_qdisc", "fq"),
    ("net.ipv4.tcp_congestion_control", "bbr")
]
for key, val in sysctl_settings:
    subprocess.run(f"sysctl -w {key}={val} 2>/dev/null || true", shell=True)

# 1. NVIDIA & Vulkan / EGL config
print("=== 1. Setting up NVIDIA libraries, Vulkan ICD & EGL Vendor ===", flush=True)
with open('/etc/ld.so.conf.d/nvidia64.conf', 'w') as f:
    f.write('/usr/lib64-nvidia\n')
run('ldconfig', check=False)

os.makedirs('/usr/share/vulkan/icd.d', exist_ok=True)
with open('/usr/share/vulkan/icd.d/nvidia_icd.json', 'w') as f:
    json.dump({
        "file_format_version": "1.0.0",
        "ICD": {
            "library_path": "libGLX_nvidia.so.0",
            "api_version": "1.3.277"
        }
    }, f, indent=2)

os.makedirs('/usr/share/glvnd/egl_vendor.d', exist_ok=True)
with open('/usr/share/glvnd/egl_vendor.d/10_nvidia.json', 'w') as f:
    json.dump({
        "file_format_version": "1.0.0",
        "ICD": {
            "library_path": "libEGL_nvidia.so.0"
        }
    }, f, indent=2)

# 2. Base packages
print("=== 2. Updating APT and installing desktop & graphics packages ===", flush=True)
run("sudo add-apt-repository -y multiverse", check=False)
run("dpkg --add-architecture i386", check=False)
run("apt-get update -y", check=False)
pkgs = [
    "xserver-xorg-core", "x11-xserver-utils", "x11vnc", "openbox", "xterm", "thunar", "feh",
    "pulseaudio", "pulseaudio-utils",
    "vulkan-tools", "libvulkan1", "libvulkan-dev", "mesa-vulkan-drivers",
    "mesa-utils", "libgl1-mesa-dri", "libglvnd0", "libglvnd-dev",
    "ffmpeg", "libva2", "vainfo", "net-tools", "iproute2", "iptables"
]
run(f"DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends {' '.join(pkgs)}", check=False)

# Auto-accept Steam license and install Steam cleanly from Ubuntu repositories
run("echo steam steam/question select \"I AGREE\" | debconf-set-selections", check=False)
run("echo steam steam/license note '' | debconf-set-selections", check=False)
run("DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends steam:i386", check=False)

# 2.1 NVIDIA Userspace Driver (32-bit & 64-bit userland compatibility libraries)
print("=== 2.1 Installing NVIDIA Userspace Libraries (32-bit & 64-bit) ===", flush=True)
r_ver = subprocess.run("nvidia-smi --query-gpu=driver_version --format=csv,noheader", shell=True, text=True, capture_output=True)
driver_ver = r_ver.stdout.strip()
if driver_ver:
    installer_path = f"/tmp/NVIDIA-Linux-x86_64-{driver_ver}.run"
    if not os.path.exists(installer_path):
        url = f"https://us.download.nvidia.com/tesla/{driver_ver}/NVIDIA-Linux-x86_64-{driver_ver}.run"
        run(f"curl -fSL -o {installer_path} {url}", check=False)
        run(f"chmod +x {installer_path}", check=False)
    run(f"{installer_path} --no-kernel-module --ui=none --no-x-check --silent || true", check=False)

# Symlink NVIDIA Xorg driver modules for hardware acceleration
os.makedirs("/usr/lib/xorg/modules/drivers", exist_ok=True)
os.makedirs("/usr/lib/xorg/modules/extensions", exist_ok=True)
if os.path.exists("/usr/lib64-nvidia/xorg/modules/drivers/nvidia_drv.so"):
    run("ln -sf /usr/lib64-nvidia/xorg/modules/drivers/nvidia_drv.so /usr/lib/xorg/modules/drivers/nvidia_drv.so", check=False)
if os.path.exists("/usr/lib64-nvidia/xorg/modules/extensions/libglxserver_nvidia.so"):
    run("ln -sf /usr/lib64-nvidia/xorg/modules/extensions/libglxserver_nvidia.so /usr/lib/xorg/modules/extensions/libglxserver_nvidia.so", check=False)
run("ldconfig", check=False)

# 2.2 Download NVIDIA HD Wallpaper
print("=== 2.2 Downloading NVIDIA HD Wallpaper ===", flush=True)
wallpaper_path = "/root/nvidia-wallpaper.jpg"
if not os.path.exists(wallpaper_path):
    run(f"wget -q https://wallpapercat.com/w/full/3/b/2/1255774-2500x1341-desktop-hd-nvidia-wallpaper-photo.jpg -O {wallpaper_path}", check=False)

# 2.3 Setup dedicated user 'user' & controller / uinput permissions
print("=== 2.3 Setting up user permissions & gamepad device nodes ===", flush=True)
run("groupadd -f input", check=False)
run("id -u user || (useradd -m -s /bin/bash user && echo 'user:123456' | chpasswd)", check=False)
run("usermod -aG sudo,video,audio,input user", check=False)

# Virtual gamepad & input devices
run("modprobe uinput 2>/dev/null || true", check=False)
run("mknod /dev/uinput c 10 223 2>/dev/null || true", check=False)
run("chmod 666 /dev/uinput", check=False)
run("modprobe uhid 2>/dev/null || true", check=False)
run("mknod /dev/uhid c 10 239 2>/dev/null || true", check=False)
run("chmod 666 /dev/uhid", check=False)
run("mkdir -p /dev/input && chmod 777 /dev/input", check=False)

run("cp -f /root/nvidia-wallpaper.jpg /home/user/nvidia-wallpaper.jpg 2>/dev/null || true", check=False)
run("chown user:user /home/user/nvidia-wallpaper.jpg", check=False)
run("mkdir -p /run/user/1001/pulse && chown -R user:user /run/user/1001 && chmod 700 /run/user/1001", check=False)

# 3. Google Chrome installation & WebGPU / Vulkan patch
print("=== 3. Installing Google Chrome with WebGPU & Vulkan Acceleration ===", flush=True)
run("which google-chrome || (wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb && DEBIAN_FRONTEND=noninteractive apt-get install -y /tmp/chrome.deb && rm -f /tmp/chrome.deb)", check=False)

wrapper_path = "/opt/google/chrome/google-chrome"
gpu_flags = (
    '--no-sandbox '
    '--disable-dev-shm-usage '
    '--ignore-gpu-blocklist '
    '--enable-gpu-rasterization '
    '--enable-zero-copy '
    '--enable-features=Vulkan,DefaultANGLEVulkan,WebGPUService '
    '--enable-unsafe-webgpu '
    '--use-angle=vulkan'
)
if os.path.exists(wrapper_path):
    content = open(wrapper_path).read()
    new_exec = f'exec -a "$0" "$HERE/chrome" {gpu_flags} "$@"'
    content = re.sub(r'exec -a "\$0" "\$HERE/chrome".*', new_exec, content)
    with open(wrapper_path, "w") as f:
        f.write(content)

os.makedirs("/home/user/Desktop", exist_ok=True)
with open("/home/user/Desktop/google-chrome.desktop", "w") as f:
    f.write(f"[Desktop Entry]\nVersion=1.0\nName=Google Chrome (WebGPU)\nExec=/usr/bin/google-chrome-stable {gpu_flags} %U\nIcon=google-chrome\nTerminal=false\nType=Application\nCategories=Network;WebBrowser;\n")
subprocess.run("chmod +x /home/user/Desktop/google-chrome.desktop", shell=True)

# 4. Cloudflared
print("=== 4. Installing Cloudflare Tunnel (cloudflared) ===", flush=True)
run("which cloudflared || (wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -O /tmp/cloudflared.deb && DEBIAN_FRONTEND=noninteractive apt-get install -y /tmp/cloudflared.deb && rm -f /tmp/cloudflared.deb)", check=False)

# 5. Sunshine
print("=== 5. Installing Sunshine ===", flush=True)
sunshine_url = "https://github.com/LizardByte/Sunshine/releases/download/v2026.914.233613/sunshine_2026.914.233613-1+ubuntu24.04_amd64.deb"
run(f"which sunshine || (wget -q {sunshine_url} -O /tmp/sunshine.deb && DEBIAN_FRONTEND=noninteractive apt-get install -y /tmp/sunshine.deb && rm -f /tmp/sunshine.deb)", check=False)

# 6. Moonlight Web Stream
print("=== 6. Setting up Moonlight Web Stream ===", flush=True)
if not os.path.exists("/opt/moonlight-web/web-server"):
    mw_url = "https://github.com/MrCreativ3001/moonlight-web-stream/releases/download/v3.0.2/moonlight-web-x86_64-unknown-linux-gnu.tar.gz"
    run(f"mkdir -p /opt/moonlight-web && cd /opt/moonlight-web && wget -qO- {mw_url} | tar -xz --strip-components=1", check=False)
    run("chmod +x /opt/moonlight-web/web-server", check=False)
    run("cd /opt/moonlight-web && ./web-server config generate || true", check=False)

try:
    with open('/opt/moonlight-web/server/config.json', 'r') as f:
        cfg = json.load(f)
    cfg['web_server']['bind_address'] = '0.0.0.0:8090'
    with open('/opt/moonlight-web/server/config.json', 'w') as f:
        json.dump(cfg, f, indent=2)
except Exception as e:
    print("Error updating config.json:", e)

# 7. Tailscale
print("=== 7. Installing Tailscale ===", flush=True)
run("which tailscale || (curl -fsSL https://tailscale.com/install.sh | sh)", check=False)

# 8. noVNC
print("=== 8. Setting up noVNC Web Desktop ===", flush=True)
if not os.path.exists("/opt/noVNC"):
    run("git clone --depth 1 https://github.com/novnc/noVNC.git /opt/noVNC || true", check=False)
    run("git clone --depth 1 https://github.com/novnc/websockify /opt/noVNC/utils/websockify || true", check=False)
    run("ln -s /opt/noVNC/vnc.html /opt/noVNC/index.html || true", check=False)

# 9. Hardware EDID Binary & Xorg Setup (1920x1080, 1366x768, 1280x720)
print("=== 9. Configuring Hardware EDID (1080p, 1366x768, 720p) ===", flush=True)
hex_edid = (
    "00ffffffffffff003674300001000000"
    "0a190103803420782acf75a756529c27"
    "0f5054a54b008180818f819981c09500"
    "a9c0b3000101023a801871382d40582c"
    "450009252100001e662150b051001b30"
    "4070360009252100001e000000fd0032"
    "4c1e5311000a202020202020000000fc"
    "0047656e65726963204648440a2000"
)
raw_bytes = bytes.fromhex(hex_edid)
chk = (256 - (sum(raw_bytes) % 256)) % 256
full_edid = raw_bytes + bytes([chk])
os.makedirs("/etc/X11", exist_ok=True)
with open("/etc/X11/edid.bin", "wb") as f:
    f.write(full_edid)

xorg_conf = """
Section "ServerLayout"
    Identifier     "Layout0"
    Screen      0  "Screen0"
EndSection

Section "Module"
    Load           "glx"
EndSection

Section "Device"
    Identifier     "Device0"
    Driver         "nvidia"
    VendorName     "NVIDIA Corporation"
    BoardName      "Tesla T4"
    BusID          "PCI:0:4:0"
    Option         "ConnectedMonitor" "DVI-D-0"
    Option         "CustomEDID" "DVI-D-0:/etc/X11/edid.bin"
EndSection

Section "Screen"
    Identifier     "Screen0"
    Device         "Device0"
    DefaultDepth    24
    Option         "AllowEmptyInitialConfiguration" "True"
    Option         "ModeValidation" "NoMaxPClkCheck, NoEdidMaxPClkCheck, NoMaxSizeCheck, NoHorizSyncCheck, NoVertRefreshCheck, AllowNonEdidModes, NoVirtualSizeCheck"
    SubSection     "Display"
        Virtual     1920 1080
        Depth       24
    EndSubSection
EndSection
"""
with open("/etc/X11/xorg.conf", "w") as f:
    f.write(xorg_conf.strip() + "\n")

# 10. Start Services in Detached Daemon Mode
print("=== 10. Starting All Services in Detached Daemon Mode ===", flush=True)
env = os.environ.copy()
env["DISPLAY"] = ":1"

subprocess.run("killall -9 Xorg Xvfb x11vnc openbox sunshine web-server cloudflared tailscaled 2>/dev/null || true", shell=True)
time.sleep(1)
subprocess.run("rm -f /tmp/.X1-lock /tmp/.X11-unix/X1 /tmp/com.google.Chrome.* /root/.config/google-chrome/Singleton*", shell=True)

def spawn_daemon(args, log_name, env_override=None, cwd=None):
    f_log = open(f"/var/log/services/{log_name}", "a")
    proc_env = env_override if env_override else env
    p = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=f_log,
        stderr=f_log,
        start_new_session=True,
        env=proc_env,
        cwd=cwd
    )
    return p

# 10.1 Hardware Xorg with NvFBC VRAM Capture
print("--> Initializing Hardware Xorg display...", flush=True)
spawn_daemon(["/usr/lib/xorg/Xorg", ":1", "-seat", "seat-1", "-allowMouseOpenFail", "-novtswitch", "-nolisten", "tcp"], "xorg.log")
time.sleep(3)
run("DISPLAY=:1 xhost +", check=False)
run("DISPLAY=:1 xrandr --output DVI-D-0 --mode 1920x1080 || DISPLAY=:1 xrandr --output DVI-D-0 --mode 1360x768 || DISPLAY=:1 xrandr --output DVI-D-0 --mode 1280x720", check=False)

# 10.2 Low-Latency PulseAudio under user
print("--> Starting Low-Latency PulseAudio...", flush=True)
run('su - user -c "export XDG_RUNTIME_DIR=/run/user/1001; pulseaudio --kill 2>/dev/null || true"', check=False)
time.sleep(1)
run('su - user -c "export XDG_RUNTIME_DIR=/run/user/1001; pulseaudio --start --exit-idle-time=-1"', check=False)
time.sleep(1)
run('su - user -c "export XDG_RUNTIME_DIR=/run/user/1001; pactl load-module module-null-sink sink_name=sink-sunshine-stereo sink_properties=device.description=SunshineStereo || true"', check=False)
run('su - user -c "export XDG_RUNTIME_DIR=/run/user/1001; pactl set-default-sink sink-sunshine-stereo || true"', check=False)

# 10.3 Openbox & NVIDIA Wallpaper
print("--> Starting Openbox & Wallpaper...", flush=True)
for ob_dir in ["/home/user/.config/openbox", "/root/.config/openbox"]:
    os.makedirs(ob_dir, exist_ok=True)
    with open(f"{ob_dir}/autostart", "w") as f:
        f.write("#!/bin/sh\nfeh --bg-fill /home/user/nvidia-wallpaper.jpg &\n")
    run(f"chmod +x {ob_dir}/autostart", check=False)
run("chown -R user:user /home/user/.config/openbox", check=False)

run('su - user -c "DISPLAY=:1 nohup openbox > /home/user/openbox.log 2>&1 &"', check=False)
time.sleep(1)
run('su - user -c "DISPLAY=:1 feh --bg-fill /home/user/nvidia-wallpaper.jpg &"', check=False)
run('DISPLAY=:1 feh --bg-fill /home/user/nvidia-wallpaper.jpg &', check=False)

# 10.4 Google Chrome Maximized with WebGPU Samples
print("--> Starting Google Chrome (WebGPU & Vulkan)...", flush=True)
chrome_start_cmd = (
    'su - user -c "'
    'DISPLAY=:1 nohup /usr/bin/google-chrome-stable '
    f'{gpu_flags} '
    '--start-maximized '
    'https://webgpu.github.io/webgpu-samples/ > /home/user/chrome.log 2>&1 &"'
)
subprocess.run(chrome_start_cmd, shell=True)

# 10.5 x11vnc & noVNC
print("--> Starting noVNC Web Desktop...", flush=True)
spawn_daemon(["x11vnc", "-display", ":1", "-nopw", "-forever", "-shared", "-rfbport", "5900", "-listen", "127.0.0.1"], "x11vnc.log")
time.sleep(1)
spawn_daemon(["/opt/noVNC/utils/novnc_proxy", "--vnc", "127.0.0.1:5900", "--listen", "6080"], "novnc.log")
time.sleep(1)

# 10.6 Tailscale locked onto DERP (prevents 5000ms UDP flapping)
print("--> Starting Tailscale with Anti-Flapping DERP lock...", flush=True)
os.makedirs("/var/lib/tailscale", exist_ok=True)
os.makedirs("/var/run/tailscale", exist_ok=True)
spawn_daemon([
    "bash", "-c",
    "while true; do TS_DEBUG_ALWAYS_USE_DERP=true tailscaled --tun=userspace-networking --socket=/run/tailscale/tailscaled.sock --port 41641; sleep 1; done"
], "tailscale.log")
time.sleep(3)

ts_ip = subprocess.run("tailscale ip -4 || true", shell=True, text=True, capture_output=True).stdout.strip()
if not ts_ip or " " in ts_ip:
    ts_ip = "100.80.112.52"

# 10.7 Moonlight Web Stream
spawn_daemon(["/opt/moonlight-web/web-server", "run"], "moonlight_web.log", cwd="/opt/moonlight-web")

# 10.8 Cloudflare Tunnels
print("--> Establishing Cloudflare Tunnels...", flush=True)
subprocess.run("rm -f /var/log/tunnels/*.log", shell=True)
spawn_daemon(["cloudflared", "tunnel", "--url", "http://127.0.0.1:8090", "--logfile", "/var/log/tunnels/moonlight_cf.log"], "cf_moonlight.log")
spawn_daemon(["cloudflared", "tunnel", "--url", "https://127.0.0.1:47990", "--http-host-header", "localhost", "--no-tls-verify", "--logfile", "/var/log/tunnels/sunshine_cf.log"], "cf_sunshine.log")
spawn_daemon(["cloudflared", "tunnel", "--url", "http://127.0.0.1:6080", "--logfile", "/var/log/tunnels/desktop_cf.log"], "cf_desktop.log")

time.sleep(5)

def extract_cf_url(log_file, retries=15):
    for _ in range(retries):
        if os.path.exists(log_file):
            content = open(log_file).read()
            matches = re.findall(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', content)
            if matches:
                return matches[-1]
        time.sleep(1)
    return "Pending"

moonlight_url = extract_cf_url("/var/log/tunnels/moonlight_cf.log")
sunshine_url = extract_cf_url("/var/log/tunnels/sunshine_cf.log")
desktop_url = extract_cf_url("/var/log/tunnels/desktop_cf.log")

# 10.9 Configure Sunshine with NvFBC hardware capture & exact CSRF origins
allowed_origins = [
    "https://localhost:47990",
    "https://127.0.0.1:47990",
    "https://localhost:8090",
    "https://127.0.0.1:8090",
    f"https://{ts_ip}:47990"
]
if sunshine_url and "trycloudflare.com" in sunshine_url:
    allowed_origins.insert(0, sunshine_url)

os.makedirs("/root/.config/sunshine", exist_ok=True)
os.makedirs("/home/user/.config/sunshine", exist_ok=True)

sunshine_cfg = f"""origin_web_ui_allowed = wan
origin_pin_allowed = wan
csrf_allowed_origins = {','.join(allowed_origins)}
port = 47989
web_port = 47990
external_ip = {ts_ip}

# --- Anti-Stutter & Relay Bufferbloat Tuning ---
min_bitrate = 2000
max_bitrate = 8000
fec_percentage = 20
packet_size = 1200

# --- NVENC Ultra Low Latency Hardware Encoding (NvFBC) ---
nvenc_preset = 1
nvenc_twopass = 0
nvenc_spatial_aq = 0
nvenc_vbv_increase = 0
nvenc_realtime_hags = 0

# --- Audio Latency & Sync ---
audio_sink = sink-sunshine-stereo
channels = 2

# --- Controller & Gamepad Emulation ---
controller = enabled
gamepad = x360
"""
with open("/root/.config/sunshine/sunshine.conf", "w") as f:
    f.write(sunshine_cfg)
with open("/home/user/.config/sunshine/sunshine.conf", "w") as f:
    f.write(sunshine_cfg)

apps_cfg = {
    "env": {
        "PATH": "$(PATH):/usr/games:/usr/lib/games"
    },
    "apps": [
        {
            "name": "Desktop (1920x1080 FHD)",
            "cmd": "xrandr --output DVI-D-0 --mode 1920x1080",
            "image-path": "",
            "auto-detach": "true"
        },
        {
            "name": "Desktop (1366x768 WXGA)",
            "cmd": "xrandr --output DVI-D-0 --mode 1360x768",
            "image-path": "",
            "auto-detach": "true"
        },
        {
            "name": "Desktop (1280x720 HD)",
            "cmd": "xrandr --output DVI-D-0 --mode 1280x720",
            "image-path": "",
            "auto-detach": "true"
        },
        {
            "name": "Google Chrome (WebGPU & 3D)",
            "cmd": f"/usr/bin/google-chrome-stable {gpu_flags} https://webgpu.github.io/webgpu-samples/",
            "image-path": ""
        },
        {
            "name": "Steam",
            "cmd": "steam -gamepadui",
            "image-path": ""
        }
    ]
}
with open("/root/.config/sunshine/apps.json", "w") as f:
    json.dump(apps_cfg, f, indent=2)
with open("/home/user/.config/sunshine/apps.json", "w") as f:
    json.dump(apps_cfg, f, indent=2)

# Set credentials for both root and user
subprocess.run("sunshine --creds admin Sunshine123! || true", shell=True)
subprocess.run("cp -rf /root/.config/sunshine/* /home/user/.config/sunshine/", shell=True)
subprocess.run("chown -R user:user /home/user/.config/sunshine", shell=True)
subprocess.run('su - user -c "sunshine --creds admin Sunshine123!" || true', shell=True)

# Launch Sunshine under user with XDG runtime dir & Display :1
sunshine_user_cmd = (
    'su - user -c "'
    'export DISPLAY=:1; '
    'export XDG_RUNTIME_DIR=/run/user/1001; '
    'nohup sunshine > /home/user/sunshine.log 2>&1 &"'
)
subprocess.run(sunshine_user_cmd, shell=True)

ts_res = subprocess.run("tailscale up --operator=root --reset --accept-dns=false --timeout=15s", shell=True, text=True, capture_output=True)
ts_match = re.search(r'https://login\.tailscale\.com/a/[a-zA-Z0-9]+', ts_res.stdout)
tailscale_url = ts_match.group(0) if ts_match else "Check 'tailscale status'"

links = {
    "moonlight_url": moonlight_url,
    "sunshine_url": sunshine_url,
    "sunshine_direct_tailscale": f"https://{ts_ip}:47990",
    "sunshine_creds": "admin / Sunshine123!",
    "desktop_url": desktop_url,
    "tailscale_ip": ts_ip,
    "tailscale_login_url": tailscale_url
}

with open("/tmp/links.json", "w") as f:
    json.dump(links, f, indent=2)

print("="*60, flush=True)
print("=== DEPLOYMENT COMPLETE & DETACHED ===", flush=True)
print(f"Tailscale Direct IP:        {ts_ip}", flush=True)
print(f"Sunshine Direct URL:        https://{ts_ip}:47990", flush=True)
print(f"Sunshine Tunnel URL:        {sunshine_url}", flush=True)
print(f"  -> Sunshine Login:        admin / Sunshine123!", flush=True)
print(f"Moonlight Web Stream URL:   {moonlight_url}", flush=True)
print(f"Interactive Web Desktop:    {desktop_url}", flush=True)
print("="*60, flush=True)
