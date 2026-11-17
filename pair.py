import sys
import subprocess
import json

if len(sys.argv) < 2:
    pin = input("Enter 4-digit Moonlight PIN: ").strip()
else:
    pin = sys.argv[1].strip()

print(f"[*] Pairing PIN: {pin} with Sunshine...")

# Script executed remotely in Colab session
remote_code = f"""
import subprocess
import json

creds = ["admin:Sunshine123!", "admin:admin"]
success = False
for cred in creds:
    cmd = f"curl -s -k -u {{cred}} -X POST https://127.0.0.1:47990/api/pin -H 'Content-Type: application/json' -d '{json.dumps({'pin': pin, 'name': 'moonlight'})}'"
    r = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    if '"status":true' in r.stdout or 'success' in r.stdout.lower() or 'named_status' in r.stdout:
        print(f"SUCCESS: {{r.stdout}}")
        success = True
        break
    else:
        print(f"Attempt with {{cred}}: {{r.stdout}}")

if not success:
    print("FAILED_TO_PAIR")
"""

with open("auto_pair.py", "w") as f:
    f.write(remote_code)

res = subprocess.run("colab exec -s t4 -f auto_pair.py", shell=True, text=True, capture_output=True)
out = res.stdout.strip()
if '"status":true' in out:
    print(f"\n[OK] Device successfully PAIRED with PIN {pin}!")
else:
    print(f"\nResponse: {out}")
