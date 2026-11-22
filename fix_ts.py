"""Self-healing Tailscale repair for the Colab VM.

Run this any time Tailscale looks "stuck on login". It is idempotent and every
step is time-boxed, so it can never hang the launcher.

What it fixes (root causes of the endless "waiting for node authorization"):
  1. DUPLICATE SUPERVISORS -- setup_remote.py starts tailscaled inside a
     `bash -c "while true; ..."` loop, but the pre-flight `killall tailscaled`
     only matches the binary, never the loop. Every re-deploy therefore added
     another loop; two loops fight over /run/tailscale/tailscaled.sock and the
     loser respawns every second forever. `tailscale status` then answers
     inconsistently, the probe thinks the node is logged out, and the launcher
     waits on a login URL that means nothing.
  2. `--reset` ON AN AUTHORIZED NODE -- resetting discards the stored node
     identity, so an already-working node was forced back to the login screen
     on every deploy/probe.
  3. UNBOUNDED `tailscale funnel` -- blocks until the control plane acks. On a
     node that is not authorized it never returns.

Never uses --reset, so an authorized node keeps its identity and IP.
"""
import json
import os
import re
import subprocess
import time

SOCK = "/run/tailscale/tailscaled.sock"
STATE = "/var/lib/tailscale"
LOG = "/var/log/services/tailscale.log"


def sh(cmd, t=20):
    try:
        p = subprocess.run(cmd, shell=True, text=True, capture_output=True, timeout=t)
        return (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return "[TIMEOUT]"
    except Exception as exc:  # pragma: no cover
        return f"[ERR {exc}]"


def ts_json(t=8):
    try:
        return json.loads(sh("tailscale status --json", t) or "{}")
    except Exception:
        return {}


def supervisor_count():
    out = subprocess.run("pgrep -fc TS_DEBUG_ALWAYS_USE_DERP || true", shell=True,
                         text=True, capture_output=True).stdout.strip()
    return int(out or 0) if out.isdigit() else 0


def step(n, msg):
    print(f"[{n}] {msg}", flush=True)


# ---------------------------------------------------------------- 1. one daemon
step(1, "Reaping duplicate tailscaled supervisors...")
before = supervisor_count()
subprocess.run("pkill -9 -f TS_DEBUG_ALWAYS_USE_DERP 2>/dev/null || true", shell=True)
subprocess.run("pkill -9 -f 'tailscaled --tun=userspace-networking' 2>/dev/null || true", shell=True)
for _ in range(80):
    if subprocess.run("pgrep -f 'tailscaled --tun=userspace-networking' >/dev/null 2>&1",
                      shell=True).returncode != 0:
        break
    time.sleep(0.25)
subprocess.run(f"rm -f {SOCK}", shell=True)
print(f"    supervisors: {before} -> {supervisor_count()}", flush=True)

step(2, "Starting exactly one supervisor...")
for d in (STATE, "/var/run/tailscale", os.path.dirname(LOG)):
    os.makedirs(d, exist_ok=True)
logf = open(LOG, "a")
subprocess.Popen(
    ["bash", "-c",
     f"while true; do TS_DEBUG_ALWAYS_USE_DERP=true tailscaled --tun=userspace-networking "
     f"--socket={SOCK} --port 41641; sleep 1; done"],
    stdin=subprocess.DEVNULL, stdout=logf, stderr=logf, start_new_session=True,
)

# ------------------------------------------------------------------ 2. wait up
step(3, "Waiting for the backend...")
state = ""
for i in range(60):
    state = ts_json(4).get("BackendState", "")
    if state:
        print(f"    BackendState={state} after {i * 0.5:.1f}s", flush=True)
        break
    time.sleep(0.5)
if not state:
    print("    daemon never answered", flush=True)

# --------------------------------------------------- 3. authorize WITHOUT reset
step(4, "Checking authorization (never using --reset)...")
data = ts_json(8)
state = data.get("BackendState", "")
login_url = ""

if state != "Running":
    login_url = data.get("AuthURL") or ""
    if not login_url:
        blob = sh("tailscale up --operator=root --accept-dns=false --accept-routes=false "
                  "--timeout=20s 2>&1", 45)
        m = (re.search(r"https://login\.tailscale\.com/a/[A-Za-z0-9]+", blob)
             or re.search(r"https://login\.tailscale\.com/[^\s\"']+", blob))
        login_url = m.group(0) if m else ""
    data = ts_json(6) or data
    state = data.get("BackendState", state)
    if state == "Running":
        print("    authorized after `tailscale up`", flush=True)
    elif login_url:
        print(f"    LOGIN REQUIRED -> {login_url}", flush=True)
    else:
        print("    still not authorized, and no login URL was offered", flush=True)
else:
    sh("tailscale set --operator=root --accept-dns=false 2>&1", 20)
    print("    already authorized", flush=True)

self_node = data.get("Self", {}) or {}
ips = self_node.get("TailscaleIPs") or []
ts_ip = ips[0] if ips else ""
dns_name = self_node.get("DNSName", "").rstrip(".")
print(f"    ip={ts_ip or '-'} dns={dns_name or '-'}", flush=True)

# ---------------------------------------------------------- 4. funnel (bounded)
step(5, "Publishing Funnel (time-boxed)...")
funnel_url = ""
if ts_json(6).get("BackendState") == "Running":
    os.makedirs("/var/log/tunnels", exist_ok=True)
    try:
        subprocess.run("tailscale funnel --bg --yes 8090 > /var/log/tunnels/funnel.log 2>&1",
                       shell=True, timeout=30)
    except subprocess.TimeoutExpired:
        print("    funnel command exceeded 30s (continuing)", flush=True)
    for _ in range(12):
        f_stat = sh("tailscale funnel status 2>&1", 12)
        m = re.search(r"https://[A-Za-z0-9.\-]+\.ts\.net", f_stat)
        if m:
            funnel_url = m.group(0).rstrip("/")
            break
        time.sleep(1)
    if funnel_url:
        print(f"    funnel URL: {funnel_url}", flush=True)
    else:
        print("    funnel not published yet (retry with /funnel)", flush=True)
else:
    print("    skipped: node not authorized", flush=True)

# ------------------------------------------------------------- 5. links.json
step(6, "Refreshing links.json...")
pin = ""
out = sh("/opt/moonlightweb/bin/MoonlightWeb --status 2>&1", 25)
pm = re.search(r"Access PIN\s+(\d{4,8})", out)
if pm:
    pin = pm.group(1)
else:
    out = sh("/opt/moonlightweb/bin/MoonlightWeb --new-pin 2>&1", 25)
    pm = re.search(r"Access PIN\s+(\d{4,8})", out)
    if pm:
        pin = pm.group(1)

links = {
    "funnel_url": f"{funnel_url}/" if funnel_url else "Run /funnel to publish",
    "moonlight_pin": pin,
    "pin_command": "/opt/moonlightweb/bin/MoonlightWeb --new-pin",
    "sunshine_direct_tailscale": f"https://{ts_ip}:47990" if ts_ip else "Authorize Tailscale",
    "sunshine_creds": "admin / Sunshine123!",
    "tailscale_ip": ts_ip,
    "tailscale_login_url": login_url,
}
for path in ("/tmp/links.json", "/var/log/services/links.json"):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(links, f, indent=2)
    except Exception:
        pass

# Sunshine needs the live node IP in external_ip / csrf origins to accept the
# MoonlightWeb handoff; keep it in sync whenever the IP changes.
try:
    conf_paths = ["/root/.config/sunshine/sunshine.conf",
                  "/home/user/.config/sunshine/sunshine.conf"]
    for cp in conf_paths:
        if not os.path.exists(cp) or not ts_ip:
            continue
        txt = open(cp).read()
        txt = re.sub(r"external_ip\s*=.*", f"external_ip = {ts_ip}", txt)
        m = re.search(r"csrf_allowed_origins\s*=\s*(.*)", txt)
        if m:
            origins = [o.strip() for o in m.group(1).split(",") if o.strip()]
            for extra in ([f"https://{ts_ip}:47990"] +
                          ([funnel_url, f"{funnel_url}:443"] if funnel_url else [])):
                if extra and extra not in origins:
                    origins.append(extra)
            txt = re.sub(r"csrf_allowed_origins\s*=.*",
                         "csrf_allowed_origins = " + ",".join(origins), txt)
        with open(cp, "w") as f:
            f.write(txt)
except Exception as exc:
    print(f"    sunshine sync skipped: {exc}", flush=True)

print(json.dumps(links, indent=2), flush=True)

# ---------------------------------------------------------------- 6. final probe
step(7, "Verifying endpoints...")
code_local = sh("curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8090/", 20).strip()
print(f"    local 8090 -> {code_local}", flush=True)
if funnel_url:
    code_pub = sh("curl -s -o /dev/null -w '%{http_code}' --max-time 30 "
                  + funnel_url + "/", 45).strip()
    print(f"    funnel     -> {code_pub}", flush=True)

print("TAILSCALE_OK" if ts_json(6).get("BackendState") == "Running" else "TAILSCALE_NEEDS_LOGIN",
      flush=True)
