import sys
import subprocess
import json
import os

if len(sys.argv) < 2:
    pin = input("Enter 4-digit Moonlight PIN: ").strip()
else:
    pin = sys.argv[1].strip()

print(f"[*] Pairing PIN: {pin} with Sunshine...")

remote_code = f"""
import subprocess, json

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

with open("auto_pair.py", "w") as f:
    f.write(remote_code)

res = subprocess.run("colab exec -s t4 -f auto_pair.py", shell=True, text=True, capture_output=True)
try:
    os.remove("auto_pair.py")
except Exception:
    pass

out = res.stdout.strip()
if "SUCCESS:" in out:
    client = out.split("SUCCESS:")[1].strip()
    print(f"\n[✓] Device '{client}' successfully PAIRED with PIN {pin}! You can now start streaming.")
elif "NO_PENDING_REQUEST" in out:
    print(f"\n[!] No pending pairing request detected from Moonlight!")
    print("    Make sure to open Moonlight and click on the host PC so the PIN is displayed on your screen first.")
else:
    print(f"\nResponse: {out}")
