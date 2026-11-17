import subprocess
import time
import sys
import json
import os
import re

# Ensure standard output does not crash on Windows console encoding
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SESSION_NAME = "t4"
KEEP_ALIVE_INTERVAL = 120  # 2 minutes

def run_cmd(cmd, capture=False):
    if capture:
        return subprocess.run(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    else:
        return subprocess.run(cmd, shell=True)

def ensure_session():
    print("[*] Checking active Colab sessions...", flush=True)
    res = run_cmd("colab sessions", capture=True)
    if SESSION_NAME not in res.stdout:
        print(f"[*] Session '{SESSION_NAME}' not found. Provisioning T4 GPU session...", flush=True)
        res_new = run_cmd(f"colab new -s {SESSION_NAME} --gpu t4")
        if res_new.returncode != 0:
            print("[!] Failed to create Colab session. Please check your network or auth.", flush=True)
            sys.exit(1)
        print(f"[OK] Session '{SESSION_NAME}' is READY.", flush=True)
    else:
        print(f"[OK] Session '{SESSION_NAME}' is active.", flush=True)

def setup_environment():
    print(f"[*] Running automated daemon setup on session '{SESSION_NAME}'...", flush=True)
    print("    (Checking Vulkan, OpenGL, Chrome, Cloudflared, Sunshine, Moonlight Web, Tailscale, noVNC)", flush=True)
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f setup_remote.py --timeout 0")
    if res.returncode != 0:
        print("[!] Setup script finished with return code non-zero, checking services...", flush=True)

def get_links():
    print("[*] Fetching service links from session...", flush=True)
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f read_links.py", capture=True)
    try:
        data = json.loads(res.stdout.strip())
        return data
    except Exception:
        return {}

def check_and_wait_tailscale(ts_login_url):
    print("\n" + "="*65)
    print("[*] Checking Tailscale status...", flush=True)
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f check_ts.py", capture=True)
    
    if "STATUS:AUTHORIZED" in res.stdout:
        lines = res.stdout.replace("STATUS:AUTHORIZED", "").strip().split("\n")
        first_line = lines[0] if lines else ""
        parts = first_line.split()
        ip = parts[0] if parts else "100.x.y.z"
        print(f"[OK] Tailscale is AUTHORIZED! Colab Node IP: {ip}", flush=True)
        return ip

    if not ts_login_url or "login.tailscale.com" not in ts_login_url:
        m = re.search(r'https://login\.tailscale\.com/a/[a-zA-Z0-9]+', res.stdout)
        if m:
            ts_login_url = m.group(0)

    if ts_login_url and "login.tailscale.com" in ts_login_url:
        print(f"[*] Authorization Required: -> {ts_login_url}")
        print("[*] Waiting for Tailscale authorization (checks every 5s)...", flush=True)
        for i in range(60): # 5 minutes
            time.sleep(5)
            res = run_cmd(f"colab exec -s {SESSION_NAME} -f check_ts.py", capture=True)
            if "STATUS:AUTHORIZED" in res.stdout:
                lines = res.stdout.replace("STATUS:AUTHORIZED", "").strip().split("\n")
                first_line = lines[0] if lines else ""
                parts = first_line.split()
                ip = parts[0] if parts else "100.x.y.z"
                print(f"\n[OK] Tailscale is AUTHORIZED! Colab Node IP: {ip}", flush=True)
                return ip
            else:
                print(".", end="", flush=True)
        print("\n[!] Tailscale wait timed out.")
    return None

def keep_alive_loop():
    print("\n" + "="*65)
    print("=== KEEP-ALIVE PULSE ACTIVATED (Interval: Every 2 Minutes) ===")
    print("=== Running lightweight GPU kernel computation (~12MB VRAM) ===")
    print("=== Keep this window open. Press Ctrl+C at any time to stop. ===")
    print("="*65, flush=True)
    
    while True:
        try:
            time.sleep(KEEP_ALIVE_INTERVAL)
            res = run_cmd(f"colab exec -s {SESSION_NAME} -f keepalive_code.py", capture=True)
            out = res.stdout.strip()
            if "Keep-Alive Pulse" in out:
                print(out, flush=True)
            elif "lost (404/401)" in out or "No active sessions" in out:
                print("\n[!] Session disconnected or lost. Re-provisioning...", flush=True)
                ensure_session()
                setup_environment()
            else:
                if out:
                    print(f"[*] Pulse output: {out}", flush=True)
        except KeyboardInterrupt:
            print("\n[*] Keep-alive loop stopped by user.")
            break
        except Exception as e:
            print(f"[!] Error in keep-alive loop: {e}", flush=True)

def main():
    ensure_session()
    setup_environment()
    links = get_links()
    
    desktop_url = links.get('desktop_url', '')
    if desktop_url and "vnc.html" not in desktop_url:
        desktop_url = desktop_url.rstrip('/') + '/vnc.html?autoconnect=true&resize=remote'

    print("\n" + "="*70)
    print("           🌟 ALL CLOUD GAMING SERVICES ARE LIVE & ACTIVE 🌟")
    print("="*70)
    print(f"  [Sunshine Web UI]       : {links.get('sunshine_url')}")
    print(f"  [Sunshine Credentials]  : {links.get('sunshine_creds', 'admin / Sunshine123!')}")
    print(f"  [Interactive Desktop]   : {desktop_url}")
    print(f"  [Moonlight Web Client]  : {links.get('moonlight_url')}")
    print("="*70)

    # Ask user if they want to activate Tailscale for ultra-low latency
    print("\n[?] Tailscale provides direct, encrypted, jitter-free 60FPS streaming (~80-110ms).")
    try:
        user_choice = input("[?] Use Tailscale for Ultra-Low Latency streaming? [Recommended: Y] (Y/n): ").strip().lower()
    except Exception:
        user_choice = "y"

    ts_ip = None
    if user_choice in ["", "y", "yes"]:
        ts_ip = check_and_wait_tailscale(links.get('tailscale_login_url'))
        if ts_ip:
            print(f"[*] Synchronizing Sunshine with active Tailscale IP: {ts_ip}...", flush=True)
            run_cmd(f"colab exec -s {SESSION_NAME} -f sync_ts_ip.py", capture=True)
            print("\n" + "-"*70)
            print(f"  --> Moonlight Host IP   : {ts_ip}")
            print(f"  --> Direct Sunshine URL : https://{ts_ip}:47990")
            print(f"  --> Quick Pair Command  : pair.bat <4-digit PIN>")
            print("-"*70)
    else:
        print("[*] Tailscale skipped. Using Cloudflare Web links for streaming.")

    keep_alive_loop()

if __name__ == "__main__":
    main()
