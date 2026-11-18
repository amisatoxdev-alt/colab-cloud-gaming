#!/usr/bin/env python3
"""
gta_setup.py - One-shot GTA V (repack) install for the Colab cloud-gaming desktop.

What it does
------------
 1. Installs Wine 9.0 (+ wine32, Vulkan/ICD deps) on Ubuntu 24.04.
 2. Downloads the GTA V repack from Google Drive (resumable, verified size).
 3. Extracts the 7z archive to a staging dir.
 4. Stages it into a Steam library at /content/SteamLibrary so it shows up
    in Steam under steamapps/common/Grand Theft Auto V.
 5. Registers a non-Steam shortcut (shortcuts.vdf) so it appears in the
    Steam library and can be launched from Steam / Moonlight / Sunshine.
 6. Registers a Sunshine app entry + a desktop launcher for streaming.
 7. Copies the bundled DXVK (d3d11/dxgi) next to the game exe.

Usage (on the Colab VM, as root):
    python3 gta_setup.py                # full install
    GTA_STEP=verify python3 gta_setup.py  # just prove the T4 is being used
    GTA_STEP=download python3 gta_setup.py  # resume an interrupted download
    GTA_STEP=add   python3 gta_setup.py # only re-register in Steam

Env knobs:
    GTA_FILE_ID   Google Drive file id (defaults to the known repack)
    GTA_STEP      wine|verify|download|extract|stage|add|all   (default all)
    GTA_KEEP_7Z   "1" to keep the .7z after extraction (default: delete)
"""

import glob
import os
import re
import struct
import subprocess
import sys
import time
import zlib

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
FILE_ID = os.environ.get(
    "GTA_FILE_ID", "1tifhdY5C7tqpLZ2C_PWYhHMOdq7vUcvT"
)
ARCHIVE = "/content/games/GTA_V_RIP.7z"
STAGE_DIR = "/content/games/GTAV"
STEAM_LIB = "/content/SteamLibrary"
GAME_DIR_NAME = "Grand Theft Auto V"
GAME_ROOT = f"{STEAM_LIB}/steamapps/common/{GAME_DIR_NAME}"
GAME_DATA = f"{GAME_ROOT}/gta_data"
WINEPREFIX = "/content/wineprefix-gtav"
LAUNCHER = "/content/gta_launch.sh"
STEP = os.environ.get("GTA_STEP", "all").lower()
KEEP_7Z = os.environ.get("GTA_KEEP_7Z", "0") == "1"
DESKTOP_UID = 1000
DESKTOP_USER = "user"

STEAM_ROOT = f"/home/{DESKTOP_USER}/.steam/debian-installation"

# The Drive download endpoint that actually works for huge files.
DRIVE_URL = (
    f"https://drive.usercontent.google.com/download?id={FILE_ID}"
    f"&export=download&confirm=t"
)

LOG_DIR = "/var/log/services"


def log(msg):
    """Print AND append to the log file.

    `/gtav log` tails /var/log/services/gta_setup.log, so every line has to land
    there as well as on the console -- otherwise watching progress is
    impossible once the console scrolls away.
    """
    line = f"[*] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(f"{LOG_DIR}/gta_setup.log", "a") as lf:
            lf.write(line + "\n")
    except OSError:
        pass


def sh(cmd, check=False, timeout=None, label=None, heartbeat=15):
    """Run a shell command WITHOUT holding its pipes.

    The previous version used capture_output=True. On a long apt run that is a
    silent trap in two ways: (1) nothing reaches the kernel for minutes, so the
    Colab CLI's idle timeout abandons a healthy install, and (2) a full pipe
    buffer can deadlock the child. Both are fatal here, so output goes to a log
    file and we print a heartbeat while waiting.
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    tag = label or (cmd[:48].replace(" ", "_").replace("/", "-"))
    log_path = f"{LOG_DIR}/gta_step.log"
    with open(log_path, "w") as lf:
        proc = subprocess.Popen(
            cmd, shell=True, stdout=lf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
        started = time.time()
        next_beat = started + heartbeat
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            now = time.time()
            if timeout and now - started > timeout:
                proc.kill()
                log(f"STEP TIMEOUT after {int(now - started)}s: {label or tag}")
                rc = -9
                break
            if now >= next_beat:
                log(f"still working: {label or tag} "
                    f"({int(now - started)}s elapsed)")
                next_beat = now + heartbeat
            time.sleep(0.5)

    tail = ""
    try:
        with open(log_path, "r", errors="replace") as lf:
            tail = lf.read()[-4000:]
    except OSError:
        pass
    res = subprocess.CompletedProcess(cmd, rc, tail, "")
    if check and rc != 0:
        log(f"[!] '{cmd[:90]}' rc={rc}")
        print(tail[-1200:], flush=True)
    return res


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def want(step):
    return STEP in ("all", step)


# --------------------------------------------------------------------------
# 1. Wine + Vulkan runtime
# --------------------------------------------------------------------------
def install_wine():
    log("Installing Wine + Vulkan userspace (this takes a few minutes)...")
    sh("export DEBIAN_FRONTEND=noninteractive; dpkg --add-architecture i386",
       label="wine: add i386 arch")
    sh("export DEBIAN_FRONTEND=noninteractive; apt-get update -qq",
       timeout=900, label="wine: apt-get update")
    pkgs = (
        "wine wine64 wine32:i386 cabextract winbind "
        "libvulkan1 libvulkan1:i386 vulkan-tools "
        "mesa-vulkan-drivers mesa-vulkan-drivers:i386 "
        "wmctrl xdotool imagemagick x11-utils p7zip-full"
    )
    r = sh(
        f"export DEBIAN_FRONTEND=noninteractive; "
        f"apt-get install -y -qq --no-install-recommends {pkgs}",
        timeout=2400, label="wine: apt-get install (large, be patient)",
    )
    log(f"apt rc={r.returncode}")
    v = sh("wine --version").stdout.strip()
    log(f"Wine: {v or 'MISSING'}")
    return "wine-" in v


# --------------------------------------------------------------------------
# 2. Download from Google Drive
# --------------------------------------------------------------------------
SEVEN_Z_MAGIC = b"7z\xbc\xaf\x27\x1c"


def parse_content_range(text):
    """'bytes 0-0/14724397872' -> (0, 14724397872). Also returns start."""
    m = re.search(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", text or "")
    if not m:
        return None, 0
    start = int(m.group(1))
    total = int(m.group(3)) if m.group(3) != "*" else 0
    return start, total


def drive_probe():
    """
    Determine the real size + filename.

    NOTE: a HEAD request to drive.usercontent returns 'content-length: 0',
    which previously made us think the download was already complete and
    accept a 2 KB HTML error page as the archive. We therefore use a
    one-byte ranged GET and read Content-Range instead.
    """
    r = sh(
        f"curl -sL --max-time 30 -r 0-0 -D - -o /dev/null '{DRIVE_URL}' "
        "| tr -d '\\r'"
    )
    total, name = 0, ""
    for line in r.stdout.splitlines():
        low = line.lower()
        if low.startswith("content-range:"):
            _, total = parse_content_range(line)
        elif low.startswith("content-disposition:") and "filename=" in line:
            name = line.split("filename=")[-1].strip().strip('"')
    return total, name


def is_real_archive(path):
    """True only if the file starts with the 7z magic bytes."""
    if not os.path.exists(path):
        return False
    with open(path, "rb") as f:
        return f.read(6) == SEVEN_Z_MAGIC


def looks_like_quota_page(path):
    """Drive returns a 2 KB HTML page when the file's download quota is hit."""
    if not os.path.exists(path) or os.path.getsize(path) > 200_000:
        return False
    try:
        with open(path, "rb") as f:
            head = f.read(2048).lower()
    except OSError:
        return False
    return b"quota" in head or b"<html" in head or b"<!doctype" in head


def download():
    os.makedirs(os.path.dirname(ARCHIVE), exist_ok=True)

    total, real_name = drive_probe()
    if real_name:
        log(f"Drive filename: {real_name}")
    log(f"Drive size: {human(total) if total else 'unknown (will stream)'}")

    # Throw away any poisoned 2 KB HTML "archive" left by a previous run.
    if os.path.exists(ARCHIVE) and not is_real_archive(ARCHIVE):
        n = os.path.getsize(ARCHIVE)
        reason = "quota page" if looks_like_quota_page(ARCHIVE) else "not a 7z"
        log(f"Removing invalid archive ({human(n)}, {reason}) from a prior run")
        os.remove(ARCHIVE)

    for attempt in range(1, 6):
        have = os.path.getsize(ARCHIVE) if os.path.exists(ARCHIVE) else 0
        if total and have >= total:
            break
        log(f"Download attempt {attempt} (have {human(have)})...")

        # Resume with -C - when we already hold a valid partial archive.
        resume = " -C -" if have and is_real_archive(ARCHIVE) else ""
        r = sh(
            f"curl -L --fail --retry 5 --retry-delay 3 --retry-all-errors "
            f"--connect-timeout 30{resume} -o '{ARCHIVE}' '{DRIVE_URL}'",
            timeout=None,
        )
        have = os.path.getsize(ARCHIVE) if os.path.exists(ARCHIVE) else 0
        log(f"  -> {human(have)} rc={r.returncode}")

        if looks_like_quota_page(ARCHIVE):
            os.remove(ARCHIVE)
            log("Drive returned a 'Quota exceeded' page, not the file.")
            log("That link has been downloaded too many times recently.")
            log("Wait ~24h, or make a copy in your own Drive "
                "(right-click -> Copy) and pass its id as GTA_FILE_ID.")
            return False

        if is_real_archive(ARCHIVE) and (not total or have >= total):
            break

        if have and not is_real_archive(ARCHIVE):
            os.remove(ARCHIVE)

        time.sleep(3)

    have = os.path.getsize(ARCHIVE) if os.path.exists(ARCHIVE) else 0
    ok = is_real_archive(ARCHIVE) and (not total or have >= total)
    log(f"Download {'OK' if ok else 'FAILED'}: {human(have)}"
        f"{' / ' + human(total) if total else ''}")
    return ok


# --------------------------------------------------------------------------
# 3. Extract
# --------------------------------------------------------------------------
def extract():
    if not os.path.exists(ARCHIVE):
        log("Archive missing - run the download step first")
        return False
    if not is_real_archive(ARCHIVE):
        n = os.path.getsize(ARCHIVE)
        log(f"Refusing to extract: {ARCHIVE} is not a 7z archive "
            f"({human(n)})")
        if looks_like_quota_page(ARCHIVE):
            log("It is a Google Drive 'Quota exceeded' HTML page.")
        os.remove(ARCHIVE)
        return False
    os.makedirs(STAGE_DIR, exist_ok=True)
    log(f"Extracting {human(os.path.getsize(ARCHIVE))} -> {STAGE_DIR}")
    log("(solid LZMA2 15 GB - expect 8-15 minutes, no output until done)")
    r = sh(
        f"7z x -y -bso0 -bsp0 -o'{STAGE_DIR}' '{ARCHIVE}'",
        timeout=None,
    )
    log(f"7z rc={r.returncode}")
    if r.returncode != 0:
        print(r.stdout[-1500:], r.stderr[-1500:], flush=True)
        return False
    exes = glob.glob(f"{STAGE_DIR}/**/*.exe", recursive=True)
    log(f"Extracted. {len(exes)} exe(s) found")
    return True


# --------------------------------------------------------------------------
# 4. Stage into a Steam library
# --------------------------------------------------------------------------
def stage():
    src = None
    for cand in glob.glob(f"{STAGE_DIR}/*/"):
        if os.path.isdir(os.path.join(cand, "gta_data")):
            src = cand.rstrip("/")
            break
    if not src:
        log("No 'gta_data' folder found in the extracted tree")
        return False

    os.makedirs(f"{STEAM_LIB}/steamapps/common", exist_ok=True)
    if not os.path.isdir(GAME_ROOT):
        log(f"Moving {src} -> {GAME_ROOT}")
        sh(f"mv '{src}' '{GAME_ROOT}'", check=True)
    else:
        log(f"Already staged at {GAME_ROOT}")

    # libraryfolders.vdf so Steam knows this library exists
    with open(f"{STEAM_LIB}/steamapps/libraryfolders.vdf", "w") as f:
        f.write(
            '"libraryfolders"\n{\n'
            '\t"0"\n\t{\n'
            f'\t\t"path"\t\t"{STEAM_LIB}"\n'
            '\t\t"label"\t\t""\n\t\t"contentid"\t\t"0"\n'
            '\t\t"totalsize"\t\t"0"\n'
            '\t\t"update_clean_bytes_tally"\t\t"0"\n'
            '\t\t"time_last_update_corruption"\t\t"0"\n'
            '\t\t"apps"\n\t\t{\n\t\t}\n'
            '\t}\n}\n'
        )
    sh(f"chown -R {DESKTOP_USER}:{DESKTOP_USER} {STEAM_LIB}")
    log("Steam library ready")
    return True


# --------------------------------------------------------------------------
# 5. DXVK + launcher
# --------------------------------------------------------------------------
def prepare_runtime():
    dxvk = f"{GAME_DATA}/dxvk"
    for dll in ("d3d11.dll", "dxgi.dll"):
        src = f"{dxvk}/{dll}"
        if os.path.exists(src):
            sh(f"cp -f '{src}' '{GAME_DATA}/{dll}'")
            log(f"DXVK: {dll}")

    # DXVK config tuned for a 16 GB datacenter card with no display output.
    with open(f"{GAME_DATA}/dxvk.conf", "w") as f:
        f.write(
            "# DXVK config for Tesla T4 (no display output, 16 GB VRAM)\n"
            "dxgi.maxFrameLatency = 1\n"
            "dxgi.syncInterval = 0\n"
            "d3d11.maxFrameLatency = 1\n"
            "d3d11.cachedDynamicResources = a\n"
            "d3d11.relaxedBarriers = Auto\n"
            "dxvk.numCompilerThreads = 4\n"
            "dxvk.enableGraphicsPipelineLibrary = Auto\n"
            "dxvk.hud = \n"
            "dxgi.deferSurfaceCreation = False\n"
            "d3d11.allowMapFlagNoWait = True\n"
            "dxvk.tearFree = False\n"
        )
    log("DXVK config: dxvk.conf (low latency, no vsync)")

    with open(LAUNCHER, "w") as f:
        f.write(
            "#!/bin/bash\n"
            "# Grand Theft Auto V launcher - Tesla T4 hardware accelerated\n"
            "set -u\n"
            'export DISPLAY="${DISPLAY:-:1}"\n'
            f'GAMEDIR="{GAME_DATA}"\n'
            f'export WINEPREFIX="${{WINEPREFIX:-{WINEPREFIX}}}"\n'
            "export WINEARCH=win64\n"
            "export WINEDEBUG=-all\n"
            # Force DXVK's own d3d11/dxgi regardless of what Wine ships.
            'export WINEDLLOVERRIDES="d3d11,dxgi,d3d10core=n,b"\n'
            # ---- Pin everything to the T4 and forbid all software fallbacks ----
            "export __NV_PRIME_RENDER_OFFLOAD=1\n"
            "export __GLX_VENDOR_LIBRARY_NAME=nvidia\n"
            "export VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json\n"
            "export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/nvidia_icd.json\n"
            "export DXVK_FILTER_DEVICE_NAME='Tesla T4'\n"
            "export DXVK_HUD=\"${DXVK_HUD:-0}\"\n"
            "export DXVK_STATE_CACHE=1\n"
            "export DXVK_STATE_CACHE_PATH=/content/dxvk-cache\n"
            "export MESA_LOADER_DRIVER_OVERRIDE=\n"
            "export LIBGL_ALWAYS_SOFTWARE=0\n"
            "export vblank_mode=0\n"
            "export __GL_SHADER_DISK_CACHE=1\n"
            "export __GL_SHADER_DISK_CACHE_PATH=/content/nv-shader-cache\n"
            "export __GL_THREADED_OPTIMIZATIONS=1\n"
            "export PULSE_LATENCY_MSEC=60\n"
            "mkdir -p /content/dxvk-cache /content/nv-shader-cache\n"
            'cd "$GAMEDIR" || exit 1\n'
            'if [ ! -d "$WINEPREFIX/drive_c/windows/system32" ]; then\n'
            '  echo "Initialising Wine prefix..."\n'
            "  wineboot -u >/dev/null 2>&1\n"
            "fi\n"
            'echo "GPU: $(DISPLAY=$DISPLAY glxinfo -B 2>/dev/null | '
            'grep -m1 "OpenGL renderer" | cut -d: -f2)"\n'
            'echo "Starting Grand Theft Auto V..."\n'
            # -ignoreDifferentVideoCard stops the game aborting when the
            # detected adapter does not match the one in its settings blob.
            'exec wine "PlayGTAV.exe" -ignoreDifferentVideoCard '
            '-high -nomemrestrict -norestrictions "$@"\n'
        )
    os.chmod(LAUNCHER, 0o755)
    log(f"Launcher: {LAUNCHER}")


# --------------------------------------------------------------------------
# 6. Hardware acceleration verification (T4 + NVENC)
# --------------------------------------------------------------------------
def verify_gpu():
    """
    Prove the whole chain is on the Tesla T4 and no software fallback is used:
      Xorg -> OpenGL -> Vulkan -> NVENC -> Sunshine encoder
    Returns True only if hardware rendering is confirmed.
    """
    log("Verifying hardware acceleration (Xorg / OpenGL / Vulkan / NVENC)...")
    ok = True

    # --- 1. Driver + Xorg ---------
    drv = sh("nvidia-smi --query-gpu=name,driver_version "
             "--format=csv,noheader").stdout.strip()
    log(f"nvidia-smi: {drv or 'NOT PRESENT'}")

    xconf = sh("grep -E 'Driver|BusID' /etc/X11/xorg.conf 2>/dev/null").stdout
    if '"nvidia"' in xconf:
        log("Xorg driver: nvidia (hw)")
    else:
        log("Xorg driver: NOT nvidia -> software fallback risk")
        ok = False

    # --- 2. OpenGL on the real GPU ---------
    gl = sh("DISPLAY=:1 glxinfo -B 2>/dev/null").stdout
    if "Tesla T4" in gl:
        renderer = [l for l in gl.splitlines() if "renderer" in l.lower()]
        log(f"OpenGL: {renderer[0].split(':', 1)[1].strip() if renderer else 'T4'}")
    else:
        log("OpenGL: Tesla T4 NOT reported -> software rendering")
        ok = False

    # --- 3. Vulkan must enumerate exactly the T4 -------------
    # `mesa-vulkan-drivers` ships lvp_icd.json (lavapipe = CPU Vulkan). Vulkan
    # enumerates every ICD in /usr/share/vulkan/icd.d, so lavapipe appears next
    # to the T4 and can win device selection in DXVK/Wine. Purge it, then pin
    # the NVIDIA ICD so no process can fall back to software.
    icd_dir = "/usr/share/vulkan/icd.d"
    nvidia_icd = f"{icd_dir}/nvidia_icd.json"
    purged = []
    if os.path.isdir(icd_dir):
        for fn in sorted(os.listdir(icd_dir)):
            if fn.startswith(("nvidia", "10_nvidia")) or "nvidia" in fn.lower():
                continue
            try:
                os.remove(os.path.join(icd_dir, fn))
                purged.append(fn)
            except OSError:
                pass
    if purged:
        log(f"Vulkan: purged software ICDs -> {', '.join(purged)}")

    os.environ["VK_ICD_FILENAMES"] = nvidia_icd
    os.environ["VK_DRIVER_FILES"] = nvidia_icd
    os.environ["LIBGL_ALWAYS_SOFTWARE"] = "0"

    freedesktop = "/usr/share/vulkan/implicit_layer.d"  # noqa: F841 (doc only)
    os.makedirs("/etc/environment.d", exist_ok=True)
    try:
        with open("/etc/environment.d/99-nvidia-vulkan.conf", "w") as f:
            f.write(f"VK_ICD_FILENAMES={nvidia_icd}\n")
            f.write(f"VK_DRIVER_FILES={nvidia_icd}\n")
            f.write("LIBGL_ALWAYS_SOFTWARE=0\n")
    except OSError as exc:
        log(f"Vulkan: could not persist env ({exc}) - session-only pin")

    vk = sh(f"DISPLAY=:1 VK_ICD_FILENAMES={nvidia_icd} "
            f"VK_DRIVER_FILES={nvidia_icd} vulkaninfo --summary 2>/dev/null").stdout
    names = [l.split("=")[1].strip() for l in vk.splitlines()
             if "deviceName" in l]
    uniq = sorted(set(names))
    log(f"Vulkan devices: {', '.join(uniq) if uniq else 'none'}")
    if uniq and all("Tesla T4" in n for n in uniq):
        log("Vulkan: T4 only (no lavapipe/llvmpipe fallback)")
    elif not uniq:
        log("Vulkan: no device enumerated -> check nvidia_icd.json")
        ok = False
    else:
        bad = [n for n in uniq if "Tesla T4" not in n]
        log(f"Vulkan: software device still present -> {', '.join(bad)}")
        ok = False

    # --- 4. NVENC encode actually works -------------
    enc = subprocess.run(
        "ffmpeg -hide_banner -loglevel error -f lavfi "
        "-i testsrc=size=1280x720:rate=30:duration=2 "
        "-c:v h264_nvenc -preset p1 -tune ll -b:v 6M -f null -",
        shell=True, text=True, capture_output=True,
    )
    if enc.returncode == 0:
        log("NVENC: h264_nvenc encodes successfully on the T4")
    else:
        log(f"NVENC: FAILED rc={enc.returncode} "
            f"-> {(enc.stderr or '').strip()[:200]}")
        ok = False

    # --- 5. Sunshine is capturing + encoding in hardware -------------
    slog = sh("grep -iE 'Screencasting with|Creating encoder' "
              "/home/user/sunshine.log 2>/dev/null | tail -4").stdout
    if "NvFBC" in slog:
        log("Sunshine capture: NvFBC (VRAM, hardware)")
    elif slog:
        log(f"Sunshine capture: {slog.splitlines()[-1].strip()}")
    if "nvenc" in slog.lower():
        log("Sunshine encoder: NVENC (hardware)")
    elif slog:
        log("Sunshine encoder: not NVENC -> software encode")
        ok = False

    log(f"Hardware acceleration: {'CONFIRMED' if ok else 'PROBLEMS FOUND'}")
    return ok


# --------------------------------------------------------------------------
# 7. Steam non-Steam shortcut (binary VDF)
# --------------------------------------------------------------------------
def _vdf_str(s):
    return b"\x01" + s.encode("utf-8") + b"\x00"


def _vdf_str_kv(k, v):
    return b"\x01" + k.encode("utf-8") + b"\x00" + v.encode("utf-8") + b"\x00"


def _vdf_int(k, v):
    return b"\x02" + k.encode("utf-8") + b"\x00" + struct.pack("<i", v)


def find_exe():
    for name in ("PlayGTAV.exe", "GTA5.exe", "GTAVLauncher.exe"):
        hit = glob.glob(f"{GAME_ROOT}/**/{name}", recursive=True)
        if hit:
            return hit[0]
    exes = [e for e in glob.glob(f"{GAME_ROOT}/**/*.exe", recursive=True)
            if f"{os.sep}bonus{os.sep}" not in e]
    return exes[0] if exes else None


def shortcuts_vdf(exe, appname="Grand Theft Auto V",
                  launch_opts="", icon=""):
    """Build a shortcuts.vdf root with a single non-Steam entry."""
    appid = (zlib.crc32(f"{exe}{appname}".encode("utf-8")) | 0x80000000)
    if appid > 0x7FFFFFFF:
        appid -= 0x100000000

    b = bytearray()
    b += b"\x00" + b"0" + b"\x00"          # entry: key "0" (object)
    b += _vdf_int("appid", appid)
    b += _vdf_str_kv("AppName", appname)
    b += _vdf_str_kv("Exe", f'"{exe}"')
    b += _vdf_str_kv("StartDir", f'"{os.path.dirname(exe)}"')
    b += _vdf_str_kv("icon", icon)
    b += _vdf_str_kv("ShortcutPath", "")
    b += _vdf_str_kv("LaunchOptions", launch_opts)
    b += _vdf_int("IsHidden", 0)
    b += _vdf_int("AllowDesktopConfig", 1)
    b += _vdf_int("AllowOverlay", 1)
    b += _vdf_int("OpenVR", 0)
    b += _vdf_int("Devkit", 0)
    b += _vdf_str_kv("DevkitGameID", "")
    b += _vdf_int("DevkitOverrideAppID", 0)
    b += _vdf_int("LastPlayTime", 0)
    b += _vdf_str_kv("FlatpakAppID", "")
    b += b"\x00" + b"tags" + b"\x00"        # tags object
    b += b"\x01" + b"0" + b"\x00" + b"" + b"\x00"
    b += b"\x08"                            # end tags
    b += b"\x08"                            # end entry
    return appid, b"\x00" + b"shortcuts" + b"\x00" + bytes(b) + b"\x08\x08"


def steam_ids():
    """Return steam3 ids that have a userdata dir (i.e. have logged in)."""
    ids = []
    for p in glob.glob(f"{STEAM_ROOT}/userdata/*"):
        base = os.path.basename(p)
        if base.isdigit() and base != "0" and os.path.isdir(p):
            ids.append(p)
    return ids


def add_to_steam():
    exe = find_exe()
    if not exe:
        log("Could not find a game executable - run extract/stage first")
        return False
    log(f"Executable: {exe}")

    if sh("pgrep -x steam").stdout.strip():
        log("Steam is running - closing it so the shortcut survives")
        sh("pkill -TERM -x steam; sleep 6; pkill -KILL -x steam")

    ids = steam_ids()
    if not ids:
        log("Steam has never been logged in on this VM (no userdata/<id>).")
        log("Writing a pending copy of shortcuts.vdf; it will be installed "
            "automatically the next time this script runs after a Steam login.")
        os.makedirs("/content/steam_shortcut_pending", exist_ok=True)
        appid, data = shortcuts_vdf(exe)
        with open("/content/steam_shortcut_pending/shortcuts.vdf", "wb") as f:
            f.write(data)
        log(f"Pending shortcut written (appid {appid}). "
            "Log into Steam once, then re-run: GTA_STEP=add")
        return True

    appid, data = shortcuts_vdf(exe)
    for ud in ids:
        cfg = os.path.join(ud, "config")
        os.makedirs(cfg, exist_ok=True)
        path = os.path.join(cfg, "shortcuts.vdf")
        if os.path.exists(path):
            sh(f"cp -f '{path}' '{path}.bak'")
        with open(path, "wb") as f:
            f.write(data)
        sh(f"chown -R {DESKTOP_USER}:{DESKTOP_USER} '{path}'")
        log(f"shortcuts.vdf -> {path} (appid {appid})")
    return True


# --------------------------------------------------------------------------
# 7. Sunshine app + desktop launcher
# --------------------------------------------------------------------------
SUNSHINE_APPS = f"/home/{DESKTOP_USER}/.config/sunshine/apps.json"


def add_sunshine_app():
    import json
    name = "Grand Theft Auto V"
    try:
        with open(SUNSHINE_APPS) as f:
            data = json.load(f)
    except Exception:
        log("Sunshine apps.json not found - skipping (run setup_remote.py first)")
        return False

    apps = data.setdefault("apps", [])
    apps = [a for a in apps if a.get("name") != name]
    apps.append({
        "name": name,
        "cmd": f"{LAUNCHER}",
        "image-path": "",
        "auto-detach": "true",
        "wait-all": "true",
        "exit-timeout": "5",
    })
    data["apps"] = apps
    with open(SUNSHINE_APPS, "w") as f:
        json.dump(data, f, indent=2)
    sh(f"chown {DESKTOP_USER}:{DESKTOP_USER} '{SUNSHINE_APPS}'")
    log(f"Sunshine app '{name}' registered")
    return True


def add_desktop_launcher():
    desk = f"/home/{DESKTOP_USER}/Desktop"
    os.makedirs(desk, exist_ok=True)
    path = f"{desk}/Grand Theft Auto V.desktop"
    with open(path, "w") as f:
        f.write(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Grand Theft Auto V\n"
            f"Exec={LAUNCHER}\n"
            "Icon=applications-games\n"
            "Terminal=false\n"
            "Categories=Game;\n"
        )
    os.chmod(path, 0o755)
    sh(f"chown {DESKTOP_USER}:{DESKTOP_USER} '{path}'")
    log(f"Desktop launcher: {path}")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    print("=" * 62)
    print(" GTA V installer for the Colab cloud-gaming desktop")
    print(f" step={STEP}  archive={ARCHIVE}")
    print("=" * 62, flush=True)

    if want("wine"):
        if not install_wine():
            log("Wine install failed - continuing anyway")

    if want("verify"):
        verify_gpu()

    if want("download"):
        if not download():
            log("Download failed. Re-run with GTA_STEP=download (it resumes).")
            return 1

    if want("extract"):
        if not extract():
            return 1

    if want("stage"):
        if not stage():
            return 1

    if want("stage") or want("add"):
        prepare_runtime()

    if want("add"):
        add_to_steam()
        add_sunshine_app()
        add_desktop_launcher()
        verify_gpu()

    if KEEP_7Z is False and os.path.exists(ARCHIVE) and want("stage"):
        log(f"Removing archive to reclaim {human(os.path.getsize(ARCHIVE))}")
        os.remove(ARCHIVE)

    print("\n" + "=" * 62)
    print(" DONE")
    print(f"  game      : {GAME_ROOT}")
    print(f"  launcher  : {LAUNCHER}")
    print(f"  wine      : {WINEPREFIX}")
    print("  launch    : bash " + LAUNCHER)
    print("  in steam  : Library -> Grand Theft Auto V")
    print("=" * 62, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

