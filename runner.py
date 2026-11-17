import subprocess
import time
import sys
import json
import os
import re
import threading
import ctypes

# -------------------------------------------------------------
# 1. Windows Terminal ANSI Colors Initialization
# -------------------------------------------------------------
if sys.platform == "win32":
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ANSI Palette
C_RESET   = "\033[0m"
C_BOLD    = "\033[1m"
C_DIM     = "\033[2m"
C_CYAN    = "\033[96m"
C_GREEN   = "\033[92m"
C_YELLOW  = "\033[93m"
C_MAGENTA = "\033[95m"
C_RED     = "\033[91m"
C_WHITE   = "\033[97m"
C_GRAY    = "\033[90m"

SESSION_NAME = "t4"
KEEP_ALIVE_INTERVAL = 120
stop_event = threading.Event()
latest_links = {}

def banner():
    art = f"""{C_CYAN}{C_BOLD}
  ██████╗ ██████╗ ██╗      █████╗ ██████╗      ██████╗ ██╗      ██████╗ ██╗   ██╗██████╗ 
 ██╔════╝██╔═══██╗██║     ██╔══██╗██╔══██╗    ██╔════╝ ██║     ██╔═══██╗██║   ██║██╔══██╗
 ██║     ██║   ██║██║     ███████║██████╔╝    ██║      ██║     ██║   ██║██║   ██║██║  ██║
 ██║     ██║   ██║██║     ██╔══██║██╔══██╗    ██║      ██║     ██║   ██║██║   ██║██║  ██║
 ╚██████╗╚██████╔╝███████╗██║  ██║██████╔╝    ╚██████╗ ███████╗╚██████╔╝╚██████╔╝██████╔╝
  ╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝╚═════╝      ╚═════╝ ╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝ 
{C_MAGENTA}          [ ULTRA-LOW LATENCY CLOUD GAMING ORCHESTRATOR • TESLA T4 ]{C_RESET}
"""
    print(art)

def log_step(step, msg):
    print(f"{C_CYAN}[{step}]{C_RESET} {C_WHITE}{msg}{C_RESET}", flush=True)

def log_ok(msg):
    print(f"{C_GREEN}[✓]{C_RESET} {msg}", flush=True)

def log_warn(msg):
    print(f"{C_YELLOW}[!]{C_RESET} {msg}", flush=True)

def log_err(msg):
    print(f"{C_RED}[✗]{C_RESET} {msg}", flush=True)

def run_cmd(cmd, capture=False):
    if capture:
        return subprocess.run(cmd, shell=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    else:
        return subprocess.run(cmd, shell=True)

# -------------------------------------------------------------
# 2. Session Lifecycle & Remote Provisioning
# -------------------------------------------------------------
def ensure_session():
    log_step("1/3", f"Checking Google Colab GPU session '{SESSION_NAME}'...")
    res = run_cmd("colab sessions", capture=True)
    if SESSION_NAME not in res.stdout:
        print(f"      {C_GRAY}Session not found. Allocating fresh Tesla T4 runtime...{C_RESET}", flush=True)
        res_new = run_cmd(f"colab new -s {SESSION_NAME} --gpu t4")
        if res_new.returncode != 0:
            log_err("Failed to allocate Colab GPU session. Please check your network or auth.")
            sys.exit(1)
        log_ok(f"Session '{SESSION_NAME}' is READY and active.")
    else:
        log_ok(f"Session '{SESSION_NAME}' is currently active.")

def setup_environment():
    log_step("2/3", "Deploying hardware environment (Xorg, NvFBC, WebGPU, Sunshine, Tunnels)...")
    print(f"      {C_GRAY}Executing parallel automated deployment on remote instance...{C_RESET}", flush=True)
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f setup_remote.py --timeout 0")
    if res.returncode != 0:
        log_warn("Deployment returned non-zero code. Verifying active services...")
    else:
        log_ok("Production stack successfully deployed.")

def get_links():
    log_step("3/3", "Extracting active service tunnels and mesh addresses...")
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f read_links.py", capture=True)
    try:
        data = json.loads(res.stdout.strip())
        return data
    except Exception:
        return {}

def check_and_wait_tailscale(ts_login_url):
    print(f"\n{C_CYAN}--- TAILSCALE MESH NETWORK STATUS ---{C_RESET}")
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f check_ts.py", capture=True)
    
    if "STATUS:AUTHORIZED" in res.stdout:
        lines = res.stdout.replace("STATUS:AUTHORIZED", "").strip().split("\n")
        first_line = lines[0] if lines else ""
        parts = first_line.split()
        ip = parts[0] if parts else "100.x.y.z"
        log_ok(f"Tailscale is AUTHORIZED! Colab Node IP: {C_GREEN}{C_BOLD}{ip}{C_RESET}")
        return ip

    if not ts_login_url or "login.tailscale.com" not in ts_login_url:
        m = re.search(r'https://login\.tailscale\.com/a/[a-zA-Z0-9]+', res.stdout)
        if m:
            ts_login_url = m.group(0)

    if ts_login_url and "login.tailscale.com" in ts_login_url:
        print(f"  {C_YELLOW}Authorization Required:{C_RESET} {C_WHITE}{C_BOLD}{ts_login_url}{C_RESET}")
        print(f"  {C_GRAY}Waiting for authentication approval (checks every 5s)...{C_RESET}", end="", flush=True)
        for _ in range(60):
            time.sleep(5)
            res = run_cmd(f"colab exec -s {SESSION_NAME} -f check_ts.py", capture=True)
            if "STATUS:AUTHORIZED" in res.stdout:
                lines = res.stdout.replace("STATUS:AUTHORIZED", "").strip().split("\n")
                first_line = lines[0] if lines else ""
                parts = first_line.split()
                ip = parts[0] if parts else "100.x.y.z"
                print(f"\n{C_GREEN}[✓] Authorized! Node IP: {C_BOLD}{ip}{C_RESET}")
                return ip
            else:
                print(".", end="", flush=True)
        print(f"\n{C_YELLOW}[!] Tailscale wait timed out.{C_RESET}")
    return None

def print_dashboard(links, ts_ip=None):
    desktop_url = links.get('desktop_url', '')
    if desktop_url and "vnc.html" not in desktop_url:
        desktop_url = desktop_url.rstrip('/') + '/vnc.html?autoconnect=true&resize=remote'

    ts_display = ts_ip if ts_ip else links.get('tailscale_ip', 'Not Linked')
    if ts_display and ts_display != "127.0.0.1" and ts_display != "Not Linked":
        sunshine_ts = f"https://{ts_display}:47990"
    else:
        sunshine_ts = "Enable via /tailscale"

    card = f"""
{C_CYAN}╭─────────────────────────────────────────────────────────────────────────────╮
│                     {C_WHITE}{C_BOLD}🎮 ACTIVE CLOUD GAMING ENDPOINTS 🎮{C_CYAN}                     │
├─────────────────────────────────────────────────────────────────────────────┤
│  {C_YELLOW}Moonlight Host IP{C_CYAN}    : {C_GREEN}{C_BOLD}{ts_display:<56}{C_CYAN}│
│  {C_YELLOW}Direct Sunshine URL{C_CYAN}  : {C_WHITE}{sunshine_ts:<56}{C_CYAN}│
│  {C_YELLOW}Public Sunshine WebUI{C_CYAN}: {C_WHITE}{links.get('sunshine_url', 'Pending'):<56}{C_CYAN}│
│  {C_YELLOW}Sunshine Credentials{C_CYAN} : {C_WHITE}{links.get('sunshine_creds', 'admin / Sunshine123!'):<56}{C_CYAN}│
│  {C_YELLOW}Interactive Desktop{C_CYAN}  : {C_WHITE}{desktop_url:<56}{C_CYAN}│
│  {C_YELLOW}Moonlight Web Client{C_CYAN} : {C_WHITE}{links.get('moonlight_url', 'Pending'):<56}{C_CYAN}│
╰─────────────────────────────────────────────────────────────────────────────╯{C_RESET}
"""
    print(card)

# -------------------------------------------------------------
# 3. Interactive Command Actions (/pair, /status, /restart, etc.)
# -------------------------------------------------------------
def pair_pin(pin):
    pin = pin.strip()
    if not pin or not pin.isdigit():
        log_err("Please provide a valid numeric 4-digit PIN (e.g., /pair 1234)")
        return
    
    print(f"{C_CYAN}[*] Pairing Moonlight PIN: {C_BOLD}{pin}{C_RESET} with Sunshine...", flush=True)
    code = f"""
import subprocess, json
creds = ['admin:Sunshine123!', 'admin:admin']
success = False
for c in creds:
    cmd = f"curl -s -k -u {{c}} -X POST https://127.0.0.1:47990/api/pin -H 'Content-Type: application/json' -d '{json.dumps({'pin': pin, 'name': 'moonlight'})}'"
    r = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    if '"status":true' in r.stdout or 'success' in r.stdout.lower() or 'named_status' in r.stdout:
        print('SUCCESS')
        success = True
        break
if not success:
    print('FAILED')
"""
    with open("temp_pair.py", "w") as f:
        f.write(code)
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f temp_pair.py", capture=True)
    try:
        os.remove("temp_pair.py")
    except Exception:
        pass

    if "SUCCESS" in res.stdout:
        log_ok(f"Device successfully {C_BOLD}PAIRED{C_RESET} with PIN {C_BOLD}{pin}{C_RESET}! Start streaming in Moonlight.")
    else:
        log_err(f"Pairing failed. Make sure Moonlight is open and displaying the PIN prompt. (Output: {res.stdout.strip()})")

def show_status():
    print(f"\n{C_CYAN}[*] Querying live hardware & service telemetry from session '{SESSION_NAME}'...{C_RESET}", flush=True)
    code = """
import subprocess
print('=== GPU STATS ===')
subprocess.run('nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader', shell=True)
print('=== STREAMING PROCESS ===')
subprocess.run('ps aux | grep -w sunshine | grep -v grep', shell=True)
print('=== ACTIVE SESSIONS ===')
subprocess.run('ss -tulpn | grep 47989 || true', shell=True)
"""
    with open("temp_stat.py", "w") as f:
        f.write(code)
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f temp_stat.py", capture=True)
    try:
        os.remove("temp_stat.py")
    except Exception:
        pass
    print(f"{C_WHITE}{res.stdout.strip()}{C_RESET}\n")

def restart_services():
    print(f"{C_CYAN}[*] Restarting Sunshine & Openbox desktop cleanly...{C_RESET}", flush=True)
    code = """
import subprocess, time
subprocess.run('pkill -9 -x -u user sunshine 2>/dev/null || true', shell=True)
subprocess.run('killall -9 zenity steam 2>/dev/null || true', shell=True)
time.sleep(1)
subprocess.run('rm -f /dev/uinput /dev/uhid', shell=True)
subprocess.run('su - user -c "export DISPLAY=:1; export XDG_RUNTIME_DIR=/run/user/1001; nohup sunshine > /home/user/sunshine.log 2>&1 &"', shell=True)
subprocess.run('su - user -c "DISPLAY=:1 nohup openbox > /home/user/openbox.log 2>&1 &"', shell=True)
subprocess.run('su - user -c "DISPLAY=:1 feh --bg-fill /home/user/nvidia-wallpaper.jpg &"', shell=True)
print('RESTARTED_OK')
"""
    with open("temp_restart.py", "w") as f:
        f.write(code)
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f temp_restart.py", capture=True)
    try:
        os.remove("temp_restart.py")
    except Exception:
        pass
    if "RESTARTED_OK" in res.stdout:
        log_ok("Sunshine & Openbox restarted cleanly with full controller & wallpaper support.")
    else:
        log_warn("Restart script finished. Telemetry output:\n" + res.stdout)

def trigger_pulse():
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f keepalive_code.py", capture=True)
    out = res.stdout.strip()
    if "Keep-Alive Pulse" in out:
        log_ok(out)
    else:
        print(f"[*] Pulse output: {out}")

def show_help():
    help_text = f"""
{C_CYAN}╭─────────────────────────────────────────────────────────────────────────────╮
│                         {C_WHITE}{C_BOLD}COMMAND CONSOLE MANUAL{C_CYAN}                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  {C_GREEN}/pair <4-digit PIN>{C_CYAN} : {C_WHITE}Instantly pair Moonlight client (e.g. /pair 1234)   │
│  {C_GREEN}/links{C_CYAN}              : {C_WHITE}Display all active stream, WebUI, and desktop URLs   │
│  {C_GREEN}/status{C_CYAN}             : {C_WHITE}Check GPU temperature, VRAM usage, and active stream │
│  {C_GREEN}/restart{C_CYAN}            : {C_WHITE}Cleanly restart Sunshine & Openbox without VM reboot │
│  {C_GREEN}/tailscale{C_CYAN}          : {C_WHITE}Query Tailscale mesh connection state and node IP    │
│  {C_GREEN}/pulse{C_CYAN}              : {C_WHITE}Trigger an immediate 12MB VRAM PyTorch keep-alive    │
│  {C_GREEN}/help{C_CYAN}               : {C_WHITE}Show this list of commands                           │
│  {C_GREEN}/exit{C_CYAN}               : {C_WHITE}Stop keep-alive and exit launcher                    │
╰─────────────────────────────────────────────────────────────────────────────╯{C_RESET}
"""
    print(help_text)

# -------------------------------------------------------------
# 4. Background Keep-Alive Pulse Daemon
# -------------------------------------------------------------
def keep_alive_worker():
    while not stop_event.is_set():
        stop_event.wait(KEEP_ALIVE_INTERVAL)
        if stop_event.is_set():
            break
        try:
            res = run_cmd(f"colab exec -s {SESSION_NAME} -f keepalive_code.py", capture=True)
            out = res.stdout.strip()
            if "Keep-Alive Pulse" in out:
                # Print discreet pulse timestamp in terminal
                t_str = time.strftime("%H:%M:%S")
                print(f"\n{C_GRAY}[{t_str}] ⚡ VRAM Pulse Active (~12MB tensor warm){C_RESET}\n{C_CYAN}[colab-gaming] > {C_RESET}", end="", flush=True)
        except Exception:
            pass

# -------------------------------------------------------------
# 5. Main Interactive Shell
# -------------------------------------------------------------
def main():
    global latest_links
    banner()
    
    ensure_session()
    setup_environment()
    latest_links = get_links()

    # Tailscale interactive prompt
    print(f"\n{C_CYAN}[?]{C_RESET} {C_WHITE}Tailscale provides direct, encrypted, jitter-free 60FPS streaming (~80-110ms).{C_RESET}")
    try:
        user_choice = input(f"{C_YELLOW}[?]{C_RESET} {C_BOLD}Use Tailscale for Ultra-Low Latency streaming? [Recommended: Y] (Y/n): {C_RESET}").strip().lower()
    except Exception:
        user_choice = "y"

    ts_ip = None
    if user_choice in ["", "y", "yes"]:
        ts_ip = check_and_wait_tailscale(latest_links.get('tailscale_login_url'))
        if ts_ip:
            log_step("TS", f"Synchronizing Sunshine with active Tailscale IP: {ts_ip}...")
            run_cmd(f"colab exec -s {SESSION_NAME} -f sync_ts_ip.py", capture=True)
            log_ok("Sunshine configuration synced with Tailscale.")
    else:
        print(f"      {C_GRAY}Tailscale skipped. Using Cloudflare tunnels.{C_RESET}")

    print_dashboard(latest_links, ts_ip)

    # Start Keep-Alive daemon thread
    pulse_thread = threading.Thread(target=keep_alive_worker, daemon=True)
    pulse_thread.start()

    print(f"{C_GREEN}[✓] System is fully ready! Type {C_BOLD}/pair <PIN>{C_RESET}{C_GREEN} to connect Moonlight or {C_BOLD}/help{C_RESET}{C_GREEN} for commands.{C_RESET}\n")

    # Interactive live command loop
    while True:
        try:
            cmd_input = input(f"{C_CYAN}[colab-gaming] > {C_RESET}").strip()
            if not cmd_input:
                continue

            parts = cmd_input.split()
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd in ["/pair", "pair"]:
                if not arg:
                    arg = input(f"{C_YELLOW}[?] Enter 4-digit Moonlight PIN: {C_RESET}").strip()
                pair_pin(arg)
            elif cmd in ["/links", "links", "/urls"]:
                print_dashboard(latest_links, ts_ip)
            elif cmd in ["/status", "status"]:
                show_status()
            elif cmd in ["/restart", "restart"]:
                restart_services()
            elif cmd in ["/tailscale", "/ts", "tailscale", "ts"]:
                ts_ip = check_and_wait_tailscale(latest_links.get('tailscale_login_url'))
                if ts_ip:
                    print_dashboard(latest_links, ts_ip)
            elif cmd in ["/pulse", "pulse"]:
                trigger_pulse()
            elif cmd in ["/help", "help", "/?"]:
                show_help()
            elif cmd in ["/exit", "exit", "/quit", "quit", "q"]:
                print(f"{C_YELLOW}[*] Shutting down launcher orchestrator...{C_RESET}")
                stop_event.set()
                break
            else:
                print(f"{C_RED}[!] Unknown command '{cmd_input}'. Type /help for available commands.{C_RESET}")
        except (KeyboardInterrupt, EOFError):
            print(f"\n{C_YELLOW}[*] Exiting interactive console...{C_RESET}")
            stop_event.set()
            break

if __name__ == "__main__":
    main()
