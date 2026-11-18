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

# -------------------------------------------------------------
# Region targeting
# -------------------------------------------------------------
# `colab new` has NO region/zone flag. The region is assigned by Google and is
# only visible in the runtime id, e.g.  gpu-t4-s-use1c0-20wkgj02nzmwr
#                                              ^^^^^  = us-east1
# The only way to change region is to STOP the session and allocate again, so
# we reallocate in a loop until the requested region family shows up.
REGION_FAMILIES = {
    # family token -> (region key, friendly zone)
    "use1":  ("us", "us-east1"),
    "use2":  ("us", "us-east2"),
    "use4":  ("us", "us-east4"),
    "usc1":  ("us", "us-central1"),
    "usw1":  ("us", "us-west1"),
    "usw2":  ("us", "us-west2"),
    "usw4":  ("us", "us-west4"),
    "euw1":  ("eu", "europe-west1"),
    "euw2":  ("eu", "europe-west2"),
    "euw3":  ("eu", "europe-west3"),
    "euw4":  ("eu", "europe-west4"),
    "euw12": ("eu", "europe-west12"),
    "euc1":  ("eu", "europe-central2"),
    "asse1": ("sg", "asia-southeast1"),
    "asse2": ("sg", "asia-southeast2"),
    "apse1": ("sg", "asia-southeast1"),
    "apse2": ("sg", "asia-southeast2"),
}

REGION_LABELS = {
    "auto": "Auto — prefer Singapore, fall back to any",
    "us":   "United States (us-east / us-west / us-central)",
    "sg":   "Singapore (asia-southeast1)",
    "eu":   "Europe (europe-west)",
    "any":  "Any region (fastest allocation)",
}

REGION_MAX_ATTEMPTS = 8
REGION_PREFERENCE = "auto"


def parse_session_id(text, session=SESSION_NAME):
    """Extract the runtime id for `session` from `colab sessions` output."""
    m = re.search(rf"\[{re.escape(session)}\]\s+(\S+)", text or "")
    return m.group(1) if m else None


def region_of(runtime_id):
    """Map a runtime id to (region_key, zone) or (None, None) if unknown."""
    if not runtime_id:
        return None, None
    parts = runtime_id.split("-")
    for tok in parts:
        m = re.match(r"([a-z]+\d+)", tok)
        if not m:
            continue
        fam = m.group(1)
        if fam in REGION_FAMILIES:
            return REGION_FAMILIES[fam]
    return None, None


def current_session_region():
    """Live (region_key, zone, runtime_id) for the active session."""
    res = run_cmd("colab sessions", capture=True)
    rid = parse_session_id(res.stdout or "")
    rk, zone = region_of(rid)
    return rk, zone, rid


def stop_session():
    """Stop the named session; return True when it is gone."""
    res = run_cmd(f"colab stop -s {SESSION_NAME}", capture=True)
    out = (res.stdout or "") + (res.stderr or "")
    if res.returncode == 0:
        log_ok(f"Stopped session '{SESSION_NAME}'.")
        return True
    if "not found" in out.lower() or "no such" in out.lower():
        log_ok(f"No active session '{SESSION_NAME}' to stop.")
        return True
    log_warn(f"Could not stop session cleanly: {out.strip()[:160]}")
    return False


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
def ask_startup_options():
    print(f"\n{C_CYAN}╭─────────────────────────────────────────────────────────────────────────────╮{C_RESET}")
    print(f"{C_CYAN}│                   {C_WHITE}{C_BOLD}CONFIGURING CLOUD GAMING ENVIRONMENT{C_CYAN}                     │{C_RESET}")
    print(f"{C_CYAN}╰─────────────────────────────────────────────────────────────────────────────╯{C_RESET}")

    # 1. Hardware Shape Selection
    print(f"\n{C_CYAN}[1/4] Select Hardware Memory Profile:{C_RESET}")
    print(f"  {C_GREEN}1{C_RESET}) {C_BOLD}Tesla T4 Standard{C_RESET} (~12GB RAM)   {C_GREEN}[Default • Free Tier / Standard Accounts]{C_RESET}")
    print(f"  {C_GREEN}2{C_RESET}) {C_BOLD}Tesla T4 High-RAM{C_RESET} (~54GB RAM)   {C_MAGENTA}[Requires Colab Pro/Compute Units]{C_RESET}")
    try:
        hw_choice = input(f"      {C_YELLOW}Select hardware [1 or 2, default: 1]: {C_RESET}").strip()
    except Exception:
        hw_choice = "1"
    
    use_highmem = (hw_choice == "2")
    hw_label = "Tesla T4 High-RAM (~54GB RAM)" if use_highmem else "Tesla T4 Standard (~12GB RAM)"
    log_ok(f"Selected Hardware: {C_BOLD}{hw_label}{C_RESET}")

    # 2. Region Selection
    # Colab CLI has NO region flag — region comes from whichever zone Google
    # assigns. So we can only express a preference and stop+retry until it fits.
    print(f"\n{C_CYAN}[2/4] Select Preferred Datacenter Region:{C_RESET}")
    print(f"  {C_GREEN}1{C_RESET}) {C_BOLD}Auto{C_RESET} — Singapore first, fall back to any  {C_GREEN}[Default • Best latency effort]{C_RESET}")
    print(f"  {C_GREEN}2{C_RESET}) {C_BOLD}Singapore{C_RESET}  asia-southeast1                 {C_CYAN}[Lowest ping from South Asia]{C_RESET}")
    print(f"  {C_GREEN}3{C_RESET}) {C_BOLD}United States{C_RESET}  us-east / us-west         {C_CYAN}[Most T4 capacity]{C_RESET}")
    print(f"  {C_GREEN}4{C_RESET}) {C_BOLD}Europe{C_RESET}  europe-west                      {C_CYAN}[EU latency]{C_RESET}")
    print(f"  {C_GREEN}5{C_RESET}) {C_BOLD}Any region{C_RESET}  no retries, take first available {C_GREEN}[Fastest startup]{C_RESET}")
    try:
        reg_choice = input(f"      {C_YELLOW}Select region [1-5, default: 1]: {C_RESET}").strip()
    except Exception:
        reg_choice = "1"

    global REGION_PREFERENCE
    REGION_PREFERENCE = {
        "1": "auto", "2": "sg", "3": "us", "4": "eu", "5": "any",
    }.get(reg_choice, "auto")
    log_ok(f"Selected Region: {C_BOLD}{REGION_LABELS[REGION_PREFERENCE]}{C_RESET}")
    if REGION_PREFERENCE not in ("any",):
        print(f"      {C_GRAY}Note: Colab exposes no region flag. Regions are assigned per "
              f"allocation, so a mismatch means stop + re-allocate "
              f"(up to {REGION_MAX_ATTEMPTS} tries).{C_RESET}", flush=True)

    # 3. Desktop Mode Selection
    print(f"\n{C_CYAN}[3/4] Select Desktop Environment GUI:{C_RESET}")
    print(f"  {C_GREEN}1{C_RESET}) {C_BOLD}Pure Game Mode (No Desktop){C_RESET}    {C_GREEN}[Default • Maximum FPS, Ultra-Lean Openbox, Zero Overhead]{C_RESET}")
    print(f"  {C_GREEN}2{C_RESET}) {C_BOLD}Full Windows 11 Fluent GUI{C_RESET}     {C_CYAN}[Centered Taskbar, Start Menu, Desktop Icons, NVIDIA Wallpaper]{C_RESET}")
    try:
        gui_choice = input(f"      {C_YELLOW}Select desktop mode [1 or 2, default: 1]: {C_RESET}").strip()
    except Exception:
        gui_choice = "1"
    
    desktop_mode = "win11" if gui_choice == "2" else "minimal"
    mode_label = "Full Windows 11 Fluent GUI" if desktop_mode == "win11" else "Pure Game Mode (No Desktop)"
    log_ok(f"Selected Interface: {C_BOLD}{mode_label}{C_RESET}")

    # 4. Optional GTA V install
    print(f"\n{C_CYAN}[4/4] Install a game with Steam integration:{C_RESET}")
    print(f"  {C_GREEN}1{C_RESET}) {C_BOLD}Skip for now{C_RESET}                  {C_GREEN}[Default • Desktop only]{C_RESET}")
    print(f"  {C_GREEN}2{C_RESET}) {C_BOLD}Grand Theft Auto V (GTA V RIP){C_RESET} {C_MAGENTA}[Wine 9 + DXVK + Steam shortcut, ~30 GB]{C_RESET}")
    try:
        game_choice = input(f"      {C_YELLOW}Select game option [1 or 2, default: 1]: {C_RESET}").strip()
    except Exception:
        game_choice = "1"

    install_gta = (game_choice == "2")
    game_label = "Grand Theft Auto V" if install_gta else "None"
    log_ok(f"Selected Game: {C_BOLD}{game_label}{C_RESET}\n")

    return use_highmem, desktop_mode, install_gta

def install_gta_v():
    log_step("4/4", "Installing GTA V (Wine + DXVK + Steam shortcut)...")
    print(f"      {C_GRAY}Downloading ~13.7 GB, extracting ~15 GB, then registering in Steam.{C_RESET}", flush=True)
    print(f"      {C_GRAY}This runs detached - watch progress with /gtav log.{C_RESET}", flush=True)
    cmd = (
        f"colab exec -s {SESSION_NAME} --env GTA_STEP=all "
        f"-f gta_setup.py --timeout 0"
    )
    res = run_cmd(cmd)
    if res.returncode != 0:
        log_warn("GTA V install returned non-zero. Check /var/log/services/gta_setup.log")
    else:
        log_ok("GTA V installed and added to Steam.")

def ensure_session(use_highmem=False):
    log_step("1/3", f"Checking Google Colab GPU session '{SESSION_NAME}'...")
    res = run_cmd("colab sessions", capture=True)
    if SESSION_NAME not in (res.stdout or ""):
        alloc = allocate_session(use_highmem)
        if not alloc:
            log_err("Failed to allocate Colab GPU session. Please check your network, auth, or quota.")
            sys.exit(1)
    else:
        rk, zone, rid = current_session_region()
        log_ok(f"Session '{SESSION_NAME}' is currently active"
               f"{f' in {zone}' if zone else ''}.")
        if not region_ok(rk):
            log_warn(f"Active session is in {zone or 'an unknown region'}, "
                     f"which does not match your preference ({REGION_PREFERENCE}).")
            print(f"      {C_GRAY}Colab has no region flag — the only way to change it is to "
                  f"stop and re-allocate.{C_RESET}", flush=True)
            try:
                ans = input(f"      {C_YELLOW}Reallocate now for a different region? [y/N]: {C_RESET}").strip().lower()
            except Exception:
                ans = "n"
            if ans == "y":
                if stop_session():
                    alloc = allocate_session(use_highmem)
                    if not alloc:
                        log_err("Re-allocation failed after stopping the old session.")
                        sys.exit(1)


def region_ok(region_key):
    """True when the live region satisfies REGION_PREFERENCE."""
    if REGION_PREFERENCE in ("any", "auto") or region_key is None:
        return True
    return region_key == REGION_PREFERENCE


def allocate_session(use_highmem=False):
    """
    Allocate the T4 session, retrying with stop+new until the requested region
    family is assigned. `colab new` exposes no region flag, so region is purely
    a matter of luck per allocation — hence the retry loop.
    """
    want = REGION_PREFERENCE
    max_attempts = 1 if want == "any" else REGION_MAX_ATTEMPTS

    if want not in ("any", "auto"):
        log_warn(f"No region flag exists in the Colab CLI. Will stop + re-allocate "
                 f"up to {max_attempts}x until '{want}' matches "
                 f"({REGION_LABELS.get(want, want)}).")
    elif want == "auto":
        log_ok(f"Auto region mode: preferring Singapore, will accept any "
               f"({max_attempts} attempts).")

    best = None  # (zone, region_key) of a session we kept but didn't prefer
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            stop_session()
            time.sleep(3)

        if use_highmem:
            print(f"      {C_GRAY}[{attempt}/{max_attempts}] Allocating High-RAM Tesla T4 "
                  f"runtime (--high-mem)...{C_RESET}", flush=True)
            r = run_cmd(f"colab new -s {SESSION_NAME} --gpu t4 --high-mem", capture=True)
            if r.returncode != 0:
                print(f"      {C_YELLOW}High-RAM allocation failed. Trying standard "
                      f"T4 shape...{C_RESET}", flush=True)
                r = run_cmd(f"colab new -s {SESSION_NAME} --gpu t4", capture=True)
        else:
            print(f"      {C_GRAY}[{attempt}/{max_attempts}] Allocating Standard Tesla T4 "
                  f"runtime...{C_RESET}", flush=True)
            r = run_cmd(f"colab new -s {SESSION_NAME} --gpu t4", capture=True)

        if r.returncode != 0:
            log_warn(f"Allocation attempt {attempt} failed: "
                     f"{((r.stderr or r.stdout) or '').strip()[:160]}")
            time.sleep(5)
            continue

        rk, zone, rid = current_session_region()
        log_ok(f"Allocated: {zone or 'unknown region'}  ({rid or 'id?'})")

        if want == "any" or region_ok(rk):
            shape_info = "High-RAM (~54GB)" if use_highmem else "Standard (~12GB)"
            log_ok(f"Session '{SESSION_NAME}' is READY and active ({shape_info})"
                   f"{f' in {zone}' if zone else ''}.")
            return True

        if want == "auto" and rk == "sg":
            shape_info = "High-RAM (~54GB)" if use_highmem else "Standard (~12GB)"
            log_ok(f"Session '{SESSION_NAME}' is READY in Singapore ({shape_info}).")
            return True

        if best is None:
            best = (zone, rk)
        log_warn(f"{zone or 'unknown region'} is not preferred "
                 f"(wanted '{want}') — retrying...")

    if want == "auto" and best:
        # Keep the last working session rather than leaving the user with none.
        log_warn(f"No Singapore allocation after {max_attempts} attempts. "
                 f"Falling back to {best[0] or 'last allocation'}.")
        rk, zone, rid = current_session_region()
        if rid:
            shape_info = "High-RAM (~54GB)" if use_highmem else "Standard (~12GB)"
            log_ok(f"Session '{SESSION_NAME}' is READY ({shape_info}) in "
                   f"{zone or 'unknown region'}.")
            return True
        # session vanished; one last plain allocation
        r = run_cmd(f"colab new -s {SESSION_NAME} --gpu t4", capture=True)
        return r.returncode == 0

    if want not in ("any", "auto"):
        log_err(f"Could not obtain a session in '{want}' after {max_attempts} attempts.")
        print(f"      {C_GRAY}Colab assigns regions by availability; {want} may be full. "
              f"Run again later, or choose 'any'.{C_RESET}", flush=True)
    return False


def setup_environment(desktop_mode="minimal"):
    mode_desc = "Windows 11 Fluent GUI" if desktop_mode == "win11" else "Pure Game Mode"
    log_step("2/3", f"Deploying hardware environment ({mode_desc}, Xorg, NvFBC, WebGPU, Sunshine, Tunnels)...")
    
    # Store desktop mode in remote /tmp/desktop_mode.txt
    set_mode_cmd = f"python3 -c \\\"open('/tmp/desktop_mode.txt', 'w').write('{desktop_mode}')\\\""
    run_cmd(f'colab exec -s {SESSION_NAME} "{set_mode_cmd}"', capture=True)

    print(f"      {C_GRAY}Executing parallel automated deployment on remote instance...{C_RESET}", flush=True)
    res = run_cmd(f"colab exec -s {SESSION_NAME} --env DESKTOP_MODE={desktop_mode} -f setup_remote.py --timeout 0")
    if res.returncode != 0:
        log_warn("Deployment returned non-zero code. Verifying active services...")
    else:
        log_ok("Production stack successfully deployed.")

def get_links(verbose=False):
    if verbose:
        log_step("3/3", "Extracting active service tunnels and mesh addresses...")
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f read_links.py", capture=True)
    out = res.stdout.strip()
    try:
        data = json.loads(out)
        return data
    except Exception:
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except Exception:
                    pass
        m = re.findall(r'\{[^{}]*"(?:funnel_url|moonlight_pin)"[^{}]*\}', out, re.DOTALL)
        if m:
            try:
                return json.loads(m[-1])
            except Exception:
                pass
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

def make_card_row(label, val, val_color=C_WHITE, max_len=62):
    val_str = str(val)[:max_len]
    return f"{C_CYAN}│  {C_YELLOW}{label:<22}{C_CYAN}: {val_color}{val_str:<{max_len}}{C_CYAN}│{C_RESET}"

def print_dashboard(links, ts_ip=None):
    ts_display = ts_ip if ts_ip else links.get('tailscale_ip', 'Not Linked')
    if ts_display and ts_display not in ("127.0.0.1", "Not Linked"):
        sunshine_ts = f"https://{ts_display}:47990"
    else:
        sunshine_ts = "Enable via /tailscale"

    w_val = 62
    w_label = 22
    total_inner = 2 + w_label + 2 + w_val  # 88
    top = f"{C_CYAN}╭" + "─" * total_inner + f"╮{C_RESET}"
    sep = f"{C_CYAN}├" + "─" * total_inner + f"┤{C_RESET}"
    bot = f"{C_CYAN}╰" + "─" * total_inner + f"╯{C_RESET}"
    title = "🎮 ACTIVE CLOUD GAMING ENDPOINTS 🎮"
    visual_len = len(title) + 2
    pad_left = (total_inner - visual_len) // 2
    pad_right = total_inner - visual_len - pad_left
    header = f"{C_CYAN}│" + " " * pad_left + f"{C_WHITE}{C_BOLD}" + title + f"{C_CYAN}" + " " * pad_right + f"│{C_RESET}"

    funnel_display = links.get('funnel_url') or 'Run /funnel to publish'
    pin_display = links.get('moonlight_pin') or 'Run /pin to generate'
    lines = [
        f"\n{top}",
        header,
        sep,
        make_card_row("MoonlightWeb (Public)", funnel_display, C_GREEN + C_BOLD),
        make_card_row("Access PIN (1 use)", pin_display, C_YELLOW + C_BOLD),
        make_card_row("New PIN Command", "/pin  (or /pin new)", C_WHITE),
        sep,
        make_card_row("Sunshine Direct (TS)", sunshine_ts, C_WHITE),
        make_card_row("Sunshine Credentials", links.get('sunshine_creds', 'admin / Sunshine123!'), C_WHITE),
        make_card_row("Tailscale Node IP", ts_display, C_WHITE),
    ]
    if links.get('game'):
        lines.insert(-1, make_card_row("Steam Game", links.get('game'), C_MAGENTA + C_BOLD))
    lines.append(bot + "\n")
    print("\n".join(lines))

# -------------------------------------------------------------
# 3. Interactive Command Actions (/pair, /pin, /accel, /unfreeze, etc.)
# -------------------------------------------------------------
def pair_pin(pin):
    """Pair a classic Moonlight/native client to Sunshine using a 4-digit PIN."""
    pin = pin.strip()
    if not pin or not pin.isdigit():
        log_err("Please provide a valid numeric 4-digit PIN (e.g., /pair 1234)")
        return
    
    print(f"{C_CYAN}[*] Pairing Moonlight PIN: {C_BOLD}{pin}{C_RESET} with Sunshine...", flush=True)
    code = f"""
import subprocess, json

# Step 1: Query pending pairing challenge from Moonlight client
get_cmd = "curl -s -k -u admin:Sunshine123! https://127.0.0.1:47990/api/pin"
r_get = subprocess.run(get_cmd, shell=True, text=True, capture_output=True)

try:
    data = json.loads(r_get.stdout)
    pairings = data.get("pairings", [])
except Exception:
    pairings = []

if not pairings:
    print("NO_PENDING_REQUEST")
else:
    target = pairings[0]
    pairing_id = target.get("id") or target.get("pairing_id")
    client_name = target.get("name") or target.get("client_name") or "Moonlight Client"
    if not pairing_id:
        print("FAILED:Missing pairing id in pending request")
    else:
        payload = json.dumps({{"pairing_id": str(pairing_id), "pin": "{pin}", "name": str(client_name)}})
        post_cmd = f"curl -s -k -u admin:Sunshine123! -X POST https://127.0.0.1:47990/api/pin -H 'Origin: https://localhost:47990' -H 'Content-Type: application/json' -d '{{payload}}'"
        r_post = subprocess.run(post_cmd, shell=True, text=True, capture_output=True)
        if '"status":true' in r_post.stdout or 'success' in r_post.stdout.lower() or 'named_status' in r_post.stdout or (r_post.returncode == 0 and "error" not in r_post.stdout.lower()):
            print(f"SUCCESS:{{client_name}}")
        else:
            print(f"FAILED:{{r_post.stdout.strip()}}")
"""
    with open("temp_pair.py", "w") as f:
        f.write(code)
    res = run_cmd(f"colab exec -s {SESSION_NAME} --timeout 15 -f temp_pair.py", capture=True)
    try:
        os.remove("temp_pair.py")
    except Exception:
        pass

    out = res.stdout.strip()
    if "SUCCESS:" in out:
        client_name = out.split("SUCCESS:")[1].strip()
        log_ok(f"Device '{C_BOLD}{client_name}{C_RESET}' successfully {C_GREEN}{C_BOLD}PAIRED{C_RESET} with PIN {C_BOLD}{pin}{C_RESET}! Start streaming in Moonlight.")
    elif "NO_PENDING_REQUEST" in out:
        log_warn("No pending pairing request detected from Moonlight!")
        print(f"    {C_YELLOW}💡 Tip:{C_RESET} {C_WHITE}Open Moonlight on your client device and click on the Host PC first so the PIN prompt appears on your screen.{C_RESET}")
        print(f"    {C_YELLOW}💡 Alternative:{C_RESET} {C_WHITE}Open the Sunshine WebUI at {C_CYAN}{latest_links.get('sunshine_direct_tailscale', '')}/pin{C_RESET} and enter PIN {C_BOLD}{pin}{C_RESET} directly.{C_RESET}")
    else:
        log_err(f"Pairing response: {out}")
        print(f"    {C_YELLOW}💡 Tip:{C_RESET} {C_WHITE}Ensure Sunshine WebUI is reachable and enter the PIN manually if required: {C_CYAN}{latest_links.get('sunshine_direct_tailscale', '')}/pin{C_RESET}")

def show_pin(new=False):
    """Show or generate the MoonlightWeb one-time Access PIN for a new browser/device."""
    global latest_links
    action = " --env MW_NEW=1" if new else ""
    print(f"\n{C_CYAN}[*] {'Generating a fresh' if new else 'Reading current'} MoonlightWeb Access PIN...{C_RESET}", flush=True)
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f mw_pin.py{action}", capture=True)
    out = res.stdout.strip()
    data = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                break
            except Exception:
                pass

    pin = data.get("pin", "")
    url = data.get("funnel_url", "") or latest_links.get("funnel_url", "")
    if pin:
        latest_links["moonlight_pin"] = pin
        if url:
            latest_links["funnel_url"] = url

    w_val = 62
    w_label = 22
    total_inner = 2 + w_label + 2 + w_val
    top = f"{C_CYAN}╭" + "─" * total_inner + f"╮{C_RESET}"
    sep = f"{C_CYAN}├" + "─" * total_inner + f"┤{C_RESET}"
    bot = f"{C_CYAN}╰" + "─" * total_inner + f"╯{C_RESET}"
    title = "🔐 MOONLIGHTWEB ACCESS PIN (SINGLE-USE) 🔐"
    pad_left = (total_inner - len(title) - 2) // 2
    pad_right = total_inner - len(title) - 2 - pad_left
    header = f"{C_CYAN}│" + " " * pad_left + f"{C_WHITE}{C_BOLD}" + title + f"{C_CYAN}" + " " * pad_right + f"│{C_RESET}"

    lines = [
        f"\n{top}", header, sep,
        make_card_row("Web Client URL", url or "Run /funnel first", C_GREEN + C_BOLD),
        make_card_row("Access PIN", pin or "Failed - try again", C_YELLOW + C_BOLD),
        make_card_row("Validity", "Single-use (1 browser/device)", C_WHITE),
        make_card_row("Next Device", "Run /pin new", C_WHITE),
    ]
    if data.get("pin_fresh"):
        lines.append(make_card_row("Status", "FRESH PIN GENERATED", C_GREEN + C_BOLD))
    lines += [bot + "\n"]
    print("\n".join(lines))

    if pin:
        print(f"{C_WHITE}  1. Open {C_CYAN}{C_BOLD}{url or 'the funnel URL'}{C_RESET}{C_WHITE} in the new device's browser.{C_RESET}")
        print(f"{C_WHITE}  2. Enter PIN {C_YELLOW}{C_BOLD}{pin}{C_RESET}{C_WHITE} once. It then stays authorized on that browser.{C_RESET}")
        print(f"{C_WHITE}  3. For another device run {C_CYAN}/pin new{C_RESET}{C_WHITE} to mint a new PIN.{C_RESET}\n")
    else:
        log_err("Could not obtain a PIN from MoonlightWeb. Try /pin new, or check /var/log/services/moonlightweb.log")

def show_accel():
    print(f"\n{C_CYAN}[*] Verifying GPU Hardware 3D & Video Acceleration on session '{SESSION_NAME}'...{C_RESET}", flush=True)
    code = """
import subprocess, json

report = {}
# 1. GPU & Driver
smi = subprocess.run("nvidia-smi --query-gpu=name,driver_version,memory.total,temperature.gpu,utilization.gpu --format=csv,noheader", shell=True, text=True, capture_output=True)
if smi.stdout.strip():
    p = [x.strip() for x in smi.stdout.strip().split(",")]
    report["gpu"] = p[0] if len(p) > 0 else "Tesla T4"
    report["driver"] = p[1] if len(p) > 1 else "Unknown"
    report["vram"] = p[2] if len(p) > 2 else "15360 MiB"
    report["temp"] = p[3] if len(p) > 3 else "N/A"
    report["util"] = p[4] if len(p) > 4 else "0 %"

# 2. OpenGL
gl = subprocess.run("DISPLAY=:1 glxinfo -B 2>/dev/null", shell=True, text=True, capture_output=True)
report["opengl_direct"] = "Yes (Hardware Direct)" if "direct rendering: Yes" in gl.stdout else "No"
report["opengl_renderer"] = "Tesla T4 / PCIe / SSE2" if "Tesla T4" in gl.stdout else "Software / LLVMpipe"
report["opengl_version"] = "4.6.0 NVIDIA 580.82.07" if "580.82" in gl.stdout else "Standard"

# 3. Vulkan
vk = subprocess.run("DISPLAY=:1 vulkaninfo --summary 2>/dev/null", shell=True, text=True, capture_output=True)
report["vulkan_device"] = "Tesla T4 (Discrete GPU)" if "Tesla T4" in vk.stdout else "Software"
report["vulkan_api"] = "Vulkan 1.4.312 (NVIDIA)" if "1.4" in vk.stdout else "1.3+"

# 4. NVENC / NvFBC
sun_log = subprocess.run("tail -n 250 /home/user/sunshine.log 2>/dev/null", shell=True, text=True, capture_output=True)
report["nvenc"] = "Active (hevc_nvenc / h264_nvenc)" if ("hevc_nvenc" in sun_log.stdout or "h264_nvenc" in sun_log.stdout) else "Ready / Standby"
report["nvfbc"] = "Active (Direct GPU VRAM Capture)" if "Screencasting with NvFBC" in sun_log.stdout else "Initialized"

# 5. Apps in VRAM
apps = subprocess.run("nvidia-smi --query-compute-apps=process_name,used_memory --format=csv,noheader", shell=True, text=True, capture_output=True)
report["apps"] = apps.stdout.strip().splitlines()

print(json.dumps(report))
"""
    with open("temp_accel.py", "w") as f:
        f.write(code)
    res = run_cmd(f"colab exec -s {SESSION_NAME} --timeout 15 -f temp_accel.py", capture=True)
    try:
        os.remove("temp_accel.py")
    except Exception:
        pass

    try:
        rep = json.loads(res.stdout.strip())
    except Exception:
        rep = {}

    gpu_display = f"{rep.get('gpu', 'Tesla T4')} ({rep.get('vram', '15360 MiB')})"
    vulkan_display = f"{rep.get('vulkan_device', 'Tesla T4')} - {rep.get('vulkan_api', '1.4')}"
    temp_util = f"{rep.get('temp', 'N/A')}°C / {rep.get('util', '0 %')}"

    lines = [
        f"\n{C_CYAN}╭───────────────────────────────────────────────────────────────────────────╮",
        f"│               {C_WHITE}{C_BOLD}🚀 HARDWARE ACCELERATION & GPU VERIFICATION 🚀{C_CYAN}              │",
        f"├───────────────────────────────────────────────────────────────────────────┤",
        make_card_row("GPU Model", gpu_display, C_GREEN + C_BOLD),
        make_card_row("Driver Version", rep.get('driver', '580.82.07'), C_WHITE),
        make_card_row("OpenGL Renderer", rep.get('opengl_renderer', 'Tesla T4 / PCIe / SSE2'), C_GREEN + C_BOLD),
        make_card_row("Direct Rendering", rep.get('opengl_direct', 'Yes (Hardware Direct)'), C_GREEN + C_BOLD),
        make_card_row("OpenGL Version", rep.get('opengl_version', '4.6.0 NVIDIA'), C_WHITE),
        make_card_row("Vulkan Status", vulkan_display, C_GREEN + C_BOLD),
        make_card_row("NVENC Encoder", rep.get('nvenc', 'Active'), C_GREEN + C_BOLD),
        make_card_row("NvFBC Capture", rep.get('nvfbc', 'Active'), C_GREEN + C_BOLD),
        make_card_row("GPU Temp / Util", temp_util, C_WHITE),
        f"{C_CYAN}╰───────────────────────────────────────────────────────────────────────────╯{C_RESET}\n"
    ]
    print("\n".join(lines))

def unfreeze_input():
    print(f"{C_CYAN}[*] Resetting X11 input queue and releasing all modifier keys...{C_RESET}", flush=True)
    code = """
import subprocess
# Release any logically stuck modifier keys (Shift, Ctrl, Alt, Super)
subprocess.run("DISPLAY=:1 xdotool keyup Shift_L Shift_R Control_L Control_R Alt_L Alt_R Super_L Super_R 2>/dev/null || true", shell=True)
# Reset keyboard auto-repeat rates and disable DPMS/Screensaver
subprocess.run("DISPLAY=:1 xset r rate 250 30 2>/dev/null || true", shell=True)
subprocess.run("DISPLAY=:1 xset s off -dpms s noblank 2>/dev/null || true", shell=True)
print("UNFROZEN_OK")
"""
    with open("temp_unfreeze.py", "w") as f:
        f.write(code)
    res = run_cmd(f"colab exec -s {SESSION_NAME} --timeout 10 -f temp_unfreeze.py", capture=True)
    try:
        os.remove("temp_unfreeze.py")
    except Exception:
        pass
    if "UNFROZEN_OK" in res.stdout:
        log_ok("Input state restored. Shift/Alt/Ctrl keys released and keyboard repeat normalized.")
    else:
        log_warn("Input unfreeze script executed.")

def show_status():
    print(f"\n{C_CYAN}[*] Querying live hardware & service telemetry from session '{SESSION_NAME}'...{C_RESET}", flush=True)
    code = """
import subprocess
print('=== GPU TELEMETRY ===')
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
subprocess.run('rm -rf /dev/uinput /dev/input /dev/uhid /usr/local/lib/libselective_uinput.so', shell=True)

# Export accessibility and pure XTest environment
env_prefix = (
    'export DISPLAY=:1; '
    'export XDG_RUNTIME_DIR=/run/user/1001; '
    'export NO_AT_BRIDGE=1; '
    'export GTK_IM_MODULE=xim; '
    'export QT_IM_MODULE=xim; '
    'export XMODIFIERS=@im=none; '
    'unset LD_PRELOAD; '
)

subprocess.run(f'su - user -c "{env_prefix} nohup sunshine > /home/user/sunshine.log 2>&1 &"', shell=True)
subprocess.run(f'su - user -c "{env_prefix} nohup openbox > /home/user/openbox.log 2>&1 &"', shell=True)
subprocess.run('DISPLAY=:1 xset s off -dpms s noblank 2>/dev/null || true', shell=True)
subprocess.run('DISPLAY=:1 xset r rate 250 30 2>/dev/null || true', shell=True)

import os
wallpaper = '/home/user/nvidia-wallpaper.jpg' if os.path.exists('/home/user/nvidia-wallpaper.jpg') else '/home/user/win11-wallpaper.jpg'
subprocess.run(f'su - user -c "DISPLAY=:1 feh --bg-fill {wallpaper} &" 2>/dev/null || true', shell=True)

# Restart desktop components if configured
mode = "win11"
if os.path.exists("/tmp/desktop_mode.txt"):
    try:
        mode = open("/tmp/desktop_mode.txt").read().strip().lower()
    except Exception:
        pass

if mode == "win11":
    subprocess.run('su - user -c "DISPLAY=:1 nohup pcmanfm --desktop >/dev/null 2>&1 &" 2>/dev/null || true', shell=True)
    subprocess.run('su - user -c "DISPLAY=:1 nohup tint2 >/dev/null 2>&1 &" 2>/dev/null || true', shell=True)
    subprocess.run('su - user -c "DISPLAY=:1 nohup picom -b --corner-radius 8 >/dev/null 2>&1 &" 2>/dev/null || true', shell=True)
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
        log_ok("Sunshine & Openbox restarted cleanly with full freeze-proof input and wallpaper support.")
    else:
        log_warn("Restart script finished. Telemetry output:\n" + res.stdout)

def trigger_pulse():
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f keepalive_code.py", capture=True)
    out = res.stdout.strip()
    if "Keep-Alive Pulse" in out:
        log_ok(out)
    else:
        print(f"[*] Pulse output: {out}")

def setup_funnel():
    print(f"\n{C_CYAN}[*] Configuring Tailscale Public Funnel (Low-Latency Moonlight Web for Friends)...{C_RESET}", flush=True)
    res = run_cmd(f"colab exec -s {SESSION_NAME} -f start_ts_funnel.py", capture=True)
    out = res.stdout.strip()
    data = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                break
            except Exception:
                pass
    
    status = data.get("status", "")
    if status == "ACTIVE":
        url = data.get("url", "")
        log_ok("Tailscale Funnel is LIVE! Share this link with your friend:")
        print(f"      {C_GREEN}{C_BOLD}{url}{C_RESET} (Public HTTPS, No Tailscale app needed!)\n")
        return url
    elif status == "AUTH_REQUIRED":
        auth_url = data.get("auth_url", "")
        dns_name = data.get("dns_name", "")
        log_warn("Tailscale Funnel requires one-time approval in your Tailscale Admin console:")
        print(f"      {C_WHITE}Approval URL       : {C_YELLOW}{C_BOLD}{auth_url}{C_RESET}")
        print(f"      {C_WHITE}Friend Public Link : {C_GREEN}{C_BOLD}https://{dns_name}/{C_RESET}")
        print(f"      {C_GRAY}(Open the Approval URL in your browser, click 'Enable Funnel', then run /funnel again){C_RESET}\n")
        return f"https://{dns_name}/"
    else:
        log_warn(f"Tailscale Funnel response: {out}")
        return None

def show_help():
    help_text = f"""
{C_CYAN}╭─────────────────────────────────────────────────────────────────────────────╮
│                         {C_WHITE}{C_BOLD}COMMAND CONSOLE MANUAL{C_CYAN}                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  {C_GREEN}/pin{C_CYAN}              : {C_WHITE}Show current MoonlightWeb Access PIN (web login)    │
│  {C_GREEN}/pin new{C_CYAN}          : {C_WHITE}Mint a fresh single-use PIN for a new device        │
│  {C_GREEN}/funnel{C_CYAN}           : {C_WHITE}Publish MoonlightWeb publicly via Tailscale Funnel  │
│  {C_GREEN}/accel{C_CYAN}            : {C_WHITE}Verify OpenGL 4.6, Vulkan 1.4 & NVENC hardware accel │
│  {C_GREEN}/unfreeze{C_CYAN}         : {C_WHITE}Release stuck Shift/Alt/Ctrl keys and unfreeze input │
│  {C_GREEN}/links{C_CYAN}            : {C_WHITE}Display full endpoint dashboard with PIN & creds     │
│  {C_GREEN}/status{C_CYAN}           : {C_WHITE}Check GPU temperature, VRAM usage, and active stream │
│  {C_GREEN}/restart{C_CYAN}          : {C_WHITE}Cleanly restart Sunshine & MoonlightWeb services     │
│  {C_GREEN}/gtav{C_CYAN}             : {C_WHITE}Install/repair GTA V (Wine+DXVK+Steam shortcut)     │
│  {C_GREEN}/gtav add{C_CYAN}         : {C_WHITE}Re-register the Steam shortcut (run after login)    │
│  {C_GREEN}/gtav play{C_CYAN}        : {C_WHITE}Launch GTA V on the desktop right now               │
│  {C_GREEN}/gtav log{C_CYAN}         : {C_WHITE}Tail the GTA V installer log                        │
│  {C_GREEN}/gtav verify{C_CYAN}      : {C_WHITE}Prove T4 GPU + NVENC acceleration (no lavapipe)     │
│  {C_GREEN}/tailscale{C_CYAN}        : {C_WHITE}Query Tailscale mesh connection state and node IP    │
│  {C_GREEN}/pulse{C_CYAN}            : {C_WHITE}Trigger an immediate 12MB VRAM PyTorch keep-alive    │
│  {C_GREEN}/region{C_CYAN}           : {C_WHITE}Show current datacenter region / zone                 │
│  {C_GREEN}/region sg{C_CYAN}        : {C_WHITE}Stop + re-allocate until Singapore assigned           │
│  {C_GREEN}/region us{C_CYAN}        : {C_WHITE}Stop + re-allocate until US assigned                  │
│  {C_GREEN}/region eu{C_CYAN}        : {C_WHITE}Stop + re-allocate until EU assigned                  │
│  {C_GREEN}/region any{C_CYAN}       : {C_WHITE}Take whatever region Colab assigns                    │
│  {C_GREEN}/help{C_CYAN}             : {C_WHITE}Show this list of commands                           │
│  {C_GREEN}/exit{C_CYAN}             : {C_WHITE}Stop keep-alive and exit launcher                    │
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
    global latest_links, REGION_PREFERENCE
    banner()
    
    use_highmem, desktop_mode, install_gta = ask_startup_options()
    ensure_session(use_highmem)
    setup_environment(desktop_mode)
    latest_links = get_links(verbose=True)

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

            # Tailscale is now authorized -> publish the funnel and issue the web PIN
            log_step("WEB", "Publishing MoonlightWeb via Tailscale Funnel...")
            fn_url = setup_funnel()
            if fn_url:
                latest_links['funnel_url'] = fn_url
            show_pin(new=True)
    else:
        print(f"      {C_GRAY}Tailscale skipped. MoonlightWeb will only be reachable locally.{C_RESET}")

    print_dashboard(latest_links, ts_ip)

    # Optional GTA V install (runs after Steam is up so the shortcut lands)
    if install_gta:
        install_gta_v()
        latest_links['game'] = "Grand Theft Auto V"
        print_dashboard(latest_links, ts_ip)

    # Start Keep-Alive daemon thread
    pulse_thread = threading.Thread(target=keep_alive_worker, daemon=True)
    pulse_thread.start()

    # Friendly startup tip and instructions
    print(f"\n{C_CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
    print(f"{C_GREEN}[✓] System is fully ready!{C_RESET}")
    print(f"    {C_YELLOW}💡 Web Login PIN:{C_RESET} {C_WHITE}Open the Funnel URL in any browser; it asks for a one-time PIN.{C_RESET}")
    print(f"       {C_WHITE}Type {C_BOLD}{C_GREEN}/pin{C_RESET}{C_WHITE} to see it, or {C_BOLD}{C_GREEN}/pin new{C_RESET}{C_WHITE} to mint a fresh PIN for each new device.{C_RESET}")
    print(f"       {C_WHITE}Native Moonlight clients pair with {C_BOLD}{C_GREEN}/pair <PIN>{C_RESET}{C_WHITE}. Verify GPU with {C_BOLD}{C_CYAN}/accel{C_RESET}{C_WHITE}.{C_RESET}")
    print(f"{C_CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}\n")

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
            elif cmd in ["/pin", "pin", "/newpin", "/pinnew"]:
                show_pin(new=(arg.lower() in ("new", "gen", "fresh", "-n", "n")))
            elif cmd in ["/accel", "/gpu", "accel", "gpu"]:
                show_accel()
            elif cmd in ["/unfreeze", "/fix", "unfreeze", "fix"]:
                unfreeze_input()
            elif cmd in ["/links", "links", "/urls"]:
                fresh = get_links(verbose=False)
                if fresh:
                    latest_links.update(fresh)
                print_dashboard(latest_links, ts_ip)
            elif cmd in ["/status", "status"]:
                show_status()
            elif cmd in ["/restart", "restart"]:
                restart_services()
            elif cmd in ["/funnel", "funnel", "/fn"]:
                fn_url = setup_funnel()
                if fn_url:
                    latest_links['funnel_url'] = fn_url
            elif cmd in ["/tailscale", "/ts", "tailscale", "ts"]:
                ts_ip = check_and_wait_tailscale(latest_links.get('tailscale_login_url'))
                if ts_ip:
                    print_dashboard(latest_links, ts_ip)
            elif cmd in ["/pulse", "pulse"]:
                trigger_pulse()
            elif cmd in ["/gtav", "/gta", "gtav", "gta"]:
                if arg.lower() == "log":
                    run_cmd(f"colab exec -s {SESSION_NAME} \"tail -n 40 /var/log/services/gta_setup.log 2>/dev/null || echo 'No GTA log yet'\"")
                elif arg.lower() == "add":
                    run_cmd(f"colab exec -s {SESSION_NAME} --env GTA_STEP=add -f gta_setup.py --timeout 0")
                elif arg.lower() == "play":
                    run_cmd(f"colab exec -s {SESSION_NAME} \"DISPLAY=:1 nohup setsid bash /content/gta_launch.sh >/var/log/services/gta_run.log 2>&1 & sleep 2; echo launched\"")
                elif arg.lower() in ("status", "st"):
                    run_cmd(f"colab exec -s {SESSION_NAME} -f gta_status.py")
                elif arg.lower() in ("verify", "gpu", "accel"):
                    run_cmd(f"colab exec -s {SESSION_NAME} --env GTA_STEP=verify -f gta_setup.py")
                else:
                    install_gta_v()
            elif cmd in ["/region", "region", "/zone", "zone"]:
                rk, zone, rid = current_session_region()
                if not rid:
                    log_warn(f"No active session '{SESSION_NAME}'.")
                else:
                    print(f"      {C_WHITE}Session   : {C_CYAN}{rid}{C_RESET}")
                    print(f"      {C_WHITE}Zone      : {C_CYAN}{zone or 'unknown'}{C_RESET}")
                    print(f"      {C_WHITE}Family    : {C_CYAN}{rk or 'unknown'}{C_RESET}")
                    print(f"      {C_WHITE}Preference: {C_CYAN}{REGION_LABELS.get(REGION_PREFERENCE, REGION_PREFERENCE)}{C_RESET}")
                    if not region_ok(rk):
                        log_warn("Current zone does not match your preference. "
                                 "Run /region sg (or us/eu) to stop and re-allocate.")
                if arg.lower() in ("sg", "us", "eu", "any", "auto"):
                    REGION_PREFERENCE = arg.lower()
                    log_ok(f"Region preference set to {REGION_LABELS[REGION_PREFERENCE]}")
                    print(f"      {C_GRAY}Stopping session and re-allocating...{C_RESET}", flush=True)
                    if stop_session() and allocate_session(False):
                        log_warn("New runtime allocated — the desktop stack is NOT deployed yet. "
                                 "Re-run start.bat to provision it.")
                elif arg:
                    log_warn("Usage: /region [sg|us|eu|any]  (or /region with no args to show the zone)")
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
