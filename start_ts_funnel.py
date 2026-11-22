import subprocess, time, json, re, os

report = {}


def sh(cmd, t=20):
    try:
        p = subprocess.run(cmd, shell=True, text=True, capture_output=True, timeout=t)
        return (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return "[TIMEOUT]"


def ts_json(t=8):
    try:
        return json.loads(sh("tailscale status --json", t) or "{}")
    except Exception:
        return {}


# 1. Query Tailscale node info
data = ts_json()
self_node = data.get("Self", {}) or {}
dns_name = self_node.get("DNSName", "").rstrip(".")
ips = self_node.get("TailscaleIPs") or []
report["dns_name"] = dns_name
report["tailscale_ip"] = ips[0] if ips else ""
report["backend_state"] = data.get("BackendState", "")

if data.get("BackendState") != "Running":
    report["status"] = "AUTH_REQUIRED"
    report["auth_url"] = data.get("AuthURL") or "https://login.tailscale.com/admin/machines"
    report["msg"] = "Node is not authorized. Run `tailscale up` and open the login URL."
    print(json.dumps(report), flush=True)
    raise SystemExit(0)

# 2. Check current funnel status
f_stat = sh("tailscale funnel status 2>&1", 15)
if re.search(r"https://[A-Za-z0-9.\-]+\.ts\.net", f_stat) and "8090" in f_stat:
    report["status"] = "ACTIVE"
    report["url"] = f"https://{dns_name}/"
    print(json.dumps(report), flush=True)
    raise SystemExit(0)

# 3. Attempt to start funnel -- time-boxed. `tailscale funnel` blocks until the
# control plane acks the serve config; unbounded it is exactly what left the
# launcher sitting on "publishing Funnel" with no output.
try:
    subprocess.run("tailscale funnel --bg --yes 8090 > /tmp/funnel.log 2>&1",
                   shell=True, timeout=25)
except subprocess.TimeoutExpired:
    report["note"] = "funnel command exceeded 25s"
time.sleep(2)

log_content = ""
if os.path.exists("/tmp/funnel.log"):
    log_content = open("/tmp/funnel.log", errors="replace").read()

if "login.tailscale.com" in log_content:
    m = re.search(r'https://login\.tailscale\.com[^\s]+', log_content)
    report["status"] = "AUTH_REQUIRED"
    report["auth_url"] = m.group(0) if m else "https://login.tailscale.com/admin/settings/features"
    report["msg"] = "Funnel must be enabled for this node in Tailscale admin."
else:
    f_stat2 = sh("tailscale funnel status 2>&1", 15)
    m2 = re.search(r"https://[A-Za-z0-9.\-]+\.ts\.net", f_stat2)
    if m2:
        report["status"] = "ACTIVE"
        report["url"] = f"https://{dns_name}/"
    else:
        report["status"] = "UNKNOWN"
        report["log"] = log_content.strip()[:400]

print(json.dumps(report), flush=True)
