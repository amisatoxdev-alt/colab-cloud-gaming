import subprocess
import os
import sys
import time
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

def log(msg):
    print(f"[*] {msg}", flush=True)

def run(cmd, check=True):
    res = subprocess.run(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check and res.returncode != 0:
        print(f"[!] Warning: '{cmd}' returned code {res.returncode}:\n{res.stdout[:500]}", flush=True)
    return res

os.makedirs("/var/log/services", exist_ok=True)
os.makedirs("/var/log/tunnels", exist_ok=True)

# -------------------------------------------------------------
# 0. Asynchronous Parallel Downloads (Pre-fetching while apt runs)
# -------------------------------------------------------------
log("Starting background downloads for Chrome, Cloudflared, Sunshine, Moonlight-Web, and Wallpaper...")
DOWNLOADS = {
    "/tmp/chrome.deb": "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
    "/tmp/cloudflared.deb": "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb",
    "/tmp/sunshine.deb": "https://github.com/LizardByte/Sunshine/releases/download/v2026.914.233613/sunshine_2026.914.233613-1+ubuntu24.04_amd64.deb",
    "/tmp/moonlight-web.tar.gz": "https://github.com/MrCreativ3001/moonlight-web-stream/releases/download/v3.0.2/moonlight-web-x86_64-unknown-linux-gnu.tar.gz",
    "/root/nvidia-wallpaper.jpg": "https://wallpapercat.com/w/full/3/b/2/1255774-2500x1341-desktop-hd-nvidia-wallpaper-photo.jpg"
}

def fetch_file(target, url):
    if os.path.exists(target) and os.path.getsize(target) > 10000:
        return
    try:
        subprocess.run(f"curl -fsSL -o '{target}' '{url}'", shell=True)
    except Exception as e:
        log(f"Download error for {target}: {e}")

executor = ThreadPoolExecutor(max_workers=5)
download_futures = [executor.submit(fetch_file, target, url) for target, url in DOWNLOADS.items()]

# -------------------------------------------------------------
# 1. Network Buffers, Fast DNS & Dedicated User Setup
# -------------------------------------------------------------
log("Configuring network buffers, DNS, and user permissions...")
try:
    with open('/etc/resolv.conf', 'w') as f:
        f.write("nameserver 1.1.1.1\nnameserver 1.0.0.1\nnameserver 8.8.8.8\noptions timeout:1 attempts:2 rotate\n")
except Exception:
    pass

for key, val in [
    ("net.core.rmem_max", "33554432"),
    ("net.core.wmem_max", "33554432"),
    ("net.core.rmem_default", "8388608"),
    ("net.core.wmem_default", "8388608"),
    ("net.core.netdev_max_backlog", "10000"),
    ("net.ipv4.udp_rmem_min", "16384"),
    ("net.ipv4.udp_wmem_min", "16384"),
    ("net.core.default_qdisc", "fq"),
    ("net.ipv4.tcp_congestion_control", "bbr")
]:
    subprocess.run(f"sysctl -w {key}={val} 2>/dev/null || true", shell=True)

# User permissions
run("id -u user >/dev/null 2>&1 || (useradd -m -s /bin/bash user && echo 'user:123456' | chpasswd)", check=False)
run("usermod -aG sudo,video,audio user", check=False)
run("rm -rf /dev/input /dev/uinput /dev/uhid", check=False)
run("mkdir -p /run/user/1001/pulse && chown -R user:user /run/user/1001 && chmod 700 /run/user/1001", check=False)

# Pre-accept Steam license and pre-configure Steam to avoid modal dialogs
run("echo steam steam/question select \"I AGREE\" | debconf-set-selections", check=False)
run("echo steam steam/license note '' | debconf-set-selections", check=False)
run("mkdir -p /home/user/.steam /home/user/.local/share/Steam", check=False)
run("touch /home/user/.steam/steam_app_installed", check=False)
run("chown -R user:user /home/user/.steam /home/user/.local", check=False)

# -------------------------------------------------------------
# 2. NVIDIA ICD & Userspace Drivers
# -------------------------------------------------------------
log("Configuring NVIDIA Vulkan ICD & Userspace Drivers...")
with open('/etc/ld.so.conf.d/nvidia64.conf', 'w') as f:
    f.write('/usr/lib64-nvidia\n')

os.makedirs('/usr/share/vulkan/icd.d', exist_ok=True)
with open('/usr/share/vulkan/icd.d/nvidia_icd.json', 'w') as f:
    json.dump({
        "file_format_version": "1.0.0",
        "ICD": {"library_path": "libGLX_nvidia.so.0", "api_version": "1.3.277"}
    }, f, indent=2)

os.makedirs('/usr/share/glvnd/egl_vendor.d', exist_ok=True)
with open('/usr/share/glvnd/egl_vendor.d/10_nvidia.json', 'w') as f:
    json.dump({
        "file_format_version": "1.0.0",
        "ICD": {"library_path": "libEGL_nvidia.so.0"}
    }, f, indent=2)

# Install Tesla userspace compatibility library
r_ver = subprocess.run("nvidia-smi --query-gpu=driver_version --format=csv,noheader", shell=True, text=True, capture_output=True)
driver_ver = r_ver.stdout.strip()
if driver_ver:
    installer_path = f"/tmp/NVIDIA-Linux-x86_64-{driver_ver}.run"
    if not os.path.exists(installer_path):
        url = f"https://us.download.nvidia.com/tesla/{driver_ver}/NVIDIA-Linux-x86_64-{driver_ver}.run"
        run(f"curl -fsSL -o {installer_path} {url}", check=False)
        run(f"chmod +x {installer_path}", check=False)
    run(f"{installer_path} --no-kernel-module --ui=none --no-x-check --silent || true", check=False)

os.makedirs("/usr/lib/xorg/modules/drivers", exist_ok=True)
os.makedirs("/usr/lib/xorg/modules/extensions", exist_ok=True)
if os.path.exists("/usr/lib64-nvidia/xorg/modules/drivers/nvidia_drv.so"):
    run("ln -sf /usr/lib64-nvidia/xorg/modules/drivers/nvidia_drv.so /usr/lib/xorg/modules/drivers/nvidia_drv.so", check=False)
if os.path.exists("/usr/lib64-nvidia/xorg/modules/extensions/libglxserver_nvidia.so"):
    run("ln -sf /usr/lib64-nvidia/xorg/modules/extensions/libglxserver_nvidia.so /usr/lib/xorg/modules/extensions/libglxserver_nvidia.so", check=False)
run("ldconfig", check=False)

# -------------------------------------------------------------
# 3. APT Package Installation & Tailscale
# -------------------------------------------------------------
log("Configuring APT repositories and installing packages...")
run("sudo add-apt-repository -y multiverse >/dev/null 2>&1", check=False)
run("dpkg --add-architecture i386", check=False)
run("apt-get update -y >/dev/null 2>&1", check=False)

# Add Tailscale repo cleanly
run("curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg | tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null 2>&1", check=False)
run("curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list | tee /etc/apt/sources.list.d/tailscale.list >/dev/null 2>&1", check=False)
run("apt-get update -y >/dev/null 2>&1", check=False)

# Wait for parallel downloads to finish
for fut in download_futures:
    fut.result()

pkgs = [
    "xserver-xorg-core", "x11-xserver-utils", "x11vnc", "openbox", "xterm", "feh",
    "pulseaudio", "pulseaudio-utils",
    "vulkan-tools", "libvulkan1", "mesa-vulkan-drivers", "mesa-utils", "libgl1-mesa-dri", "libglvnd0",
    "ffmpeg", "libva2", "net-tools", "iproute2", "steam:i386", "tailscale", "tailscale-archive-keyring",
    "libxtst6:i386", "libxrandr2:i386", "libxrender1:i386", "libgtk2.0-0t64:i386",
    "libpipewire-0.3-0t64:i386", "libpulse0:i386", "libgdk-pixbuf-2.0-0:i386",
    "libxcb-res0:i386", "libvdpau1:i386", "libbz2-1.0:i386", "dbus-x11",
    "/tmp/chrome.deb", "/tmp/cloudflared.deb", "/tmp/sunshine.deb"
]
log("Executing unified APT package installation...")
run(f"DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends {' '.join(pkgs)} >/dev/null 2>&1", check=False)

# Bootstrap Steam to bypass zenity modal prompts and enable instant hardware 3D launch
steam_dir = "/home/user/.steam/debian-installation"
os.makedirs(f"{steam_dir}/deb-installer", exist_ok=True)
os.makedirs("/home/user/.steam", exist_ok=True)
run(f"ln -fns '{steam_dir}' /home/user/.steam/steam", check=False)
run(f"ln -fns '{steam_dir}' /home/user/.steam/root", check=False)
if not os.path.exists(f"{steam_dir}/bootstrap.tar.xz"):
    run(f"curl -fsSL -o '{steam_dir}/deb-installer/steam.tar.gz' https://repo.steampowered.com/steam/archive/beta/steam_1.0.0.79.tar.gz", check=False)
    run(f"tar -C '{steam_dir}/deb-installer' -zxf '{steam_dir}/deb-installer/steam.tar.gz' steam-launcher/bootstraplinux_ubuntu12_32.tar.xz", check=False)
    run(f"mv '{steam_dir}/deb-installer/steam-launcher/bootstraplinux_ubuntu12_32.tar.xz' '{steam_dir}/bootstrap.tar.xz'", check=False)
    run(f"tar -C '{steam_dir}' -xf '{steam_dir}/bootstrap.tar.xz'", check=False)
    with open(f"{steam_dir}/deb-installer/version", "w") as f:
        f.write("1.0.0.79\n")
run("touch /home/user/.steam/steam_app_installed", check=False)
run("chown -R user:user /home/user/.steam /home/user/.local", check=False)

# Set up Moonlight Web
if not os.path.exists("/opt/moonlight-web/web-server"):
    run("mkdir -p /opt/moonlight-web && tar -xzf /tmp/moonlight-web.tar.gz -C /opt/moonlight-web --strip-components=1", check=False)
    run("chmod +x /opt/moonlight-web/web-server", check=False)
    run("cd /opt/moonlight-web && ./web-server config generate >/dev/null 2>&1 || true", check=False)
    try:
        with open('/opt/moonlight-web/server/config.json', 'r') as f:
            cfg = json.load(f)
        cfg['web_server']['bind_address'] = '0.0.0.0:8090'
        with open('/opt/moonlight-web/server/config.json', 'w') as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

# Set up noVNC
if not os.path.exists("/opt/noVNC"):
    run("git clone --depth 1 https://github.com/novnc/noVNC.git /opt/noVNC >/dev/null 2>&1 || true", check=False)
    run("git clone --depth 1 https://github.com/novnc/websockify /opt/noVNC/utils/websockify >/dev/null 2>&1 || true", check=False)
    run("ln -s /opt/noVNC/vnc.html /opt/noVNC/index.html 2>/dev/null || true", check=False)

# Copy wallpaper
run("cp -f /root/nvidia-wallpaper.jpg /home/user/nvidia-wallpaper.jpg 2>/dev/null || true", check=False)
run("chown user:user /home/user/nvidia-wallpaper.jpg", check=False)

# Patch Google Chrome with hardware WebGPU & Vulkan flags
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

# -------------------------------------------------------------
# 4. Hardware EDID & Dynamic PCI Bus ID Xorg Setup
# -------------------------------------------------------------
log("Configuring Hardware EDID (1080p / 768p / 720p) and Xorg...")
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

# Dynamically query PCI Bus ID
pci_raw = subprocess.run("nvidia-smi --query-gpu=pci.bus_id --format=csv,noheader", shell=True, text=True, capture_output=True).stdout.strip()
bus_id = "PCI:0:4:0"
m = re.search(r'(\d+):(\d+)\.0', pci_raw)
if m:
    bus_id = f"PCI:{int(m.group(1))}:{int(m.group(2))}:0"

xorg_conf = f"""
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
    BusID          "{bus_id}"
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

# -------------------------------------------------------------
# 5. Starting All Services (Xorg, PulseAudio, Openbox, Tailscale, Cloudflare)
# -------------------------------------------------------------
log("Starting display, audio, and background daemons...")
subprocess.run("killall -9 Xorg Xvfb x11vnc openbox sunshine web-server cloudflared tailscaled 2>/dev/null || true", shell=True)
time.sleep(1)
subprocess.run("rm -f /tmp/.X1-lock /tmp/.X11-unix/X1 /tmp/com.google.Chrome.* /root/.config/google-chrome/Singleton*", shell=True)

env = os.environ.copy()
env["DISPLAY"] = ":1"

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

# 5.1 Hardware Xorg with NvFBC VRAM Capture
spawn_daemon(["/usr/lib/xorg/Xorg", ":1", "-seat", "seat-1", "-allowMouseOpenFail", "-novtswitch", "-nolisten", "tcp"], "xorg.log")
time.sleep(2)
run("DISPLAY=:1 xhost + >/dev/null 2>&1 || true", check=False)
run("DISPLAY=:1 xrandr --output DVI-D-0 --mode 1920x1080 >/dev/null 2>&1 || true", check=False)

# 5.2 Low-Latency PulseAudio under user
run('su - user -c "export XDG_RUNTIME_DIR=/run/user/1001; pulseaudio --kill 2>/dev/null || true"', check=False)
run('su - user -c "export XDG_RUNTIME_DIR=/run/user/1001; pulseaudio --start --exit-idle-time=-1"', check=False)
run('su - user -c "export XDG_RUNTIME_DIR=/run/user/1001; pactl load-module module-null-sink sink_name=sink-sunshine-stereo sink_properties=device.description=SunshineStereo >/dev/null 2>&1 || true"', check=False)
run('su - user -c "export XDG_RUNTIME_DIR=/run/user/1001; pactl set-default-sink sink-sunshine-stereo >/dev/null 2>&1 || true"', check=False)

# 5.3 Openbox Autostart & Wallpaper
for ob_dir in ["/home/user/.config/openbox", "/root/.config/openbox"]:
    os.makedirs(ob_dir, exist_ok=True)
    with open(f"{ob_dir}/autostart", "w") as f:
        f.write("#!/bin/sh\nxset s off -dpms s noblank\nxset r rate 250 30\nfeh --bg-fill /home/user/nvidia-wallpaper.jpg &\n")
    run(f"chmod +x {ob_dir}/autostart", check=False)
run("chown -R user:user /home/user/.config/openbox", check=False)

openbox_env = (
    'export DISPLAY=:1; '
    'export XDG_RUNTIME_DIR=/run/user/1001; '
    'export NO_AT_BRIDGE=1; '
    'export GTK_IM_MODULE=xim; '
    'export QT_IM_MODULE=xim; '
    'export XMODIFIERS=@im=none; '
)
run(f'su - user -c "{openbox_env} nohup openbox > /home/user/openbox.log 2>&1 &"', check=False)
run('DISPLAY=:1 xset s off -dpms s noblank >/dev/null 2>&1 || true', check=False)
run('DISPLAY=:1 xset r rate 250 30 >/dev/null 2>&1 || true', check=False)
run('su - user -c "DISPLAY=:1 feh --bg-fill /home/user/nvidia-wallpaper.jpg &"', check=False)
run('DISPLAY=:1 feh --bg-fill /home/user/nvidia-wallpaper.jpg &', check=False)

# 5.4 Google Chrome & noVNC
spawn_daemon(["x11vnc", "-display", ":1", "-nopw", "-forever", "-shared", "-rfbport", "5900", "-listen", "127.0.0.1"], "x11vnc.log")
spawn_daemon(["/opt/noVNC/utils/novnc_proxy", "--vnc", "127.0.0.1:5900", "--listen", "6080"], "novnc.log")

# 5.5 Tailscale DERP mode
os.makedirs("/var/lib/tailscale", exist_ok=True)
os.makedirs("/var/run/tailscale", exist_ok=True)
spawn_daemon([
    "bash", "-c",
    "while true; do TS_DEBUG_ALWAYS_USE_DERP=true tailscaled --tun=userspace-networking --socket=/run/tailscale/tailscaled.sock --port 41641; sleep 1; done"
], "tailscale.log")
time.sleep(2)

ts_ip = subprocess.run("tailscale ip -4 2>/dev/null || true", shell=True, text=True, capture_output=True).stdout.strip()
if not ts_ip or " " in ts_ip:
    ts_ip = "127.0.0.1"

# 5.6 Moonlight Web
spawn_daemon(["/opt/moonlight-web/web-server", "run"], "moonlight_web.log", cwd="/opt/moonlight-web")

# 5.7 Cloudflare Tunnels
log("Establishing Cloudflare Tunnels...")
subprocess.run("rm -f /var/log/tunnels/*.log", shell=True)
spawn_daemon(["cloudflared", "tunnel", "--url", "http://127.0.0.1:8090", "--logfile", "/var/log/tunnels/moonlight_cf.log"], "cf_moonlight.log")
spawn_daemon(["cloudflared", "tunnel", "--url", "https://127.0.0.1:47990", "--http-host-header", "localhost", "--no-tls-verify", "--logfile", "/var/log/tunnels/sunshine_cf.log"], "cf_sunshine.log")
spawn_daemon(["cloudflared", "tunnel", "--url", "http://127.0.0.1:6080", "--logfile", "/var/log/tunnels/desktop_cf.log"], "cf_desktop.log")

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

# 5.8 Sunshine Configuration
allowed_origins = [
    "https://localhost:47990",
    "https://127.0.0.1:47990",
    "https://localhost:8090",
    "https://127.0.0.1:8090"
]
if ts_ip and ts_ip != "127.0.0.1":
    allowed_origins.append(f"https://{ts_ip}:47990")
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

min_bitrate = 2000
max_bitrate = 8000
fec_percentage = 20
packet_size = 1200

nvenc_preset = 1
nvenc_twopass = 0
nvenc_spatial_aq = 0
nvenc_vbv_increase = 0
nvenc_realtime_hags = 0

audio_sink = sink-sunshine-stereo
channels = 2
"""
with open("/root/.config/sunshine/sunshine.conf", "w") as f:
    f.write(sunshine_cfg)
with open("/home/user/.config/sunshine/sunshine.conf", "w") as f:
    f.write(sunshine_cfg)

apps_cfg = {
    "env": {"PATH": "$(PATH):/usr/games:/usr/lib/games"},
    "apps": [
        {"name": "Desktop (1920x1080 FHD)", "cmd": "xrandr --output DVI-D-0 --mode 1920x1080", "image-path": "", "auto-detach": "true"},
        {"name": "Desktop (1366x768 WXGA)", "cmd": "xrandr --output DVI-D-0 --mode 1360x768", "image-path": "", "auto-detach": "true"},
        {"name": "Desktop (1280x720 HD)", "cmd": "xrandr --output DVI-D-0 --mode 1280x720", "image-path": "", "auto-detach": "true"},
        {"name": "Google Chrome (WebGPU & 3D)", "cmd": f"/usr/bin/google-chrome-stable {gpu_flags} https://webgpu.github.io/webgpu-samples/", "image-path": ""},
        {"name": "Steam", "cmd": "steam -silent", "image-path": ""},
        {"name": "Vulkan 3D Test (vkcube)", "cmd": "vkcube", "image-path": ""},
        {"name": "OpenGL 3D Test (glxgears)", "cmd": "glxgears -info", "image-path": ""}
    ]
}
with open("/root/.config/sunshine/apps.json", "w") as f:
    json.dump(apps_cfg, f, indent=2)
with open("/home/user/.config/sunshine/apps.json", "w") as f:
    json.dump(apps_cfg, f, indent=2)

subprocess.run("sunshine --creds admin Sunshine123! >/dev/null 2>&1 || true", shell=True)
subprocess.run("cp -rf /root/.config/sunshine/* /home/user/.config/sunshine/", shell=True)
subprocess.run("chown -R user:user /home/user/.config/sunshine", shell=True)
subprocess.run('su - user -c "sunshine --creds admin Sunshine123!" >/dev/null 2>&1 || true', shell=True)

sunshine_user_cmd = (
    'su - user -c "'
    'export DISPLAY=:1; '
    'export XDG_RUNTIME_DIR=/run/user/1001; '
    'export NO_AT_BRIDGE=1; '
    'export GTK_IM_MODULE=xim; '
    'export QT_IM_MODULE=xim; '
    'export XMODIFIERS=@im=none; '
    'nohup sunshine > /home/user/sunshine.log 2>&1 &"'
)
subprocess.run(sunshine_user_cmd, shell=True)

ts_res = subprocess.run("tailscale up --operator=root --reset --accept-dns=false --timeout=15s", shell=True, text=True, capture_output=True)
ts_match = re.search(r'https://login\.tailscale\.com/a/[a-zA-Z0-9]+', ts_res.stdout)
tailscale_url = ts_match.group(0) if ts_match else "Check 'tailscale status'"

links = {
    "moonlight_url": moonlight_url,
    "sunshine_url": sunshine_url,
    "sunshine_direct_tailscale": f"https://{ts_ip}:47990" if ts_ip != "127.0.0.1" else "Authorize Tailscale",
    "sunshine_creds": "admin / Sunshine123!",
    "desktop_url": desktop_url,
    "tailscale_ip": ts_ip if ts_ip != "127.0.0.1" else "",
    "tailscale_login_url": tailscale_url
}
with open("/var/log/services/links.json", "w") as f:
    json.dump(links, f, indent=2)
with open("/tmp/links.json", "w") as f:
    json.dump(links, f, indent=2)

log("Deployment complete and all daemons initialized successfully.")
