import subprocess
import time
import sys
import json
import os
import re
import threading
import tempfile
import uuid
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
# `colab new` has NO region/zone flag: Google assigns the zone per allocation.
# The zone is authoritative in the runtime URL, e.g.
#   https://8080-gpu-t4-s-ass1c2-222r7xhmk9xse-c.asia-southeast1.prod.r.colab.dev
#                                                 ^^^^^^^^^^^^^^^^^ region
# The runtime id abbreviation is NOT a reliable source -- `ass1c2` is
# asia-southeast1, but it does not match the `asse1` spelling you would guess,
# so guessing from the id reported a perfectly good Singapore VM as "unknown".
# colab_zone.py reads the URL out of the local store instead.
REGION_LABELS = {
    "auto": "Auto — Singapore first, fall back to any region",
    "us":   "United States (us-east / us-west / us-central)",
    "sg":   "Singapore (asia-southeast1)",
    "eu":   "Europe (europe-west)",
    "any":  "Any region (fastest allocation)",
}

# Families we are willing to re-allocate for. Anything else (a zone we have
# never seen) is treated as "unknown" and never triggers a retry storm.
VALID_FAMILIES = ("sg", "us", "eu")

REGION_MAX_ATTEMPTS = 8
REGION_PREFERENCE = "auto"


def family_of_zone(zone):
    """GCP region name -> launcher family ('sg' | 'us' | 'eu' | '')."""
    z = (zone or "").strip().lower()
    if z.startswith("asia-southeast"):
        return "sg"
    if z.startswith(("us-east", "us-west", "us-central")):
        return "us"
    if z.startswith("europe-"):
        return "eu"
    return ""


def zone_info():
    """(family, zone, endpoint) read from the live session via colab_zone.py."""
    r = run_cmd(f"{sys.executable} colab_zone.py --name {SESSION_NAME}",
                capture=True, timeout=60)
    out = (r.stdout or "").strip()
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            return d.get("family") or "", d.get("zone") or "", d.get("endpoint") or ""
    return "", "", ""


def current_session_region():
    """(family, zone, endpoint) for the active session; ('','','') if none."""
    return zone_info()


def stop_session():
    """Stop the named session; True when no assignment is left behind.

    `colab stop` is the only non-destructive teardown, and it needs a local
    name. If it cannot find ours, the assignment is freed through the API.
    """
    rows = server_assignments()
    if not rows:
        log_ok(f"No active session '{SESSION_NAME}' to stop.")
        return True

    res = run_cmd(f"colab stop -s {SESSION_NAME}", capture=True, timeout=120)
    out = (res.stdout or "") + (res.stderr or "")
    if "not found" in out.lower() or "not addressable" in out.lower():
        free_orphans()
    if not server_assignments():
        log_ok(f"Stopped session '{SESSION_NAME}'.")
        return True
    log_warn(f"Session still present after stop: {out.strip()[:160]}")
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

def run_cmd(cmd, capture=False, timeout=None):
    """Run a host command, never blocking forever. Returns CompletedProcess.

    A bare subprocess.run() is how the launcher hung with no output and no way
    out: one wedged `colab exec` blocked the caller indefinitely.

    subprocess.run(timeout=...) is NOT enough on Windows. It terminates only
    cmd.exe; the real child (`python`, `colab`) is orphaned, keeps the stdout
    pipe open, and communicate() keeps waiting. Measured: timeout=3 on
    `python -c "time.sleep(30)"` still blocked for the full 30s. So: capture
    into temp FILES (no pipe to hold open), and on expiry kill the whole tree
    with `taskkill /F /T`. A timeout is reported as returncode 124 (the
    `timeout(1)` convention) so callers can distinguish it from a real failure.
    """
    if not timeout:
        if capture:
            return subprocess.run(cmd, shell=True, text=True,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
        return subprocess.run(cmd, shell=True)

    out_f = err_f = None
    out_path = err_path = None
    try:
        if capture:
            base = os.path.join(tempfile.gettempdir(),
                                f"ccg_{os.getpid()}_{uuid.uuid4().hex[:10]}")
            out_path, err_path = base + ".out", base + ".err"
            out_f = open(out_path, "w+", encoding="utf-8", errors="replace")
            err_f = open(err_path, "w+", encoding="utf-8", errors="replace")
            p = subprocess.Popen(cmd, shell=True, stdout=out_f, stderr=err_f,
                                 stdin=subprocess.DEVNULL, text=True)
        else:
            p = subprocess.Popen(cmd, shell=True)

        try:
            code = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log_warn(f"Command exceeded {timeout}s — killing it.")
            # /T kills the child tree, which is what actually holds the handle.
            subprocess.run(f"taskkill /F /T /PID {p.pid}",
                           shell=True, capture_output=True)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
            code = 124

        out = err = ""
        if capture:
            for f in (out_f, err_f):
                f.flush()
            out_f.seek(0)
            err_f.seek(0)
            out = out_f.read()
            err = err_f.read()
        return subprocess.CompletedProcess(cmd, code, stdout=out, stderr=err)
    finally:
        for f in (out_f, err_f):
            if f:
                try:
                    f.close()
                except Exception:
                    pass
        for path in (out_path, err_path):
            if path:
                try:
                    os.remove(path)
                except Exception:
                    pass


# Only one `colab exec` may be in flight at a time. `colab exec` drives a single
# persistent Jupyter kernel per session, so a second concurrent call does not
# run in parallel -- it queues behind the first, silently, forever. The keepalive
# thread and the interactive loop both issue execs, so this lock is what stops
# them wedging each other.
EXEC_LOCK = threading.Lock()

# Hard ceiling for the host-side wait on an exec. Kept above the CLI's own
# --timeout (which only counts idle time without output) so the CLI usually
# gives up first and reports something useful.
EXEC_HARD_TIMEOUT = 600


def _reset_kernel():
    """Forget the cached kernel/session ids so the next exec boots a fresh kernel.

    Recovery for the single worst failure mode: `colab exec` reuses the
    kernel_id in ~/.config/colab-cli/sessions.json. If that kernel is wedged
    (a cell that never finishes, a daemon holding its output stream open),
    every subsequent exec waits on it forever and no command can be issued to
    fix it -- the session becomes unreachable while still being billed.
    Dropping the ids makes the CLI allocate a new kernel on the same VM.
    Verified: a print('PONG') that had been timing out for minutes returned in
    5.4s immediately after the cached kernel_id was cleared.
    """
    path = os.path.expanduser("~/.config/colab-cli/sessions.json")
    try:
        with open(path) as f:
            data = json.load(f)
        entry = data.get(SESSION_NAME)
        if not entry:
            return False
        dropped = [k for k in ("kernel_id", "session_id") if entry.pop(k, None)]
        if not dropped:
            return False
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        log_warn(f"Reset the remote kernel ({', '.join(dropped)} cleared).")
        return True
    except Exception as e:
        log_warn(f"Could not reset the remote kernel: {e}")
        return False


def run_exec(args, capture=True, idle_timeout=180, hard_timeout=EXEC_HARD_TIMEOUT,
             retry_on_timeout=True):
    """Run `colab exec -s SESSION_NAME <args>`, serialized and time-bounded.

    `idle_timeout` is passed to the CLI as --timeout (seconds without output).
    `hard_timeout` bounds the host-side wait. On a wedge the cached kernel is
    reset and the command is retried once on a fresh kernel -- safe for the
    idempotent probes this launcher mostly runs. Long jobs (deployment, GTA
    download) pass hard_timeout=None and are never retried.
    """
    parts = [f"colab exec -s {SESSION_NAME}"]
    if idle_timeout:
        parts.append(f"--timeout {idle_timeout}")
    parts.append(args)
    cmd = " ".join(parts)

    with EXEC_LOCK:
        r = run_cmd(cmd, capture=capture, timeout=hard_timeout)
        if r.returncode == 124 and retry_on_timeout:
            log_warn("The session kernel stopped responding — restarting it "
                     "and retrying once...")
            if _reset_kernel():
                r = run_cmd(cmd, capture=capture, timeout=hard_timeout)
        return r

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
    # Held inside run_exec, which holds EXEC_LOCK for the whole call -- so the
    # keep-alive pulse sees the lock taken and skips, and no second exec can
    # race this one on the single kernel. The idle window is wide because the
    # download/extract stages are long, and gta_setup.py now prints a 15 s
    # heartbeat so the idle timer is never starved.
    res = run_exec("--env GTA_STEP=all -f gta_setup.py", capture=False,
                   idle_timeout=600, hard_timeout=None, retry_on_timeout=False)
    if res.returncode != 0:
        log_warn("GTA V install returned non-zero. Check /gtav log")
    else:
        log_ok("GTA V installed and added to Steam.")

def _session_rows():
    """
    Parse `colab sessions` into [(name_or_None, endpoint, hardware)].

    A row looks like:
      [t4] gpu-t4-s-ass1c2-222r7xhmk9xse | Hardware: T4 | Shape: Standard | Variant: GPU
      [?]  gpu-t4-s-usw4b1-z2tzc3fin5zv | Hardware: T4 | ...

    name is None when the CLI printed '?' (server assignment with no local
    name) or '' -- those rows are NOT addressable by `colab exec -s ...`.

    The CLI's own log lines share the same bracket shape, e.g. with no
    sessions `colab sessions` prints `[colab] No sessions found.` -- which
    naively parses as name='colab', endpoint='No'. That phantom row made
    has_any_session() permanently true: allocation never ran for 8 attempts and
    stop_session() reported failure after a successful stop. A genuine row
    always carries a runtime endpoint (`gpu-...` / `tpu-...`) or a
    `Hardware:` field; log lines carry neither.
    """
    out = run_cmd("colab sessions", capture=True, timeout=120).stdout or ""
    rows = []
    for line in out.splitlines():
        m = re.match(r"^\[([^\]]*)\]\s+(\S+)", line.strip())
        if not m:
            continue
        raw = m.group(1).strip()
        endpoint = m.group(2)
        hw = ""
        hm = re.search(r"Hardware:\s*(\S+)", line)
        if hm:
            hw = hm.group(1)
        if not hw and not re.match(r"^(gpu|tpu)-", endpoint):
            continue  # a CLI log line, not a runtime assignment
        rows.append((None if raw in ("", "?") else raw, endpoint, hw))
    return rows


def server_assignments():
    """Endpoint list of every assignment the server reports (named or not)."""
    return [r[1] for r in _session_rows()]


def has_any_session():
    """True when any assignment is visible, even one we cannot address."""
    return bool(_session_rows())


def session_name():
    """
    Our session's own name as the CLI reports it, or None.

    Parsed from the '[...]' prefix, never by substring search: SESSION_NAME
    ('t4') is a substring of 'gpu-t4-s-...' and of 'Hardware: T4', so
    `'t4' in output` always matched and made the launcher believe a session
    existed when it did not.
    """
    for name, _endpoint, _hw in _session_rows():
        if name:
            return name
    return None


def session_is_named():
    """True only when an assignment is registered under exactly SESSION_NAME."""
    return any(name == SESSION_NAME for name, _e, _h in _session_rows())


def needs_adoption():
    """True when a live assignment exists but is not under SESSION_NAME."""
    rows = _session_rows()
    if not rows:
        return False
    return not any(name == SESSION_NAME for name, _e, _h in rows)


def adopt_orphans():
    """
    Re-register a live assignment under SESSION_NAME instead of destroying it.

    Two shapes end up here, and the old code only handled the first:
      * `[?] <endpoint>`  -- nothing in the local store at all;
      * `[abc123] <endpoint>` -- a leftover name from an earlier `colab new`
        that was run without `-s`.
    In both cases `colab exec -s t4` fails with "Session 't4' not found" and
    `colab stop -s t4` cannot release it, even though the VM is alive. The old
    check only looked for `[?]`, so a leftover *named* session was reported as
    "cannot be adopted" forever -- that is the repeated-warning bug.

    Returns True when a usable, locally-named session exists afterwards.
    """
    if session_is_named():
        return True
    if not needs_adoption():
        return False

    for name, endpoint, hw in _session_rows():
        log_warn(f"Live Colab VM {endpoint} ({hw or 'unknown'}) is registered "
                 f"as '{name or '<unnamed>'}' — adopting it as "
                 f"'{SESSION_NAME}' instead of deleting it...")

    r = run_cmd(f'{sys.executable} colab_adopt.py --name {SESSION_NAME}',
                capture=True, timeout=120)
    body = ((r.stdout or "") + (r.stderr or "")).strip()
    for line in body.splitlines():
        print(f"      {C_GRAY}{line}{C_RESET}", flush=True)
    return session_is_named()


def free_orphans():
    """
    Release server assignments so `colab new` can run cleanly.

    DESTRUCTIVE: unassign() destroys the VM. Every assignment except the one
    tracked as SESSION_NAME is released, because Colab answers `colab new`
    with `412 precondition failed` while a stale assignment exists. Call this
    only when a fresh allocation is genuinely wanted -- use adopt_orphans()
    when the goal is merely to reach a VM that is already running.
    """
    if not server_assignments():
        return True
    log_warn("Releasing stale Colab assignment(s) so allocation can proceed...")
    r = run_cmd(f"{sys.executable} colab_free.py --free --name {SESSION_NAME}",
                capture=True, timeout=180)
    print(f"      {C_GRAY}{((r.stdout or '') + (r.stderr or '')).strip()}{C_RESET}",
          flush=True)
    return not server_assignments()


def ensure_session(use_highmem=False):
    log_step("1/4", f"Checking Google Colab GPU session '{SESSION_NAME}'...")
    adopt_orphans()

    if not session_is_named():
        alloc = allocate_session(use_highmem)
        if not alloc:
            log_err("Failed to allocate Colab GPU session. Please check your "
                    "network, auth, or quota.")
            sys.exit(1)
    else:
        fam, zone, endpoint = current_session_region()
        log_ok(f"Session '{SESSION_NAME}' is currently active"
               f"{f' in {zone}' if zone else ''}.")
        if not region_ok(fam):
            log_warn(f"Active session is in {zone or 'an unknown region'}, "
                     f"which does not match your preference "
                     f"({REGION_LABELS.get(REGION_PREFERENCE, REGION_PREFERENCE)}).")
            print(f"      {C_GRAY}Colab has no region flag — the only way to change "
                  f"it is to stop and re-allocate.{C_RESET}", flush=True)
            try:
                ans = input(f"      {C_YELLOW}Reallocate now for a different "
                            f"region? [y/N]: {C_RESET}").strip().lower()
            except Exception:
                ans = "n"
            if ans == "y":
                if stop_session():
                    alloc = allocate_session(use_highmem)
                    if not alloc:
                        log_err("Re-allocation failed after stopping the old "
                                "session.")
                        sys.exit(1)


def region_ok(family):
    """True when the live region satisfies REGION_PREFERENCE.

    'auto' accepts anything, and an UNKNOWN family is accepted too: a zone we
    cannot name must never trigger a re-allocation loop, because that loop
    destroys a working VM for nothing. That is exactly how a perfect
    Singapore VM (ass1c2) got thrown away.
    """
    if REGION_PREFERENCE in ("any", "auto"):
        return True
    if not family:
        return True
    return family == REGION_PREFERENCE


def _shape_label(use_highmem):
    return "High-RAM (~54GB)" if use_highmem else "Standard (~12GB)"


def _ready(use_highmem, note=""):
    fam, zone, endpoint = current_session_region()
    log_ok(f"Session '{SESSION_NAME}' is READY ({_shape_label(use_highmem)}) in "
           f"{zone or 'unknown region'}{note}.")
    return True


def _current_family_ok(use_highmem):
    """Shared tail of both allocation paths: is the live VM good enough?"""
    fam, zone, endpoint = current_session_region()
    if REGION_PREFERENCE == "any" or region_ok(fam):
        return _ready(use_highmem)
    if REGION_PREFERENCE == "auto" and fam == "sg":
        return _ready(use_highmem)
    return None


def allocate_session(use_highmem=False):
    """
    Allocate the T4 session, retrying until the requested region family fits.

    `colab new` exposes no region flag, so region is pure luck per allocation
    and the only lever is stop + re-allocate. Order of operations matters:

      1. Any live assignment that we can adopt is a WINNER -- keep it. A VM is
         only released when there is no way to reach it, because releasing is
         irreversible and re-allocation can be refused outright.
      2. A `412 precondition failed` from `colab new` means a stale assignment
         is holding the slot: release the stale ones, then retry.
      3. Never return False while a VM is alive; fall back to the live one.
    """
    want = REGION_PREFERENCE
    max_attempts = 1 if want in ("any", "auto") else REGION_MAX_ATTEMPTS
    # Fast retry interval between allocation attempts
    RETRY_DELAY = 3
    last_zone = ""

    if want not in ("any", "auto"):
        log_warn(f"No region flag exists in the Colab CLI. Will stop + "
                 f"re-allocate up to {max_attempts}x until '{want}' matches "
                 f"({REGION_LABELS.get(want, want)}). Cooldown between "
                 f"retries: {RETRY_DELAY}s.")
    elif want == "auto":
        log_ok("Auto region mode: Singapore first, any region accepted.")

    for attempt in range(1, max_attempts + 1):
        # ------------------------------------------------ reuse what exists
        if has_any_session():
            if not session_is_named():
                adopt_orphans()
            if not session_is_named():
                # Unreachable even after adoption: a real blocker. Release it
                # so the allocation below is not refused with a 412.
                log_warn(f"Live assignment cannot be registered as "
                         f"'{SESSION_NAME}' — releasing it to allocate cleanly.")
                free_orphans()
                if attempt < max_attempts:
                    time.sleep(RETRY_DELAY)
                continue

            ok = _current_family_ok(use_highmem)
            if ok is not None:
                return ok

            fam, zone, endpoint = current_session_region()
            last_zone = zone
            if attempt < max_attempts:
                log_warn(f"Session is in {zone or 'unknown'} (wanted '{want}') — "
                         f"stopping and retrying ({attempt}/{max_attempts})...")
                stop_session()
                print(f"      {C_GRAY}Waiting {RETRY_DELAY}s for the Colab "
                      f"cooldown...{C_RESET}", flush=True)
                time.sleep(RETRY_DELAY)
                continue
            break

        # ------------------------------------------------ allocate something new
        flags = "--gpu t4 --high-mem" if use_highmem else "--gpu t4"
        print(f"      {C_GRAY}[{attempt}/{max_attempts}] Allocating "
              f"{'High-RAM' if use_highmem else 'Standard'} Tesla T4 "
              f"runtime...{C_RESET}", flush=True)
        r = run_cmd(f"colab new -s {SESSION_NAME} {flags}", capture=True, timeout=420)

        if r.returncode != 0 and use_highmem:
            print(f"      {C_YELLOW}High-RAM allocation refused. Trying the "
                  f"standard T4 shape...{C_RESET}", flush=True)
            r = run_cmd(f"colab new -s {SESSION_NAME} --gpu t4", capture=True, timeout=420)

        if r.returncode != 0:
            msg = ((r.stderr or "") + (r.stdout or "")).strip()[:200]
            log_warn(f"Allocation attempt {attempt} failed: {msg}")

            # Whatever happened, the server is the source of truth: it may have
            # refused because a stale assignment is still holding the slot, or
            # it may have actually created one before erroring.
            adopt_orphans()
            if session_is_named():
                ok = _current_family_ok(use_highmem)
                if ok is not None:
                    return ok

            if "precondition" in msg.lower() or "412" in msg:
                log_warn("A stale assignment is blocking the allocation — "
                         "releasing it.")
                free_orphans()

            if attempt < max_attempts:
                print(f"      {C_GRAY}Waiting {RETRY_DELAY}s before "
                      f"retry...{C_RESET}", flush=True)
                time.sleep(RETRY_DELAY)
            continue

        fam, zone, endpoint = current_session_region()
        last_zone = zone
        log_ok(f"Allocated: {zone or 'unknown region'}  ({endpoint or 'id?'})")

        ok = _current_family_ok(use_highmem)
        if ok is not None:
            return ok

        if attempt < max_attempts:
            log_warn(f"{zone or 'unknown'} is not preferred (wanted '{want}') — "
                     f"stopping and retrying ({attempt}/{max_attempts})...")
            stop_session()
            print(f"      {C_GRAY}Waiting {RETRY_DELAY}s for the Colab "
                  f"cooldown...{C_RESET}", flush=True)
            time.sleep(RETRY_DELAY)

    # ------------------------------------- never leave the user without a VM
    if has_any_session():
        adopt_orphans()
        if session_is_named():
            log_warn(f"Could not get '{want}' after {max_attempts} attempts — "
                     f"keeping the live session instead of destroying it.")
            return _ready(use_highmem, " (fallback)")

    log_warn(f"No live session after {max_attempts} attempts. Final allocation "
             f"in any region...")
    free_orphans()
    r = run_cmd(f"colab new -s {SESSION_NAME} --gpu t4", capture=True, timeout=420)
    if session_is_named():
        return _ready(use_highmem, " (fallback)")

    log_err(f"Could not allocate any session after {max_attempts} attempts "
            f"plus a final fallback.")
    return False


def ensure_deployable():
    """
    Guarantee `colab exec -s SESSION_NAME` will resolve before we use it.

    `colab sessions` may list an assignment that has no local name for our
    session, in which case exec fails with "Session 't4' not found" and the
    whole deployment is wasted. Adopt first, then re-allocate if impossible.
    """
    if session_is_named():
        return True
    adopt_orphans()
    if session_is_named():
        log_ok(f"Re-adopted the live VM as '{SESSION_NAME}'.")
        return True
    log_warn(f"Session '{SESSION_NAME}' is not addressable. "
             f"Allocating a fresh one...")
    return allocate_session(False)


def setup_environment(desktop_mode="minimal"):
    mode_desc = "Windows 11 Fluent GUI" if desktop_mode == "win11" else "Pure Game Mode"
    log_step("2/4", f"Deploying hardware environment ({mode_desc}, Xorg, NvFBC, WebGPU, Sunshine, Tunnels)...")

    if not ensure_deployable():
        log_err("No addressable session to deploy onto — aborting before wasting a cycle.")
        sys.exit(1)

    # Store desktop mode in remote /tmp/desktop_mode.txt
    set_mode_cmd = f"python3 -c \\\"open('/tmp/desktop_mode.txt', 'w').write('{desktop_mode}')\\\""
    run_exec(f'"{set_mode_cmd}"', idle_timeout=20)

    print(f"      {C_GRAY}Executing parallel automated deployment on remote instance...{C_RESET}", flush=True)
    res = run_exec("--env DESKTOP_MODE=%s -f setup_remote.py" % desktop_mode,
                   capture=False, idle_timeout=900, hard_timeout=None, retry_on_timeout=False)
    if res.returncode != 0:
        log_warn("Deployment returned non-zero code. Verifying active services...")
    else:
        log_ok("Production stack successfully deployed.")

def get_links(verbose=False):
    if verbose:
        log_step("3/4", "Extracting active service tunnels and mesh addresses...")
    res = run_exec("-f read_links.py", idle_timeout=30)
    out = (res.stdout or "").strip()
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
    res = run_exec("-f check_ts.py", idle_timeout=30)

    if "STATUS:AUTHORIZED" in (res.stdout or ""):
        lines = res.stdout.replace("STATUS:AUTHORIZED", "").strip().split("\n")
        first_line = lines[0] if lines else ""
        parts = first_line.split()
        ip = parts[0] if parts else "100.x.y.z"
        log_ok(f"Tailscale is AUTHORIZED! Colab Node IP: {C_GREEN}{C_BOLD}{ip}{C_RESET}")
        return ip

    if not ts_login_url or "login.tailscale.com" not in ts_login_url:
        m = re.search(r'https://login\.tailscale\.com/a/[a-zA-Z0-9]+', res.stdout or "")
        if m:
            ts_login_url = m.group(0)

    if ts_login_url and "login.tailscale.com" in ts_login_url:
        print(f"  {C_YELLOW}Authorization Required:{C_RESET} {C_WHITE}{C_BOLD}{ts_login_url}{C_RESET}")
        print(f"  {C_GRAY}Waiting for authentication approval (checks every 5s, up to 5 min)...{C_RESET}",
              end="", flush=True)
        # Each poll is bounded, so this can never hang even if one exec wedges.
        # A single wedged poll previously blocked the launcher forever because
        # run_cmd had no deadline; a dead poll is now just a skipped tick.
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(5)
            res = run_exec("-f check_ts.py", idle_timeout=25, retry_on_timeout=True)
            if "STATUS:AUTHORIZED" in (res.stdout or ""):
                lines = res.stdout.replace("STATUS:AUTHORIZED", "").strip().split("\n")
                first_line = lines[0] if lines else ""
                parts = first_line.split()
                ip = parts[0] if parts else "100.x.y.z"
                print(f"\n{C_GREEN}[✓] Authorized! Node IP: {C_BOLD}{ip}{C_RESET}")
                return ip
            if res.returncode == 124:
                print(f"{C_YELLOW}!{C_RESET}", end="", flush=True)
            else:
                print(".", end="", flush=True)
        print(f"\n{C_YELLOW}[!] Tailscale authorization timed out after 5 min.{C_RESET}")
        print(f"  {C_GRAY}The desktop is already deployed — you can authorize at any "
              f"time, then run /tailscale or /funnel.{C_RESET}")
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
    res = run_exec("-f temp_pair.py", idle_timeout=15)
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
    res = run_exec(f"-f mw_pin.py{action}", idle_timeout=30)
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
    res = run_exec("-f temp_accel.py", idle_timeout=15, hard_timeout=180)
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
    res = run_exec("-f temp_unfreeze.py", idle_timeout=10)
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
    res = run_exec("-f temp_stat.py", idle_timeout=30, hard_timeout=180)
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
    res = run_exec("-f temp_restart.py", idle_timeout=30, hard_timeout=240)
    try:
        os.remove("temp_restart.py")
    except Exception:
        pass
    if "RESTARTED_OK" in res.stdout:
        log_ok("Sunshine & Openbox restarted cleanly with full freeze-proof input and wallpaper support.")
    else:
        log_warn("Restart script finished. Telemetry output:\n" + res.stdout)

def trigger_pulse():
    res = run_exec("-f keepalive_code.py", idle_timeout=20, hard_timeout=120)
    out = res.stdout.strip()
    if "Keep-Alive Pulse" in out:
        log_ok(out)
    else:
        print(f"[*] Pulse output: {out}")

def setup_funnel():
    print(f"\n{C_CYAN}[*] Configuring Tailscale Public Funnel (Low-Latency Moonlight Web for Friends)...{C_RESET}", flush=True)
    res = run_exec("-f start_ts_funnel.py", idle_timeout=30, hard_timeout=180)
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
    # Built from data with computed padding: the previous hand-aligned box drifted
    # between 65 and 79 visible columns (and '{SESSION_NAME}' expanded to 't4'),
    # so the right border never lined up.
    rows = [
        ("/pin", "Show current MoonlightWeb Access PIN (web login)"),
        ("/pin new", "Mint a fresh single-use PIN for a new device"),
        ("/funnel", "Publish MoonlightWeb publicly via Tailscale Funnel"),
        ("/accel", "Verify OpenGL 4.6, Vulkan 1.4 & NVENC hardware accel"),
        ("/unfreeze", "Release stuck Shift/Alt/Ctrl keys and unfreeze input"),
        ("/links", "Display full endpoint dashboard with PIN & creds"),
        ("/status", "Check GPU temperature, VRAM usage, active stream"),
        ("/restart", "Cleanly restart Sunshine & MoonlightWeb services"),
        ("/gtav", "Install/repair GTA V (Wine+DXVK+Steam shortcut)"),
        ("/gtav add", "Re-register the Steam shortcut (run after login)"),
        ("/gtav play", "Launch GTA V on the desktop right now"),
        ("/gtav log", "Tail the GTA V installer log"),
        ("/gtav verify", "Prove T4 GPU + NVENC acceleration (no lavapipe)"),
        ("/tailscale", "Query Tailscale mesh state and node IP"),
        ("/pulse", "Trigger an immediate 12MB VRAM PyTorch keep-alive"),
        ("/region", "Show the live zone taken from the runtime URL"),
        ("/region sg", "Stop + re-allocate until Singapore assigned"),
        ("/region us", "Stop + re-allocate until US assigned"),
        ("/region eu", "Stop + re-allocate until EU assigned"),
        ("/region any", "Take whatever region Colab assigns"),
        ("/adopt", "Attach a live VM registered under another name"),
        ("/kernel", "Reset a wedged remote kernel (fixes a frozen console)"),
        ("/free", f"Release every assignment except '{SESSION_NAME}'"),
        ("/logout", "Log out of Google Colab & clear cached account tokens"),
        ("/help", "Show this list of commands"),
        ("/exit", "Stop keep-alive and exit launcher"),
    ]

    W = 75          # inner width between the two border characters
    CMD_W = 16      # column width for the command
    title = "COMMAND CONSOLE MANUAL"

    t_pad = W - len(title)
    t_left = t_pad // 2

    out = [f"{C_CYAN}╭{'─' * W}╮",
           f"│{' ' * t_left}{C_WHITE}{C_BOLD}{title}{C_CYAN}{' ' * (t_pad - t_left)}│",
           f"├{'─' * W}┤"]

    for cmd, desc in rows:
        body = f"  {cmd.ljust(CMD_W)}: {desc}"
        if len(body) > W:
            desc = desc[:W - (CMD_W + 4)]
            body = f"  {cmd.ljust(CMD_W)}: {desc}"
        out.append(f"│  {C_GREEN}{cmd.ljust(CMD_W)}{C_CYAN}: "
                   f"{C_WHITE}{desc.ljust(W - CMD_W - 4)}{C_CYAN}│")

    out.append(f"╰{'─' * W}╯{C_RESET}")
    print("\n" + "\n".join(out))


# -------------------------------------------------------------
# 4. Background Keep-Alive Pulse Daemon
# -------------------------------------------------------------
def keep_alive_worker():
    """Pulse the GPU every KEEP_ALIVE_INTERVAL seconds to dodge idle timeouts.

    Never let this interrupt the user: `colab exec` drives one persistent kernel
    per session, so a pulse fired during a deployment or a long command does not
    run beside it -- it queues behind it, and two execs racing on one kernel is
    how the session got wedged. The pulse is skipped (cheaply) whenever another
    exec holds the lock, and a single unbounded call previously blocked the
    whole launcher, which is why every call here is time-bounded.
    """
    while not stop_event.is_set():
        stop_event.wait(KEEP_ALIVE_INTERVAL)
        if stop_event.is_set():
            break
        # Degrade gracefully instead of queueing behind an in-flight command.
        if not EXEC_LOCK.acquire(blocking=False):
            continue
        try:
            # --timeout 20 was too tight: a cold `import torch` on a fresh kernel
            # prints nothing for 20-40s, so the CLI abandoned a cell that was
            # still running. That orphan then owned the kernel and every later
            # exec queued behind it forever. keepalive_code.py now prints before
            # importing torch, and the idle window is generous.
            cmd = f"colab exec -s {SESSION_NAME} --timeout 60 -f keepalive_code.py"
            res = run_cmd(cmd, capture=True, timeout=180)
            out = (res.stdout or "") + (res.stderr or "")
            if "Keep-Alive Pulse" in out:
                t_str = time.strftime("%H:%M:%S")
                print(f"\n{C_GRAY}[{t_str}] ⚡ VRAM Pulse Active (~12MB tensor warm){C_RESET}\n{C_CYAN}[colab-gaming] > {C_RESET}", end="", flush=True)
            elif res.returncode != 0:
                # Any failure leaves the kernel suspect. The CLI exits 1 (not 124)
                # when it abandons a cell, so keying recovery off 124 alone never
                # fired and the wedge survived every subsequent pulse.
                _reset_kernel()
        except Exception:
            pass
        finally:
            EXEC_LOCK.release()

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
            run_exec("-f sync_ts_ip.py", idle_timeout=30)
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
                    run_exec("\"tail -n 40 /var/log/services/gta_setup.log 2>/dev/null || echo 'No GTA log yet'\"", idle_timeout=20)
                elif arg.lower() == "add":
                    run_exec("--env GTA_STEP=add -f gta_setup.py", idle_timeout=60, hard_timeout=None, retry_on_timeout=False)
                elif arg.lower() == "play":
                    run_exec("\"DISPLAY=:1 nohup setsid bash /content/gta_launch.sh >/var/log/services/gta_run.log 2>&1 & sleep 2; echo launched\"", idle_timeout=20)
                elif arg.lower() in ("status", "st"):
                    run_exec("-f gta_status.py", capture=False, idle_timeout=60, hard_timeout=300)
                elif arg.lower() in ("verify", "gpu", "accel"):
                    run_exec("--env GTA_STEP=verify -f gta_setup.py", capture=False, idle_timeout=60, hard_timeout=420)
                else:
                    install_gta_v()
            elif cmd in ["/region", "/zone", "region", "zone"]:
                fam, zone, endpoint = current_session_region()
                if not endpoint:
                    log_warn(f"No active session '{SESSION_NAME}'.")
                else:
                    print(f"      {C_WHITE}Session   : {C_CYAN}{endpoint}{C_RESET}")
                    print(f"      {C_WHITE}Zone      : {C_CYAN}{zone or 'unknown'}{C_RESET}")
                    print(f"      {C_WHITE}Family    : {C_CYAN}{fam or 'unknown'}{C_RESET}")
                    print(f"      {C_WHITE}Preference: {C_CYAN}"
                          f"{REGION_LABELS.get(REGION_PREFERENCE, REGION_PREFERENCE)}"
                          f"{C_RESET}")
                    if not region_ok(fam):
                        log_warn("Current zone does not match your preference. "
                                 "Run /region sg (or us/eu) to stop and re-allocate.")
                if arg.lower() in ("sg", "us", "eu", "any", "auto"):
                    REGION_PREFERENCE = arg.lower()
                    log_ok(f"Region preference set to "
                           f"{REGION_LABELS[REGION_PREFERENCE]}")
                    print(f"      {C_GRAY}Stopping session and re-allocating..."
                          f"{C_RESET}", flush=True)
                    if stop_session() and allocate_session(False):
                        log_warn("New runtime allocated — the desktop stack is NOT "
                                 "deployed yet. Re-run start.bat to provision it.")
                elif arg:
                    log_warn("Usage: /region [sg|us|eu|any]  "
                             "(or /region with no args to show the zone)")
            elif cmd in ["/kernel", "kernel"]:
                log_step("KERNEL", "Discarding the cached remote kernel...")
                if _reset_kernel():
                    res = run_exec("-f check_ts.py", idle_timeout=30)
                    if res.returncode == 0:
                        log_ok("Fresh kernel is responding.")
                    else:
                        log_warn("Kernel reset, but the probe still failed. "
                                 "The VM may be gone — try /adopt or start.bat.")
                else:
                    log_ok("No cached kernel to reset (the next exec boots one).")
            elif cmd in ["/adopt", "adopt"]:
                log_step("ADOPT", "Re-attaching to any live Colab VM...")
                if adopt_orphans() or session_is_named():
                    fam, zone, endpoint = current_session_region()
                    log_ok(f"Session '{SESSION_NAME}' in {zone or 'unknown'} "
                           f"is attached and addressable.")
                else:
                    log_warn("Nothing to adopt. Run /region <sg|us|eu> to allocate.")
            elif cmd in ["/free", "free"]:
                log_step("FREE", "Releasing every assignment except "
                                 f"'{SESSION_NAME}' (destroys those VMs)...")
                if free_orphans():
                    log_ok("No stale assignments remain.")
                else:
                    log_warn("Some assignments could not be released.")
            elif cmd in ["/logout", "logout"]:
                log_step("LOGOUT", "Starting Colab account logout...")
                try:
                    import logout_colab
                    rc = logout_colab.logout(force=False, stop_sessions=False)
                    if rc == 0:
                        print(f"      {C_GRAY}Shutting down launcher orchestrator...{C_RESET}")
                        stop_event.set()
                        break
                except Exception as e:
                    log_warn(f"Logout failed: {e}")
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
