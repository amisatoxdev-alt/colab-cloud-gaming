import json
import os
import re
import subprocess

links = {
    "funnel_url": "Run /funnel to publish",
    "moonlight_pin": "",
    "pin_command": "/opt/moonlightweb/bin/MoonlightWeb --new-pin",
    "sunshine_creds": "admin / Sunshine123!",
    "tailscale_ip": "",
    "sunshine_direct_tailscale": "Authorize Tailscale",
    "tailscale_login_url": "Check 'tailscale status'"
}

# 1. Read existing links file if present
for path in ['/tmp/links.json', '/var/log/services/links.json']:
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if v and v != "Pending":
                            links[k] = v
                    break
        except Exception:
            pass

# 2. Live Tailscale IP
try:
    ts_proc = subprocess.run("tailscale ip -4 2>/dev/null", shell=True, text=True, capture_output=True)
    ts_ip = ts_proc.stdout.strip()
    if ts_ip and not any(err in ts_ip for err in ["failed", "error", "Tailscale", "127.0.0.1"]):
        links["tailscale_ip"] = ts_ip
        links["sunshine_direct_tailscale"] = f"https://{ts_ip}:47990"
except Exception:
    pass

# 3. Live Tailscale Funnel (primary public endpoint)
try:
    fn_proc = subprocess.run("tailscale funnel status 2>/dev/null", shell=True, text=True, capture_output=True)
    m = re.search(r'https://[A-Za-z0-9.\-]+\.ts\.net', fn_proc.stdout)
    if m:
        links["funnel_url"] = m.group(0).rstrip("/") + "/"
    else:
        st = subprocess.run("tailscale status --json 2>/dev/null", shell=True, text=True, capture_output=True).stdout
        dns = json.loads(st).get("Self", {}).get("DNSName", "").rstrip(".")
        if dns:
            links["funnel_url"] = f"https://{dns}/"
except Exception:
    pass

# 4. Live MoonlightWeb Access PIN (single-use, one per remote browser/device)
try:
    BIN = "/opt/moonlightweb/bin/MoonlightWeb"
    out = subprocess.run(f"{BIN} --status 2>&1", shell=True, text=True, capture_output=True).stdout
    pm = re.search(r"Access PIN\s+(\d{4,8})", out)
    if pm:
        links["moonlight_pin"] = pm.group(1)
    else:
        new_out = subprocess.run(f"{BIN} --new-pin 2>&1", shell=True, text=True, capture_output=True).stdout
        pm2 = re.search(r"Access PIN\s+(\d{4,8})", new_out)
        if pm2:
            links["moonlight_pin"] = pm2.group(1)
except Exception:
    pass

# Ensure updated links.json is written back for persistence
try:
    os.makedirs("/var/log/services", exist_ok=True)
    with open('/var/log/services/links.json', 'w') as f:
        json.dump(links, f, indent=2)
    with open('/tmp/links.json', 'w') as f:
        json.dump(links, f, indent=2)
except Exception:
    pass

# Output exactly one clean JSON string
print(json.dumps(links))
