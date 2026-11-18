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
desktop_mode = os.environ.get("DESKTOP_MODE", "minimal").lower()
if os.path.exists("/tmp/desktop_mode.txt"):
    try:
        desktop_mode = open("/tmp/desktop_mode.txt").read().strip().lower()
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
    "pulseaudio", "pulseaudio-utils", "tint2", "pcmanfm", "papirus-icon-theme", "rofi", "picom",
    "librsvg2-common", "librsvg2-bin", "xsettingsd",
    "vulkan-tools", "libvulkan1", "mesa-vulkan-drivers", "mesa-utils", "libgl1-mesa-dri", "libglvnd0",
    "ffmpeg", "libva2", "net-tools", "iproute2", "steam:i386", "tailscale", "tailscale-archive-keyring",
    "libxtst6:i386", "libxrandr2:i386", "libxrender1:i386", "libgtk2.0-0t64:i386",
    "libpipewire-0.3-0t64:i386", "libpulse0:i386", "libgdk-pixbuf-2.0-0:i386",
    "libxcb-res0:i386", "libvdpau1:i386", "libbz2-1.0:i386", "dbus-x11",
    "/tmp/chrome.deb", "/tmp/moonlightweb.deb", "/tmp/sunshine.deb"
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
ob_xml = """<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <theme>
    <name>Windows-11</name>
    <titleLayout>NLIMC</titleLayout>
    <keepBorder>yes</keepBorder>
    <animateIconify>no</animateIconify>
    <font place="ActiveWindow">
      <name>Sans</name>
      <size>9</size>
      <weight>Bold</weight>
    </font>
  </theme>
  <desktops>
    <number>1</number>
    <names>
      <name>Desktop</name>
    </names>
  </desktops>
  <keyboard>
    <keybind key="A-F4">
      <action name="Close"/>
    </keybind>
    <keybind key="W-d">
      <action name="ToggleShowDesktop"/>
    </keybind>
    <keybind key="W-r">
      <action name="Execute">
        <command>rofi -show drun</command>
      </action>
    </keybind>
  </keyboard>
  <mouse>
    <context name="Root">
      <mousebind button="Right" action="Press">
        <action name="ShowMenu"><menu>root-menu</menu></action>
      </mousebind>
    </context>
  </mouse>
</openbox_config>
"""
for ob_dir in ["/home/user/.config/openbox", "/root/.config/openbox"]:
    os.makedirs(ob_dir, exist_ok=True)
    with open(f"{ob_dir}/rc.xml", "w") as f:
        f.write(ob_xml)

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
# 5. Starting All Services (Xorg, PulseAudio, Openbox, Tailscale, MoonlightWeb)
# -------------------------------------------------------------
log("Starting display, audio, and background daemons...")
subprocess.run("killall -9 Xorg Xvfb x11vnc openbox sunshine cloudflared MoonlightWeb tailscaled 2>/dev/null || true", shell=True)
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
run("DISPLAY=:1 xrandr --output DVI-D-0 --mode 1920x1080 >/dev/null 2>&1 || true", check=False)

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
    run(f'su - user -c "DISPLAY=:1 feh --bg-fill {wallpaper_file} &"', check=False)
    run(f'DISPLAY=:1 feh --bg-fill {wallpaper_file} &', check=False)
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
    run(f'su - user -c "DISPLAY=:1 feh --bg-fill {wallpaper_file} &"', check=False)
    run(f'DISPLAY=:1 feh --bg-fill {wallpaper_file} &', check=False)

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

# 5.6 MoonlightWeb (official moonlightweb.top client) - started early, published later
log("Starting MoonlightWeb web streaming server on port 8090...")
mw_env = env.copy()
mw_env["MW_SERVICE"] = "1"
spawn_daemon(["/opt/moonlightweb/bin/MoonlightWeb", "--port", "8090", "--autostart"],
             "moonlightweb.log", env_override=mw_env)
time.sleep(4)

# 5.7 Tailscale login (must happen BEFORE Funnel can be published)
log("Bringing Tailscale up and waiting for node authorization...")
ts_res = subprocess.run("tailscale up --operator=root --reset --accept-dns=false --timeout=25s",
                        shell=True, text=True, capture_output=True)
ts_match = re.search(r'https://login\.tailscale\.com/a/[a-zA-Z0-9]+', ts_res.stdout + ts_res.stderr)
tailscale_url = ts_match.group(0) if ts_match else "Check 'tailscale status'"

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

# --- Tesla T4 hardware capture + encode (never software) ---
encoder = nvenc
capture = nvfbc
nvenc_preset = 1
nvenc_twopass = quarter_res
nvenc_spatial_aq = enabled
nvenc_vbv_increase = 0
nvenc_realtime_hags = enabled
nvenc_latency_over_power = enabled

hevc_mode = 3
av1_mode = 0

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
        {"name": f"{desktop_title} (1920x1080 FHD)", "cmd": "xrandr --output DVI-D-0 --mode 1920x1080", "image-path": "", "auto-detach": "true"},
        {"name": f"{desktop_title} (1366x768 WXGA)", "cmd": "xrandr --output DVI-D-0 --mode 1360x768", "image-path": "", "auto-detach": "true"},
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
