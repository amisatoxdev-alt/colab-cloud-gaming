import json
import os
import re
import subprocess

links = {
    "moonlight_url": "Pending",
    "sunshine_url": "Pending",
    "desktop_url": "Pending",
    "gamepad_url": "Pending",
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

# 2. Directly extract latest URLs from tunnel logs if pending or missing
def extract_cf_url(log_path):
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r') as f:
                content = f.read()
            matches = re.findall(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', content)
            if matches:
                return matches[-1]
        except Exception:
            pass
    return None

cf_map = {
    "sunshine_url": "/var/log/tunnels/sunshine_cf.log",
    "moonlight_url": "/var/log/tunnels/moonlight_cf.log",
    "desktop_url": "/var/log/tunnels/desktop_cf.log",
    "gamepad_url": "/var/log/tunnels/gamepad_cf.log"
}

for key, log_file in cf_map.items():
    if links.get(key) in (None, "", "Pending"):
        url = extract_cf_url(log_file)
        if url:
            links[key] = url

# 3. Check live Tailscale IP
try:
    ts_proc = subprocess.run("tailscale ip -4 2>/dev/null", shell=True, text=True, capture_output=True)
    ts_ip = ts_proc.stdout.strip()
    if ts_ip and not any(err in ts_ip for err in ["failed", "error", "Tailscale", "127.0.0.1"]):
        links["tailscale_ip"] = ts_ip
        links["sunshine_direct_tailscale"] = f"https://{ts_ip}:47990"
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
