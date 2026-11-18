import subprocess, time, json, re, os

report = {}

# 1. Query Tailscale node info
try:
    s_raw = subprocess.check_output("tailscale status --json", shell=True).decode()
    data = json.loads(s_raw)
    self_node = data.get("Self", {})
    dns_name = self_node.get("DNSName", "").rstrip(".")
    report["dns_name"] = dns_name
    report["tailscale_ip"] = self_node.get("TailscaleIPs", [""])[0]
except Exception as e:
    report["error"] = f"Failed to get tailscale status: {e}"
    print(json.dumps(report))
    exit(0)

# 2. Check current funnel status
f_stat = subprocess.run("tailscale funnel status 2>&1", shell=True, capture_output=True, text=True).stdout
if "https://" in f_stat and "8090" in f_stat:
    report["status"] = "ACTIVE"
    report["url"] = f"https://{dns_name}/"
    print(json.dumps(report))
    exit(0)

# 3. Attempt to start funnel
subprocess.run("nohup tailscale funnel --bg --yes 8090 > /tmp/funnel.log 2>&1 &", shell=True)
time.sleep(2)

log_content = open("/tmp/funnel.log").read() if os.path.exists("/tmp/funnel.log") else ""

if "https://login.tailscale.com" in log_content:
    m = re.search(r'https://login\.tailscale\.com[^\s]+', log_content)
    report["status"] = "AUTH_REQUIRED"
    report["auth_url"] = m.group(0) if m else "https://login.tailscale.com/admin/settings/features"
    report["dns_name"] = dns_name
    report["msg"] = "Funnel must be enabled for this node in Tailscale admin."
elif "No serve config" in log_content or "error" in log_content.lower():
    report["status"] = "ERROR"
    report["log"] = log_content.strip()
else:
    # Check again
    f_stat2 = subprocess.run("tailscale funnel status 2>&1", shell=True, capture_output=True, text=True).stdout
    if "https://" in f_stat2 or "Available" in f_stat2 or os.path.exists("/tmp/funnel.log"):
        report["status"] = "ACTIVE"
        report["url"] = f"https://{dns_name}/"
    else:
        report["status"] = "UNKNOWN"
        report["log"] = log_content

print(json.dumps(report))

