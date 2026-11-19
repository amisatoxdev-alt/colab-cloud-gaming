import subprocess
import json
import re
import os

res = subprocess.run("tailscale status", shell=True, text=True, capture_output=True)
out = res.stdout + res.stderr
if "Logged out" in out or "error" in out.lower() or res.returncode != 0:
    m = re.search(r'https://login\.tailscale\.com/a/[a-zA-Z0-9]+', out)
    login_url = m.group(0) if m else ""
    if not login_url:
        up_res = subprocess.run("tailscale up --operator=root --reset --accept-dns=false --timeout=5s 2>&1", shell=True, text=True, capture_output=True)
        m2 = re.search(r'https://login\.tailscale\.com/a/[a-zA-Z0-9]+', up_res.stdout + up_res.stderr)
        if m2:
            login_url = m2.group(0)
    print(f"STATUS:PENDING_AUTH {login_url}")
else:
    print("STATUS:AUTHORIZED\n" + out.strip())
    # Save IP to links
    ip_res = subprocess.run("tailscale ip -4", shell=True, text=True, capture_output=True)
    ip = ip_res.stdout.strip()
    if ip and os.path.exists('/tmp/links.json'):
        try:
            data = json.load(open('/tmp/links.json'))
            data['tailscale_ip'] = ip
            data['sunshine_direct_tailscale'] = f"https://{ip}:47990"
            json.dump(data, open('/tmp/links.json', 'w'), indent=2)
        except Exception:
            pass


