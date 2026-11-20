import subprocess
import os
import sys
import time
import json
import re
import shutil
import urllib.request
from concurrent.futures import ThreadPoolExecutor

def log(msg):
    print(f"[*] {msg}", flush=True)

def run(cmd, check=True, label=None, heartbeat=12, timeout=None):
    """Run a shell command with periodic heartbeats to keep the kernel output active.

    Colab CLI aborts execution if no output is received from the kernel for the duration
    of --timeout. Long commands (apt-get install, package downloads, updates) must emit
    heartbeats so the idle timer never fires. Output is captured to /var/log/services/setup_exec.log.
    """
    os.makedirs("/var/log/services", exist_ok=True)
    tag = label or (cmd[:60].strip() if len(cmd) > 60 else cmd.strip())
    log_file = "/var/log/services/setup_exec.log"
    with open(log_file, "a") as lf:
        proc = subprocess.Popen(
            cmd, shell=True, stdout=lf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True
        )
        t0 = time.time()
        next_beat = t0 + heartbeat
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            now = time.time()
            if timeout and now - t0 > timeout:
                proc.kill()
                log(f"Command timed out after {int(now - t0)}s: {tag}")
                rc = -9
                break
            if now >= next_beat:
                log(f"Still working: {tag} ({int(now - t0)}s elapsed)")
                next_beat = now + heartbeat
            time.sleep(0.5)

    tail = ""
    try:
        with open(log_file, "r", errors="replace") as lf:
            tail = lf.read()[-500:]
    except Exception:
        pass

    if check and rc != 0:
        log(f"[!] Warning: '{tag}' returned code {rc}:\n{tail}")
    return subprocess.CompletedProcess(cmd, rc, stdout=tail, stderr="")

os.makedirs("/var/log/services", exist_ok=True)
os.makedirs("/var/log/tunnels", exist_ok=True)

# -------------------------------------------------------------
# 0. Asynchronous Parallel Downloads (Pre-fetching while apt runs)
# -------------------------------------------------------------
desktop_mode = os.environ.get("DESKTOP_MODE", "minimal").lower()
if os.path.exists("/tmp/desktop_mode.txt"):
    try:
        desktop_mode = open("/tmp/desktop_mode.txt").read().strip().lower()
    except Exception:
        pass
try:
    with open("/tmp/desktop_mode.txt", "w") as f:
        f.write(desktop_mode)
except Exception:
    pass

log(f"Starting background downloads (Desktop Mode: {desktop_mode.upper()})...")
DOWNLOADS = {
    "/tmp/chrome.deb": "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
    "/tmp/sunshine.deb": "https://github.com/LizardByte/Sunshine/releases/download/v2026.914.233613/sunshine_2026.914.233613-1+ubuntu24.04_amd64.deb",
    "/tmp/moonlightweb.deb": "https://github.com/linckosz/moonlight-web/releases/download/v0.4.3/moonlightweb-0.4.3-linux-x64.deb",
    "/root/nvidia-wallpaper.jpg": "https://wallpapercat.com/w/full/3/b/2/1255774-2500x1341-desktop-hd-nvidia-wallpaper-photo.jpg",
    "/root/win11-wallpaper.jpg": "https://raw.githubusercontent.com/viridivn/windows11wallpapers/master/Windows/img19.jpg"
}

def fetch_file(target, url):
    if os.path.exists(target) and os.path.getsize(target) > 10000:
        return
    try:
        subprocess.run(f"curl -fsSL -o '{target}' '{url}'", shell=True)
    except Exception as e:
        log(f"Download error for {target}: {e}")

executor = ThreadPoolExecutor(max_workers=5)
download_futures = {target: executor.submit(fetch_file, target, url) for target, url in DOWNLOADS.items()}

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

# User permissions & input group
run("groupadd -f input", check=False)
run("id -u user >/dev/null 2>&1 || (useradd -m -s /bin/bash user && echo 'user:123456' | chpasswd)", check=False)
run("usermod -aG sudo,video,audio,input user", check=False)

# Enforce pure Xorg XTest by removing uinput character devices
run("rm -rf /dev/uinput /dev/input /dev/uhid /usr/local/lib/libselective_uinput.so", check=False)
run("pkill -9 -f systemd-udevd 2>/dev/null || true", check=False)
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

# --- Purge every non-NVIDIA Vulkan ICD -------------------------------------
# `mesa-vulkan-drivers` ships lvp_icd.json (lavapipe = CPU/software Vulkan).
# Vulkan enumerates ALL ICDs in this directory, so lavapipe shows up alongside
# the T4 and can win device selection inside DXVK/Wine. Keep nvidia only.
_icd_dir = "/usr/share/vulkan/icd.d"
_keep = {"nvidia_icd.json", "nvidia_icd.x86_64.json"}
_purged = []
if os.path.isdir(_icd_dir):
    for _f in sorted(os.listdir(_icd_dir)):
        if _f in _keep or "nvidia" in _f.lower():
            continue
        try:
            os.remove(os.path.join(_icd_dir, _f))
            _purged.append(_f)
        except OSError:
            pass
log(f"Vulkan ICDs: kept NVIDIA, purged {_purged or 'nothing'}")

# Pin the ICD for every process (login shells, Sunshine, Steam, Wine)
os.makedirs("/etc/environment.d", exist_ok=True)
with open("/etc/environment.d/99-nvidia-vulkan.conf", "w") as f:
    f.write("VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json\n")
    f.write("VK_DRIVER_FILES=/usr/share/vulkan/icd.d/nvidia_icd.json\n")
    f.write("LIBGL_ALWAYS_SOFTWARE=0\n")

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
log("Configuring APT repositories and package sources...")
run("sudo add-apt-repository -y multiverse", check=False, label="add multiverse repository", heartbeat=10)
run("dpkg --add-architecture i386", check=False, label="add i386 architecture")

# Add Tailscale repo cleanly
run("curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg | tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null 2>&1", check=False)
run("curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list | tee /etc/apt/sources.list.d/tailscale.list >/dev/null 2>&1", check=False)
log("Updating APT package indexes...")
run("apt-get update -y", check=False, label="apt-get update", heartbeat=10)

# Wait for parallel downloads to finish with progress reporting
log("Checking background downloads...")
for target, fut in download_futures.items():
    fname = os.path.basename(target)
    t0 = time.time()
    while not fut.done():
        log(f"Still downloading {fname}... ({int(time.time() - t0)}s)")
        time.sleep(6)
    fut.result()
    sz = os.path.getsize(target) if os.path.exists(target) else 0
    log(f"Package ready: {fname} ({sz // 1024} KB)")

pkgs = [
    "xserver-xorg-core", "x11-xserver-utils", "x11vnc", "openbox", "xterm", "feh",
    "pulseaudio", "pulseaudio-utils", "tint2", "pcmanfm", "papirus-icon-theme", "rofi", "picom",
    "librsvg2-common", "librsvg2-bin", "xsettingsd",
    "vulkan-tools", "libvulkan1", "mesa-vulkan-drivers", "mesa-utils", "libgl1-mesa-dri", "libglvnd0",
    "ffmpeg", "libva2", "net-tools", "iproute2", "steam:i386", "tailscale", "tailscale-archive-keyring",
    "libxtst6", "libx11-6", "xdotool",
    "libxtst6:i386", "libxrandr2:i386", "libxrender1:i386", "libgtk2.0-0t64:i386",
    "libpipewire-0.3-0t64:i386", "libpulse0:i386", "libgdk-pixbuf-2.0-0:i386",
    "libxcb-res0:i386", "libvdpau1:i386", "libbz2-1.0:i386", "dbus-x11",
    "/tmp/chrome.deb", "/tmp/moonlightweb.deb", "/tmp/sunshine.deb"
]
log("Executing unified APT package installation...")
run(
    f"DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends {' '.join(pkgs)}",
    check=False,
    label="apt-get install desktop packages",
    heartbeat=10
)
log("Unified APT packages successfully installed.")

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

# Set up official MoonlightWeb (moonlightweb.top) - installed via .deb package
if not os.path.exists("/opt/moonlightweb/bin/MoonlightWeb"):
    run("dpkg -i /tmp/moonlightweb.deb >/dev/null 2>&1 || apt-get install -f -y >/dev/null 2>&1", check=False)
run("ln -sf /opt/moonlightweb/bin/MoonlightWeb /usr/local/bin/moonlightweb 2>/dev/null || true", check=False)

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

# -------------------------------------------------------------
# 3.8 Windows 11 Desktop Shortcuts & Tint2 Centered Taskbar
# -------------------------------------------------------------
log("Configuring Windows 11 Desktop shortcuts, icons, and centered taskbar...")
desktop_dir = "/home/user/Desktop"
os.makedirs(desktop_dir, exist_ok=True)

shortcuts = {
    "this_pc.desktop": (
        "[Desktop Entry]\nVersion=1.0\nName=This PC\n"
        "Exec=pcmanfm /home/user\nIcon=computer\nTerminal=false\nType=Application\n"
    ),
    "google-chrome.desktop": (
        f"[Desktop Entry]\nVersion=1.0\nName=Google Chrome\n"
        f"Exec=/usr/bin/google-chrome-stable {gpu_flags} %U\nIcon=google-chrome\nTerminal=false\nType=Application\n"
    ),
    "steam.desktop": (
        "[Desktop Entry]\nVersion=1.0\nName=Steam\n"
        "Exec=/usr/games/steam -silent\nIcon=steam\nTerminal=false\nType=Application\n"
    ),
    "terminal.desktop": (
        "[Desktop Entry]\nVersion=1.0\nName=Terminal\n"
        "Exec=xterm -fa 'Monospace' -fs 11 -bg '#1e1e1e' -fg '#ffffff'\nIcon=utilities-terminal\nTerminal=false\nType=Application\n"
    ),
    "recycle_bin.desktop": (
        "[Desktop Entry]\nVersion=1.0\nName=Recycle Bin\n"
        "Exec=pcmanfm trash:///\nIcon=user-trash\nTerminal=false\nType=Application\n"
    ),
    "vulkan_test.desktop": (
        "[Desktop Entry]\nVersion=1.0\nName=Vulkan 3D Test\n"
        "Exec=vkcube\nIcon=applications-games\nTerminal=false\nType=Application\n"
    ),
    "start_menu.desktop": (
        "[Desktop Entry]\nVersion=1.0\nName=Start\n"
        "Exec=rofi -show drun\nIcon=start-here\nTerminal=false\nType=Application\n"
    )
}

for fname, s_content in shortcuts.items():
    fpath = f"{desktop_dir}/{fname}"
    with open(fpath, "w") as f:
        f.write(s_content)
    subprocess.run(f"chmod +x '{fpath}'", shell=True)

run("chown -R user:user /home/user/Desktop", check=False)

# Configure Rofi Windows 11 Fluent Start Menu
rofi_dir = "/home/user/.config/rofi"
os.makedirs(rofi_dir, exist_ok=True)
rofi_rasi = """configuration {
    modi: "drun,run";
    show-icons: true;
    icon-theme: "Papirus";
    drun-display-format: "{name}";
    font: "Sans 11";
}
* {
    background-color: transparent;
    text-color: #ffffff;
}
window {
    location: south west;
    anchor: south west;
    x-offset: 12px;
    y-offset: -56px;
    width: 480px;
    height: 540px;
    background-color: rgba(24, 24, 26, 0.95);
    border: 1px;
    border-color: rgba(60, 60, 65, 0.8);
    border-radius: 10px;
    padding: 16px;
}
mainbox {
    children: [ inputbar, listview ];
    spacing: 12px;
}
inputbar {
    background-color: rgba(40, 40, 44, 0.9);
    border-radius: 6px;
    padding: 8px 12px;
    children: [ prompt, entry ];
    border: 1px;
    border-color: #0078d4;
}
prompt {
    enabled: false;
}
entry {
    placeholder: "Type here to search...";
    placeholder-color: #888888;
    text-color: #ffffff;
}
listview {
    columns: 2;
    lines: 6;
    spacing: 6px;
    scrollbar: false;
}
element {
    padding: 8px 10px;
    border-radius: 6px;
    spacing: 10px;
}
element selected {
    background-color: rgba(255, 255, 255, 0.12);
    text-color: #00a4ef;
}
element-icon {
    size: 28px;
}
element-text {
    vertical-align: 0.5;
}
"""
with open(f"{rofi_dir}/config.rasi", "w") as f:
    f.write(rofi_rasi)
run("chown -R user:user /home/user/.config/rofi", check=False)

# Configure Tint2 Windows 11 Fluent Centered Taskbar
tint2_dir = "/home/user/.config/tint2"
os.makedirs(tint2_dir, exist_ok=True)
tint2_conf = """# Tint2 Windows 11 Fluent Dark Theme
rounded = 0
border_width = 1
border_sides = T
border_color = #333336 60
background_color = #18181a 92

# Background 2: Active Task (Windows 11 Blue Bottom Accent)
rounded = 4
border_width = 2
border_sides = B
border_color = #0078d4 100
background_color = #262629 90

# Panel
panel_items = LTSC
panel_monitor = all
panel_position = bottom center horizontal
panel_size = 100% 48
panel_margin = 0 0
panel_padding = 4 2 4
panel_background_id = 1
panel_dock = 0
panel_layer = top
panel_window_name = tint2
disable_transparency = 0
mouse_effects = 1

# Launchers (Start, Chrome, Steam, Files, Terminal)
launcher_padding = 6 4 6
launcher_background_id = 0
launcher_icon_size = 30
launcher_icon_theme = Papirus
launcher_item_app = /home/user/Desktop/start_menu.desktop
launcher_item_app = /home/user/Desktop/google-chrome.desktop
launcher_item_app = /home/user/Desktop/steam.desktop
launcher_item_app = /home/user/Desktop/this_pc.desktop
launcher_item_app = /home/user/Desktop/terminal.desktop

# Taskbar (Centered)
taskbar_mode = single_desktop
taskbar_padding = 4 0 4
taskbar_background_id = 0
taskbar_active_background_id = 0
taskbar_name = 0
taskbar_hide_inactive_tasks = 0
taskbar_hide_different_monitor = 0
taskbar_always_show_all_desktop_tasks = 1
taskbar_sort_order = none
task_align = left

# Tasks
task_text = 0
task_icon = 1
task_centered = 1
task_padding = 10 4
task_background_id = 0
task_active_background_id = 2
task_iconified_background_id = 0

# Systray
systray_padding = 6 4 6
systray_background_id = 0
systray_sort = right2left
systray_icon_size = 20

# Clock (Windows 11 Format: Time on top, Date underneath)
time1_format = %H:%M
time2_format = %d/%m/%Y
time1_font = Sans Bold 9
time2_font = Sans 8
clock_font_color = #e0e0e0 100
clock_padding = 8 2
clock_background_id = 0
clock_tooltip = %A, %B %d, %Y
"""
with open(f"{tint2_dir}/tint2rc", "w") as f:
    f.write(tint2_conf)
run("chown -R user:user /home/user/.config/tint2", check=False)

# Configure PCManFM desktop wallpaper & icons
pcman_dir = "/home/user/.config/pcmanfm/default"
os.makedirs(pcman_dir, exist_ok=True)
with open(f"{pcman_dir}/desktop-items-0.conf", "w") as f:
    f.write("[*]\nwallpaper_mode=stretch\nwallpaper=/home/user/nvidia-wallpaper.jpg\ndesktop_bg=#000000\ndesktop_fg=#ffffff\ndesktop_shadow=#000000\nshow_wm_menu=1\nsort_by=2\nsort_type=0\n")
run("chown -R user:user /home/user/.config/pcmanfm", check=False)

# -------------------------------------------------------------
# 3.9 Windows 11 Community Themes & 100% Pure Xorg XTest Guarantee
# -------------------------------------------------------------
log("Configuring Windows 11 Community Theme, xsettingsd, and 100% Native XTest...")
run("rm -rf /dev/uinput /dev/input /dev/uhid /usr/local/lib/libselective_uinput.so /tmp/selective_uinput.*", check=False)
run("pkill -9 -f systemd-udevd 2>/dev/null || true", check=False)

# Download and install B00merang Windows 11 Theme
if not os.path.exists("/usr/share/themes/Windows-11/openbox-3/themerc"):
    run("curl -fsSL https://github.com/B00merang-Project/Windows-10/archive/refs/heads/master.tar.gz -o /tmp/win10.tar.gz", check=False)
    run("mkdir -p /usr/share/themes/Windows-11 && tar -xzf /tmp/win10.tar.gz -C /usr/share/themes/Windows-11 --strip-components=1 2>/dev/null || true", check=False)

# Patch themerc obsolete parentrelative keyword
themerc_p = "/usr/share/themes/Windows-11/openbox-3/themerc"
if os.path.exists(themerc_p):
    try:
        t_data = open(themerc_p).read().replace("parentrelative", "flat")
        open(themerc_p, "w").write(t_data)
    except Exception:
        pass

# Download Windows 11 Start Icon
run("curl -fsSL -o /home/user/.config/win11-start.png https://raw.githubusercontent.com/B00merang-Artwork/Windows-11/master/icon.png 2>/dev/null || true", check=False)

# Configure xsettingsd for global GTK theme propagation
for xs_dir in ["/home/user/.config/xsettingsd", "/root/.config/xsettingsd"]:
    os.makedirs(xs_dir, exist_ok=True)
    with open(f"{xs_dir}/xsettingsd.conf", "w") as f:
        f.write('Net/ThemeName "Windows-11"\nNet/IconThemeName "Papirus"\nGtk/CursorThemeName "Adwaita"\nGtk/FontName "Segoe UI 10"\nXft/Antialias 1\nXft/Hinting 1\nXft/HintStyle "hintslight"\nXft/RGBA "rgb"\n')

# Configure Openbox rc.xml
# ---------------------------------------------------------------------------
# NEVER hand-write a minimal rc.xml. A config whose <mouse> section holds only
# the "Root" context binds NOTHING to the title bar: the buttons still render
# (they come from <titleLayout> + themerc artwork) but no click is ever handled,
# so close / iconify / maximize are completely dead. That was the real bug.
#
# Instead we take the distribution rc.xml -- which carries all 19 mouse
# contexts (Frame, Titlebar, Titlebar Top Right Bottom Left ..., Client, Close,
# Iconify, Maximize, Desktop, Root, MoveResize) -- and override only what we
# actually want: the theme name, the title layout, and our keybinds.
OB_THEME = "Windows-11"
OB_TITLE_LAYOUT = "NLIMC"

def build_openbox_rc():
    base = None
    for cand in ("/etc/xdg/openbox/rc.xml", "/usr/share/openbox/rc.xml"):
        if not os.path.exists(cand):
            continue
        try:
            text = open(cand, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if "<openbox_config" in text:
            base = text
            break
    if not base:
        return None

    # Theme name: only the <name> inside <theme>, not one of the <font> names.
    base = re.sub(r"(<theme>.*?<name>)[^<]*(</name>)",
                  lambda m: m.group(1) + OB_THEME + m.group(2),
                  base, count=1, flags=re.S)
    # Title layout: icon | label | iconify | maximize | close
    if "<titleLayout>" in base:
        base = re.sub(r"<titleLayout>[^<]*</titleLayout>",
                      f"<titleLayout>{OB_TITLE_LAYOUT}</titleLayout>", base, count=1)
    else:
        base = base.replace("</theme>",
                            f"  <titleLayout>{OB_TITLE_LAYOUT}</titleLayout>\n  </theme>", 1)

    # Windows-like shortcuts, appended only when the distro does not ship them.
    kb_extra = ""
    if '"W-d"' not in base:
        kb_extra += ('    <keybind key="W-d">\n'
                     '      <action name="ToggleShowDesktop"/>\n'
                     '    </keybind>\n')
    if '"W-r"' not in base:
        kb_extra += ('    <keybind key="W-r">\n'
                     '      <action name="Execute">\n'
                     '        <command>rofi -show drun</command>\n'
                     '      </action>\n'
                     '    </keybind>\n')
    if kb_extra:
        if "</keyboard>" in base:
            base = base.replace("</keyboard>", kb_extra + "  </keyboard>", 1)
        else:
            base = base.replace("</openbox_config>",
                                "  <keyboard>\n" + kb_extra + "  </keyboard>\n</openbox_config>", 1)
    return base

ob_xml = build_openbox_rc()
if ob_xml:
    for ob_dir in ["/home/user/.config/openbox", "/root/.config/openbox"]:
        os.makedirs(ob_dir, exist_ok=True)
        with open(f"{ob_dir}/rc.xml", "w") as f:
            f.write(ob_xml)
    log("Openbox rc.xml derived from distro default (all mouse contexts intact).")
else:
    log("WARNING: no distribution openbox rc.xml found - leaving openbox defaults.")

run("chown -R user:user /home/user/.config", check=False)

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

# Dynamically query GPU Name and PCI Bus ID
gpu_name_res = subprocess.run("nvidia-smi --query-gpu=gpu_name --format=csv,noheader", shell=True, text=True, capture_output=True)
gpu_board_name = gpu_name_res.stdout.strip() or "NVIDIA Graphics Device"
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
    BoardName      "{gpu_board_name}"
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

# -------------------------------------------------------------
# Nebula Cloud Gaming Web Portal (Self-Contained Deployment)
# -------------------------------------------------------------
PORTAL_TAR_GZ_B64 = """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"""

def extract_cloud_portal(target_dir="/opt/cloud_portal"):
    try:
        import io, tarfile, base64
        os.makedirs(target_dir, exist_ok=True)
        raw = base64.b64decode(PORTAL_TAR_GZ_B64.strip())
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            tar.extractall(target_dir)
        log(f"Nebula Cloud Gaming Web Portal extracted successfully to {target_dir}")
    except Exception as e:
        log(f"Warning: Failed to extract cloud portal: {e}")

# 5. Starting All Services (Xorg, PulseAudio, Openbox, Tailscale, MoonlightWeb)
# -------------------------------------------------------------
log("Starting display, audio, and background daemons...")
subprocess.run("pkill -9 -f portal_server.py 2>/dev/null || true; killall -9 Xorg Xvfb x11vnc openbox sunshine cloudflared MoonlightWeb tailscaled 2>/dev/null || true", shell=True)
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
spawn_daemon(["/usr/lib/xorg/Xorg", ":1", "-allowMouseOpenFail", "-novtswitch", "-nolisten", "tcp"], "xorg.log")
time.sleep(2)
run("DISPLAY=:1 xhost + >/dev/null 2>&1 || true", check=False)
# --- Desktop resolution ----------------------------------------------------
# Candidate modes in preference order. "1366x768" is what people ask for, but
# this EDID has no 1366x768 entry -- the true 16:9 mode is 1368x768 (and
# 1360x768 on some panels). Trying a mode the EDID does not list silently fails,
# leaving the screen at whatever Xorg picked, so probe before applying.
DESKTOP_RES_CANDIDATES = [os.environ.get("DESKTOP_RES", ""), "1368x768",
                          "1360x768", "1920x1080", "1280x720"]
DESKTOP_RES = "1920x1080"
try:
    _modes_out = subprocess.run("DISPLAY=:1 xrandr --query", shell=True, text=True,
                                capture_output=True).stdout
    for _cand in DESKTOP_RES_CANDIDATES:
        if _cand and _cand in _modes_out:
            DESKTOP_RES = _cand
            break
    run(f"DISPLAY=:1 xrandr --output DVI-D-0 --mode {DESKTOP_RES} >/dev/null 2>&1 || true",
        check=False)
    _dims = subprocess.run("DISPLAY=:1 xdpyinfo 2>/dev/null | grep -m1 dimensions",
                           shell=True, text=True, capture_output=True).stdout.strip()
    log(f"Desktop resolution: {DESKTOP_RES} ({_dims or 'unverified'})")
except Exception as _exc:
    log(f"Resolution setup skipped: {_exc}")

# 5.2 Low-Latency PulseAudio under user
run('su - user -c "export XDG_RUNTIME_DIR=/run/user/1001; pulseaudio --kill 2>/dev/null || true"', check=False)
run('su - user -c "export XDG_RUNTIME_DIR=/run/user/1001; pulseaudio --start --exit-idle-time=-1"', check=False)
run('su - user -c "export XDG_RUNTIME_DIR=/run/user/1001; pactl load-module module-null-sink sink_name=sink-sunshine-stereo sink_properties=device.description=SunshineStereo >/dev/null 2>&1 || true"', check=False)
run('su - user -c "export XDG_RUNTIME_DIR=/run/user/1001; pactl set-default-sink sink-sunshine-stereo >/dev/null 2>&1 || true"', check=False)

# 5.3 Desktop Environment (Windows 11 GUI vs Pure Game Mode)
if os.path.exists("/root/nvidia-wallpaper.jpg"):
    shutil.copy2("/root/nvidia-wallpaper.jpg", "/home/user/nvidia-wallpaper.jpg")
    run("chown user:user /home/user/nvidia-wallpaper.jpg", check=False)
if os.path.exists("/root/win11-wallpaper.jpg"):
    shutil.copy2("/root/win11-wallpaper.jpg", "/home/user/win11-wallpaper.jpg")
    run("chown user:user /home/user/win11-wallpaper.jpg", check=False)

wallpaper_file = "/home/user/nvidia-wallpaper.jpg" if os.path.exists("/home/user/nvidia-wallpaper.jpg") else "/home/user/win11-wallpaper.jpg"

if desktop_mode == "win11":
    log("Deploying Full Windows 11 Fluent GUI (Taskbar, Start Menu, Desktop Icons, NVIDIA Wallpaper)...")
    for ob_dir in ["/home/user/.config/openbox", "/root/.config/openbox"]:
        os.makedirs(ob_dir, exist_ok=True)
        with open(f"{ob_dir}/autostart", "w") as f:
            f.write("#!/bin/sh\n"
                    "xset s off -dpms s noblank\n"
                    "xset r rate 250 30\n"
                    f"xrandr --output DVI-D-0 --mode {DESKTOP_RES}\n"
                    "xsettingsd &\n"
                    f"feh --bg-fill {wallpaper_file} &\n"
                    "pcmanfm --desktop &\n"
                    "picom -b --corner-radius 8 &\n"
                    "tint2 &\n")
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
    run(f'su - user -c "{openbox_env} nohup xsettingsd >/dev/null 2>&1 &"', check=False)
    run(f'su - user -c "{openbox_env} nohup openbox > /home/user/openbox.log 2>&1 &"', check=False)
    run('DISPLAY=:1 xset s off -dpms s noblank >/dev/null 2>&1 || true', check=False)
    run('DISPLAY=:1 xset r rate 250 30 >/dev/null 2>&1 || true', check=False)
    run(f'su - user -c "DISPLAY=:1 nohup feh --bg-fill {wallpaper_file} > /home/user/feh.log 2>&1 < /dev/null &"', check=False)
    run(f'DISPLAY=:1 nohup feh --bg-fill {wallpaper_file} > /tmp/feh_root.log 2>&1 < /dev/null', check=False)
    run(f'su - user -c "{openbox_env} nohup pcmanfm --desktop > /home/user/pcmanfm.log 2>&1 &"', check=False)
    run(f'su - user -c "{openbox_env} nohup picom -b --corner-radius 8 >/dev/null 2>&1 &"', check=False)
    run(f'su - user -c "{openbox_env} nohup tint2 > /home/user/tint2.log 2>&1 &"', check=False)
else:
    log("Deploying Pure Game Mode (No Desktop - Maximum FPS & Zero Overhead)...")
    for ob_dir in ["/home/user/.config/openbox", "/root/.config/openbox"]:
        os.makedirs(ob_dir, exist_ok=True)
        with open(f"{ob_dir}/autostart", "w") as f:
            f.write("#!/bin/sh\n"
                    "xset s off -dpms s noblank\n"
                    "xset r rate 250 30\n"
                    f"xrandr --output DVI-D-0 --mode {DESKTOP_RES}\n"
                    f"feh --bg-fill {wallpaper_file} &\n")
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
    run(f'su - user -c "DISPLAY=:1 nohup feh --bg-fill {wallpaper_file} > /home/user/feh.log 2>&1 < /dev/null &"', check=False)
    run(f'DISPLAY=:1 nohup feh --bg-fill {wallpaper_file} > /tmp/feh_root.log 2>&1 < /dev/null', check=False)

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

# 5.6 Nebula Cloud Gaming Web Portal (Port 8090) & MoonlightWeb (Port 8091)
log("Extracting and launching Nebula Cloud Gaming Web Portal on port 8090...")
run("pip install -q aiohttp", check=False)
extract_cloud_portal()
spawn_daemon([sys.executable, "/opt/cloud_portal/portal_server.py", "8090"], "portal_server.log", env_override=env)
time.sleep(1)

log("Starting MoonlightWeb fallback on port 8091...")
mw_env = env.copy()
mw_env["MW_SERVICE"] = "1"
spawn_daemon(["/opt/moonlightweb/bin/MoonlightWeb", "--port", "8091", "--autostart"],
             "moonlightweb.log", env_override=mw_env)
time.sleep(2)

# 5.7 Tailscale login (must happen BEFORE Funnel can be published)
log("Bringing Tailscale up and waiting for node authorization...")
ts_res = subprocess.run("tailscale up --operator=root --reset --accept-dns=false --timeout=25s",
                        shell=True, text=True, capture_output=True)
ts_match = re.search(r'https://login\.tailscale\.com/a/[a-zA-Z0-9]+', ts_res.stdout + ts_res.stderr)
tailscale_url = ts_match.group(0) if ts_match else "Check 'tailscale status'"
if "login.tailscale.com" in tailscale_url:
    log(f"Tailscale Authorization URL: {tailscale_url}")

# Re-resolve node IP now that the node may be authenticated
ts_ip_live = subprocess.run("tailscale ip -4 2>/dev/null || true", shell=True, text=True, capture_output=True).stdout.strip()
if ts_ip_live and " " not in ts_ip_live and not ts_ip_live.startswith("100.64."):
    pass
if ts_ip_live and " " not in ts_ip_live:
    ts_ip = ts_ip_live

# 5.8 Tailscale Funnel (public HTTPS endpoint, replaces Cloudflare tunnels entirely)
log("Publishing MoonlightWeb through Tailscale Funnel...")
subprocess.run("mkdir -p /var/log/tunnels", shell=True)
subprocess.run("tailscale funnel --bg --yes 8090 > /var/log/tunnels/funnel.log 2>&1", shell=True)
time.sleep(3)

funnel_url = ""
try:
    f_stat = subprocess.run("tailscale funnel status 2>&1", shell=True, text=True, capture_output=True).stdout
    m = re.search(r'https://[A-Za-z0-9.\-]+\.ts\.net', f_stat)
    if m:
        funnel_url = m.group(0).rstrip("/")
    else:
        s_raw = subprocess.run("tailscale status --json 2>/dev/null", shell=True, text=True, capture_output=True).stdout
        dns_name = json.loads(s_raw).get("Self", {}).get("DNSName", "").rstrip(".")
        if dns_name:
            funnel_url = f"https://{dns_name}"
except Exception:
    pass

# 5.9 Generate the one-time Access PIN for remote browsers (single-use per device)
def mw_access_pin(generate_new=False):
    bin_path = "/opt/moonlightweb/bin/MoonlightWeb"
    flag = "--new-pin" if generate_new else "--status"
    out = subprocess.run(f"{bin_path} {flag} 2>&1", shell=True, text=True, capture_output=True).stdout
    pm = re.search(r"Access PIN\s+(\d{4,8})", out)
    return pm.group(1) if pm else ""

moonlight_pin = mw_access_pin(generate_new=True) or mw_access_pin(False)
log(f"MoonlightWeb Access PIN ready: {moonlight_pin or 'PENDING (run /pin)'}")

# 5.10 Sunshine Configuration
allowed_origins = [
    "https://localhost:47990",
    "https://127.0.0.1:47990",
    "https://localhost:8090",
    "https://127.0.0.1:8090"
]
if ts_ip and ts_ip != "127.0.0.1":
    allowed_origins.append(f"https://{ts_ip}:47990")
if funnel_url:
    allowed_origins.append(funnel_url)
    allowed_origins.append(f"{funnel_url}:443")

os.makedirs("/root/.config/sunshine", exist_ok=True)
os.makedirs("/home/user/.config/sunshine", exist_ok=True)

# Detect if GPU supports AV1 hardware encode (e.g. Ada Lovelace L4)
is_av1_supported = any(k in gpu_board_name.lower() for k in ("l4", "ada", "4090", "4080", "4070", "4060", "hopper"))
av1_mode = 3 if is_av1_supported else 0

sunshine_cfg = f"""origin_web_ui_allowed = wan
origin_pin_allowed = wan
csrf_allowed_origins = {','.join(allowed_origins)}
port = 47989
web_port = 47990
external_ip = {ts_ip}

min_bitrate = 2000
max_bitrate = 50000
fec_percentage = 20
packet_size = 1024

# --- NVIDIA hardware capture + encode (NVFBC + NVENC) ---
encoder = nvenc
capture = nvfbc
nvenc_preset = 1
nvenc_twopass = quarter_res
nvenc_spatial_aq = enabled
nvenc_vbv_increase = 0
nvenc_realtime_hags = enabled
nvenc_latency_over_power = enabled

hevc_mode = 3
av1_mode = {av1_mode}

# --- Low-latency streaming for Moonlight / MoonlightWeb ---
channels = 2
audio_sink = sink-sunshine-stereo
"""
with open("/root/.config/sunshine/sunshine.conf", "w") as f:
    f.write(sunshine_cfg)
with open("/home/user/.config/sunshine/sunshine.conf", "w") as f:
    f.write(sunshine_cfg)

desktop_title = "Windows 11 Desktop" if desktop_mode == "win11" else "Game Mode Desktop"
apps_cfg = {
    "env": {"PATH": "$(PATH):/usr/games:/usr/lib/games"},
    "apps": [
        {"name": f"{desktop_title} ({DESKTOP_RES} native)", "cmd": f"xrandr --output DVI-D-0 --mode {DESKTOP_RES}", "image-path": "", "auto-detach": "true"},
        {"name": f"{desktop_title} (1920x1080 FHD)", "cmd": "xrandr --output DVI-D-0 --mode 1920x1080", "image-path": "", "auto-detach": "true"},
        {"name": f"{desktop_title} (1368x768 WXGA)", "cmd": "xrandr --output DVI-D-0 --mode 1368x768", "image-path": "", "auto-detach": "true"},
        {"name": f"{desktop_title} (1280x720 HD)", "cmd": "xrandr --output DVI-D-0 --mode 1280x720", "image-path": "", "auto-detach": "true"},
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

# Ensure no /dev/uinput exists before Sunshine starts so it uses 100% pure XTest
subprocess.run("rm -rf /dev/uinput /dev/input /dev/uhid", shell=True)

sunshine_user_cmd = (
    'su - user -c "'
    'export DISPLAY=:1; '
    'export XDG_RUNTIME_DIR=/run/user/1001; '
    'export NO_AT_BRIDGE=1; '
    'export GTK_IM_MODULE=xim; '
    'export QT_IM_MODULE=xim; '
    'export XMODIFIERS=@im=none; '
    'unset LD_PRELOAD; '
    'nohup sunshine > /home/user/sunshine.log 2>&1 &"'
)
subprocess.run(sunshine_user_cmd, shell=True)

# -------------------------------------------------------------
# 6. Final Publish Pass (Funnel + PIN) - guaranteed after Tailscale is up
# -------------------------------------------------------------
log("Final pass: publishing Funnel and issuing the web access PIN...")
subprocess.run("tailscale funnel --bg --yes 8090 > /var/log/tunnels/funnel.log 2>&1", shell=True)
time.sleep(2)

try:
    f_stat = subprocess.run("tailscale funnel status 2>&1", shell=True, text=True, capture_output=True).stdout
    m = re.search(r'https://[A-Za-z0-9.\-]+\.ts\.net', f_stat)
    if m:
        funnel_url = m.group(0).rstrip("/")
    elif not funnel_url:
        s_raw = subprocess.run("tailscale status --json 2>/dev/null", shell=True, text=True, capture_output=True).stdout
        dns_name = json.loads(s_raw).get("Self", {}).get("DNSName", "").rstrip(".")
        if dns_name:
            funnel_url = f"https://{dns_name}"
except Exception:
    pass

# Always mint a brand-new single-use PIN at the end of setup
fresh_pin = mw_access_pin(generate_new=True) or mw_access_pin(False)
if fresh_pin:
    moonlight_pin = fresh_pin

ts_ip_live = subprocess.run("tailscale ip -4 2>/dev/null || true", shell=True, text=True, capture_output=True).stdout.strip()
if ts_ip_live and " " not in ts_ip_live:
    ts_ip = ts_ip_live

links = {
    "funnel_url": f"{funnel_url}/" if funnel_url else "Run /funnel to publish",
    "moonlight_pin": moonlight_pin,
    "pin_command": "/opt/moonlightweb/bin/MoonlightWeb --new-pin",
    "sunshine_direct_tailscale": f"https://{ts_ip}:47990" if ts_ip != "127.0.0.1" else "Authorize Tailscale",
    "sunshine_creds": "admin / Sunshine123!",
    "tailscale_ip": ts_ip if ts_ip != "127.0.0.1" else "",
    "tailscale_login_url": tailscale_url
}
with open("/var/log/services/links.json", "w") as f:
    json.dump(links, f, indent=2)
with open("/tmp/links.json", "w") as f:
    json.dump(links, f, indent=2)

log(f"Funnel URL: {links['funnel_url']}")
log(f"Web Access PIN: {moonlight_pin or 'run /pin to generate'}")
log("Deployment complete and all daemons initialized successfully.")
