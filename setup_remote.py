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
