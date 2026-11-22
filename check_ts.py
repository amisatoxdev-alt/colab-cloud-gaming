import json
import os
import re
import subprocess


def sh(cmd, t=12):
    try:
        p = subprocess.run(cmd, shell=True, text=True, capture_output=True, timeout=t)
        return (p.stdout or "") + (p.stderr or "")
    except Exception:
        return ""


def ts_json(t=8):
    try:
        return json.loads(sh("tailscale status --json", t) or "{}")
    except Exception:
        return {}


def emit(status, ip="", url=""):
    print(json.dumps({"status": status, "ip": ip, "login_url": url}), flush=True)
    # Legacy line format kept for any older caller that greps it.
    if status == "AUTHORIZED":
        print("STATUS:AUTHORIZED", flush=True)
        print(ip, flush=True)
    else:
        print(f"STATUS:PENDING_AUTH {url}", flush=True)


data = ts_json()
state = data.get("BackendState", "")
self_node = data.get("Self", {}) or {}
ips = self_node.get("TailscaleIPs") or []
ip = ips[0] if ips else ""

# BackendState is the only reliable signal. Previous versions string-matched
# `tailscale status` output for the word "error" -- but that output lists every
# offline peer, and stale peers made the probe report "not authorized" on a
# perfectly healthy node, which then triggered a re-login and the login hang.
if state == "Running" and ip:
    try:
        if os.path.exists("/tmp/links.json"):
            with open("/tmp/links.json") as f:
                links = json.load(f)
            links["tailscale_ip"] = ip
            links["sunshine_direct_tailscale"] = f"https://{ip}:47990"
            links["tailscale_login_url"] = ""
            with open("/tmp/links.json", "w") as f:
                json.dump(links, f, indent=2)
            os.makedirs("/var/log/services", exist_ok=True)
            with open("/var/log/services/links.json", "w") as f:
                json.dump(links, f, indent=2)
    except Exception:
        pass
    emit("AUTHORIZED", ip)
else:
    # Only start a login flow when the node genuinely needs one, and NEVER with
    # --reset: --reset discards the stored node identity/settings, so an already
    # authenticated node is forced back to the login screen on every probe.
    url = data.get("AuthURL") or ""
    if not url:
        blob = sh("tailscale up --operator=root --accept-dns=false --timeout=8s 2>&1", 20)
        m = (re.search(r"https://login\.tailscale\.com/a/[a-zA-Z0-9]+", blob)
             or re.search(r"https://login\.tailscale\.com/[^\s\"']+", blob))
        if m:
            url = m.group(0)
        data = ts_json(4) or data
        state = data.get("BackendState", state)
        if state == "Running":
            self_node = data.get("Self", {}) or {}
            ips = self_node.get("TailscaleIPs") or []
            ip = ips[0] if ips else ""
            if ip:
                emit("AUTHORIZED", ip)
                raise SystemExit(0)
    emit("PENDING_AUTH", "", url or "https://login.tailscale.com/admin/machines")
