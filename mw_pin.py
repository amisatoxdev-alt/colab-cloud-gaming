import subprocess, json, re, os, sys

BIN = "/opt/moonlightweb/bin/MoonlightWeb"
LOG = "/root/.local/share/MoonlightWeb/MoonlightWeb/logs/moonlightweb.log"

def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, text=True, capture_output=True).stdout
    except Exception:
        return ""

def parse_pin(text):
    if not text:
        return None
    m = re.search(r"Access PIN\s+(\d{4,8})", text)
    if m and "none set" not in text[max(0, m.start() - 30):m.end()]:
        return m.group(1)
    return None

def running():
    out = sh("ps -eo args | grep -c '[M]oonlightWeb --port'")
    try:
        return int(out.strip() or 0) > 0
    except Exception:
        return False

def funnel_url():
    out = sh("tailscale funnel status 2>/dev/null")
    m = re.search(r"https://[A-Za-z0-9.\-]+\.ts\.net", out)
    if m:
        return m.group(0).rstrip("/") + "/"
    try:
        st = json.loads(sh("tailscale status --json 2>/dev/null"))
        dns = st.get("Self", {}).get("DNSName", "").rstrip(".")
        if dns:
            return f"https://{dns}/"
    except Exception:
        pass
    return None

def current_pin():
    return parse_pin(sh(f"{BIN} --status 2>&1"))

def new_pin():
    out = sh(f"{BIN} --new-pin 2>&1")
    pin = parse_pin(out)
    if not pin:
        # fall back: re-read status (some builds only mutate state)
        pin = current_pin()
    return pin

def main():
    # colab exec cannot forward CLI args, so accept the trigger via env vars too
    env_flag = os.environ.get("MW_NEW", "") or os.environ.get("MW_ACTION", "")
    want_new = any(a in ("--new", "-n", "new", "gen", "fresh") for a in sys.argv[1:]) or \
        env_flag.strip().lower() in ("1", "new", "true", "yes", "gen", "fresh")

    if not running():
        os.makedirs("/var/log/services", exist_ok=True)
        subprocess.Popen(
            f"MW_SERVICE=1 nohup {BIN} --port 8090 --autostart >> /var/log/services/moonlightweb.log 2>&1 &",
            shell=True, start_new_session=True,
        )
        import time
        time.sleep(4)

    pin = new_pin() if want_new else current_pin()
    if not pin:
        pin = new_pin()

    url = funnel_url()
    report = {
        "status": "OK" if pin else "NO_PIN",
        "funnel_url": url or "",
        "pin": pin or "",
        "pin_fresh": bool(want_new),
        "server_running": running(),
        "web_ui": f"{url}" if url else "http://127.0.0.1:8090/",
        "cli_command": f"{BIN} --new-pin",
        "note": "PIN is single-use: generate a new one for each new browser/device.",
    }
    print(json.dumps(report))

if __name__ == "__main__":
    main()
