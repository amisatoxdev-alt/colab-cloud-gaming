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
    GTA_STEP=wine  python3 gta_setup.py # only one step
    GTA_STEP=add   python3 gta_setup.py # only re-register in Steam

Env knobs:
    GTA_FILE_ID   Google Drive file id (defaults to the known repack)
    GTA_STEP      wine|download|extract|stage|add|all   (default all)
    GTA_KEEP_7Z   "1" to keep the .7z after extraction (default: delete)
"""

import glob
import os
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
    print(f"[*] {msg}", flush=True)


def sh(cmd, check=False, timeout=None):
    res = subprocess.run(
        cmd, shell=True, text=True, capture_output=True, timeout=timeout
    )
    if check and res.returncode != 0:
        print(f"[!] '{cmd[:90]}' rc={res.returncode}\n{res.stdout[-800:]}"
              f"{res.stderr[-800:]}", flush=True)
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
    sh("export DEBIAN_FRONTEND=noninteractive; dpkg --add-architecture i386")
    sh("export DEBIAN_FRONTEND=noninteractive; apt-get update -qq", timeout=600)
    pkgs = (
        "wine wine64 wine32:i386 cabextract winbind "
        "libvulkan1 libvulkan1:i386 vulkan-tools "
        "mesa-vulkan-drivers mesa-vulkan-drivers:i386 "
        "wmctrl xdotool imagemagick x11-utils p7zip-full"
    )
    r = sh(
        f"export DEBIAN_FRONTEND=noninteractive; "
        f"apt-get install -y -qq --no-install-recommends {pkgs}",
        timeout=1800,
    )
    log(f"apt rc={r.returncode}")
    v = sh("wine --version").stdout.strip()
    log(f"Wine: {v or 'MISSING'}")
    return "wine-" in v


# --------------------------------------------------------------------------
# 2. Download from Google Drive
# --------------------------------------------------------------------------
def drive_total_size():
    """Ask Drive how big the file is (and its real filename)."""
    r = sh(
        f"curl -sIL '{DRIVE_URL}' | tr -d '\\r' | "
        "grep -iE '^(content-length|content-disposition):' | tail -4"
    )
    total, name = 0, "GTA_V_RIP.7z"
    for line in r.stdout.splitlines():
        low = line.lower()
        if low.startswith("content-length:"):
            try:
                total = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif low.startswith("content-disposition:"):
            if "filename=" in line:
                name = line.split("filename=")[-1].strip().strip('"')
    return total, name


def download():
    os.makedirs(os.path.dirname(ARCHIVE), exist_ok=True)
    total, real_name = drive_total_size()
    log(f"Drive reports: {real_name} ({human(total) if total else 'size ?'})")

    for attempt in range(1, 6):
        have = os.path.getsize(ARCHIVE) if os.path.exists(ARCHIVE) else 0
        if total and have >= total:
            log(f"Archive already complete ({human(have)})")
            return True
        log(f"Download attempt {attempt} (have {human(have)})...")
        # -C - resumes; if the server rejects range we restart cleanly
        r = sh(
            f"curl -L --fail --retry 3 --retry-delay 2 -C - "
            f"-o '{ARCHIVE}' '{DRIVE_URL}'",
            timeout=None,
        )
        have = os.path.getsize(ARCHIVE) if os.path.exists(ARCHIVE) else 0
        log(f"After attempt {attempt}: {human(have)} rc={r.returncode}")
        if total and have >= total:
            break
        if have == 0 and attempt == 1:
            sh(f"rm -f '{ARCHIVE}'")

    have = os.path.getsize(ARCHIVE) if os.path.exists(ARCHIVE) else 0
    ok = bool(have) and (not total or have >= total)
    log(f"Download {'OK' if ok else 'INCOMPLETE'}: {human(have)}"
        f"{' / ' + human(total) if total else ''}")
    return ok


# --------------------------------------------------------------------------
# 3. Extract
# --------------------------------------------------------------------------
def extract():
    if not os.path.exists(ARCHIVE):
        log("Archive missing - run the download step first")
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

    with open(LAUNCHER, "w") as f:
        f.write(
            "#!/bin/bash\n"
            "# Grand Theft Auto V launcher (Wine + DXVK) for the Colab desktop\n"
            "set -u\n"
            'export DISPLAY="${DISPLAY:-:1}"\n'
            f'GAMEDIR="{GAME_DATA}"\n'
            f'export WINEPREFIX="${{WINEPREFIX:-{WINEPREFIX}}}"\n'
            "export WINEARCH=win64\n"
            "export WINEDEBUG=-all\n"
            'export WINEDLLOVERRIDES="d3d11,dxgi=n,b"\n'
            'export DXVK_HUD="${DXVK_HUD:-0}"\n'
            'cd "$GAMEDIR" || exit 1\n'
            'if [ ! -d "$WINEPREFIX/drive_c/windows/system32" ]; then\n'
            '  echo "Initialising Wine prefix..."\n'
            "  wineboot -u >/dev/null 2>&1\n"
            "fi\n"
            'echo "Starting Grand Theft Auto V..."\n'
            'exec wine "PlayGTAV.exe" "$@"\n'
        )
    os.chmod(LAUNCHER, 0o755)
    log(f"Launcher: {LAUNCHER}")


# --------------------------------------------------------------------------
# 6. Steam non-Steam shortcut (binary VDF)
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
