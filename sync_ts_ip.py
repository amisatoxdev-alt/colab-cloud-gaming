import subprocess
import os
import re
import time

def run(cmd):
    return subprocess.run(cmd, shell=True, text=True, capture_output=True).stdout.strip()

ts_ip = run("tailscale ip -4")
if not ts_ip or " " in ts_ip:
    print("No valid Tailscale IP found yet.")
    exit(0)

print(f"[*] Syncing Tailscale IP '{ts_ip}' into Sunshine...")

# 1. Read existing allowed origins from sunshine.conf
conf_path = "/home/user/.config/sunshine/sunshine.conf"
current_conf = open(conf_path).read() if os.path.exists(conf_path) else ""

# Extract current origins or use defaults
origins = []
m = re.search(r'csrf_allowed_origins\s*=\s*(.*)', current_conf)
if m:
    origins = [x.strip() for x in m.group(1).split(",") if x.strip()]

new_origin = f"https://{ts_ip}:47990"
if new_origin not in origins:
    origins.append(new_origin)

# Update config with correct external_ip and origins
current_conf = re.sub(r'external_ip\s*=.*', f'external_ip = {ts_ip}', current_conf)
current_conf = re.sub(r'csrf_allowed_origins\s*=.*', f'csrf_allowed_origins = {",".join(origins)}', current_conf)
# Ensure controller is enabled
if "controller =" not in current_conf:
    current_conf += "\ncontroller = enabled\ngamepad = x360\ngamepad_driver = uinput\n"

with open("/root/.config/sunshine/sunshine.conf", "w") as f:
    f.write(current_conf)
with open("/home/user/.config/sunshine/sunshine.conf", "w") as f:
    f.write(current_conf)

# Restart Sunshine under user
subprocess.run("pkill -9 -x -u user sunshine 2>/dev/null || true", shell=True)
time.sleep(1)
sunshine_cmd = (
    'su - user -c "'
    'export DISPLAY=:1; '
    'export XDG_RUNTIME_DIR=/run/user/1001; '
    'export NO_AT_BRIDGE=1; '
    'export GTK_IM_MODULE=xim; '
    'export QT_IM_MODULE=xim; '
    'export XMODIFIERS=@im=none; '
    'export LD_PRELOAD=/usr/local/lib/libselective_uinput.so; '
    'nohup sunshine > /home/user/sunshine.log 2>&1 &"'
)
subprocess.run(sunshine_cmd, shell=True)

# Re-apply wallpaper
subprocess.run('su - user -c "DISPLAY=:1 feh --bg-fill /home/user/nvidia-wallpaper.jpg &"', shell=True)
subprocess.run('DISPLAY=:1 feh --bg-fill /home/user/nvidia-wallpaper.jpg &', shell=True)

print(f"[OK] Sunshine synchronized with Tailscale IP {ts_ip} and restarted successfully.")
