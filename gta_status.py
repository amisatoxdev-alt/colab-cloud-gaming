#!/usr/bin/env python3
"""gta_status.py - report the state of the GTA V install on the Colab VM."""
import glob
import os
import subprocess


def sh(cmd):
    return subprocess.run(cmd, shell=True, text=True,
                          capture_output=True).stdout.strip()


def size(path):
    out = sh(f"du -sh '{path}' 2>/dev/null")
    return out.split("\t")[0] if out else "-"


GAME_ROOT = "/content/SteamLibrary/steamapps/common/Grand Theft Auto V"
ARCHIVE = "/content/games/GTA_V_RIP.7z"
LOG = "/var/log/services/gta_setup.log"
STEAM_ROOT = "/home/user/.steam/debian-installation"


def main():
    print("=" * 60)
    print(" GTA V INSTALL STATUS")
    print("=" * 60)

    print(f" archive    : {size(ARCHIVE) if os.path.exists(ARCHIVE) else 'not downloaded'}")
    print(f" game dir   : {size(GAME_ROOT) if os.path.isdir(GAME_ROOT) else 'missing'}")
    print(f" wine       : {sh('wine --version') or 'NOT INSTALLED'}")

    exe = glob.glob(f"{GAME_ROOT}/**/PlayGTAV.exe", recursive=True)
    print(f" PlayGTAV   : {exe[0] if exe else 'not found'}")

    dxvk = os.path.exists(f"{GAME_ROOT}/gta_data/d3d11.dll")
    print(f" DXVK d3d11 : {'installed' if dxvk else 'missing'}")

    print(f" launcher   : {'yes' if os.path.exists('/content/gta_launch.sh') else 'no'}")

    ids = [p for p in glob.glob(f"{STEAM_ROOT}/userdata/*")
           if os.path.basename(p).isdigit() and os.path.basename(p) != "0"]
    print(f" steam user : {ids[0].split('/')[-1] if ids else 'NOT LOGGED IN'}")
    if ids:
        p = f"{ids[0]}/config/shortcuts.vdf"
        print(f" shortcut   : {'present' if os.path.exists(p) else 'missing'}")
    else:
        pend = "/content/steam_shortcut_pending/shortcuts.vdf"
        print(f" shortcut   : {'pending (log into Steam, then /gtav add)' if os.path.exists(pend) else 'missing'}")

    running = sh("pgrep -af 'GTA5.exe|PlayGTAV.exe' | head -2")
    print(f" running    : {running if running else 'no'}")

    print(f" disk free  : {sh('df -h /content | tail -1 | awk \"{print \\$4}\"')}")
    print("-" * 60)
    tail = sh(f"tail -n 12 {LOG} 2>/dev/null")
    print(" last log:")
    print(tail if tail else "  (no log yet)")
    print("=" * 60)


if __name__ == "__main__":
    main()
