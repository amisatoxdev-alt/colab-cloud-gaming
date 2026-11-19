#!/usr/bin/env python3
"""
logout_colab.py - Log out of Google Colab CLI and clear stored OAuth credentials.

What it does:
1. Deletes the Google Colab OAuth token stored in Windows Credential Manager (via keyring).
2. Deletes legacy ~/.config/colab-cli/token.json if present.
3. Clears ~/.config/colab-cli/sessions.json and removes stale locks and backups.
4. Optionally stops any active Google Colab GPU sessions before logging out.
5. Allows seamlessly switching to another Google account on the next run.
"""

import argparse
import glob
import json
import os
import subprocess
import sys

# Windows VT100 color support
if sys.platform == "win32":
    os.system("color")

C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_RED = "\033[91m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_CYAN = "\033[96m"
C_WHITE = "\033[97m"
C_GRAY = "\033[90m"

KEYRING_SERVICE = "google-colab-cli"
KEYRING_USERNAME = "oauth_token"
CONFIG_DIR = os.path.expanduser("~/.config/colab-cli")
LEGACY_TOKEN_PATH = os.path.join(CONFIG_DIR, "token.json")
SESSIONS_PATH = os.path.join(CONFIG_DIR, "sessions.json")


def log_step(step, msg):
    print(f"{C_CYAN}[{step}]{C_RESET} {C_WHITE}{msg}{C_RESET}", flush=True)


def log_ok(msg):
    print(f"{C_GREEN}[✓]{C_RESET} {C_WHITE}{msg}{C_RESET}", flush=True)


def log_warn(msg):
    print(f"{C_YELLOW}[!]{C_RESET} {C_WHITE}{msg}{C_RESET}", flush=True)


def log_err(msg):
    print(f"{C_RED}[✗]{C_RESET} {C_WHITE}{msg}{C_RESET}", flush=True)


def get_stored_token():
    """Check if an OAuth token is present in Keyring or legacy file."""
    try:
        import keyring
        tok = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        if tok:
            return "keyring"
    except Exception:
        pass
    if os.path.exists(LEGACY_TOKEN_PATH):
        return "file"
    return None


def get_active_sessions():
    """Read local session store to check for active sessions."""
    if not os.path.exists(SESSIONS_PATH):
        return {}
    try:
        with open(SESSIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("endpoint")}
    except Exception:
        pass
    return {}


def stop_active_sessions():
    """Attempt to stop active Colab sessions using colab CLI."""
    sessions = get_active_sessions()
    if not sessions:
        try:
            res = subprocess.run(["colab", "sessions"], capture_output=True, text=True, timeout=15)
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip().startswith("[")]
            lines = [l for l in lines if not l.startswith("[colab]")]
            if not lines:
                return
        except Exception:
            return

    log_step("STOP", "Stopping active Colab GPU sessions before logout...")
    for name in sessions.keys():
        try:
            print(f"      {C_GRAY}Stopping session '{name}'...{C_RESET}", flush=True)
            subprocess.run(["colab", "stop", name], capture_output=True, text=True, timeout=30)
        except Exception as e:
            log_warn(f"Failed to stop session '{name}': {e}")


def clear_keyring():
    """Delete OAuth token from OS Credential Manager."""
    try:
        import keyring
        import keyring.errors
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
            log_ok("Removed Colab OAuth credentials from Windows Credential Manager.")
            return True
        except keyring.errors.PasswordDeleteError:
            log_step("AUTH", "No OAuth credentials found in Windows Credential Manager.")
            return True
        except Exception as e:
            log_warn(f"Keyring removal returned: {e}")
            return False
    except ImportError:
        log_warn("Python 'keyring' module not found; skipping Windows Vault cleanup.")
        return False


def clear_session_files():
    """Reset sessions.json and clear temporary lock/backup files."""
    cleared = 0
    if os.path.exists(LEGACY_TOKEN_PATH):
        try:
            os.remove(LEGACY_TOKEN_PATH)
            cleared += 1
            log_ok("Removed legacy token file.")
        except Exception as e:
            log_warn(f"Could not remove legacy token file: {e}")

    if os.path.exists(SESSIONS_PATH):
        try:
            with open(SESSIONS_PATH, "w", encoding="utf-8") as f:
                f.write("{}\n")
            cleared += 1
            log_ok("Reset session state cache (sessions.json).")
        except Exception as e:
            log_warn(f"Could not reset sessions.json: {e}")

    for bak in glob.glob(os.path.join(CONFIG_DIR, "sessions.json*bak*")):
        try:
            os.remove(bak)
        except Exception:
            pass

    for lock in glob.glob(os.path.join(CONFIG_DIR, "*.lock")):
        try:
            os.remove(lock)
        except Exception:
            pass


def logout(force=False, stop_sessions=False):
    """Main logout workflow."""
    print(f"\n{C_CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
    print(f" {C_WHITE}{C_BOLD}Google Colab CLI — Account Logout Tool{C_RESET}")
    print(f"{C_CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}\n")

    auth_source = get_stored_token()
    sessions = get_active_sessions()

    if not auth_source and not sessions:
        log_ok("You are already logged out (no stored credentials or active sessions found).")
        print()
        return 0

    if auth_source:
        log_step("AUTH", f"Found active Google Colab credentials in {auth_source}.")
    if sessions:
        names = ", ".join(sessions.keys())
        log_warn(f"Active session configuration found: {C_BOLD}{names}{C_RESET}")

    if not force:
        try:
            prompt = (
                f"\n{C_YELLOW}[?]{C_RESET} {C_BOLD}Are you sure you want to log out and clear "
                f"stored credentials? [Y/n]: {C_RESET}"
            )
            ans = input(prompt).strip().lower()
            if ans not in ("", "y", "yes"):
                print(f"\n{C_GRAY}Logout cancelled by user.{C_RESET}\n")
                return 0
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return 1

    if stop_sessions:
        stop_active_sessions()
    elif sessions and not force:
        try:
            s_prompt = (
                f"{C_YELLOW}[?]{C_RESET} {C_BOLD}Release/stop active Colab GPU sessions before logging out? "
                f"[Recommended: y] [Y/n]: {C_RESET}"
            )
            s_ans = input(s_prompt).strip().lower()
            if s_ans in ("", "y", "yes"):
                stop_active_sessions()
        except (KeyboardInterrupt, EOFError):
            pass

    print()
    log_step("LOGOUT", "Clearing credentials and local session stores...")
    clear_keyring()
    clear_session_files()

    # Verify
    verify_tok = get_stored_token()
    if not verify_tok:
        print(f"\n{C_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
        print(f"{C_GREEN}[✓] Successfully logged out of Google Colab!{C_RESET}")
        print(f"    {C_WHITE}Next time you run {C_BOLD}{C_GREEN}start.bat{C_RESET}{C_WHITE}, you will be prompted{C_RESET}")
        print(f"    {C_WHITE}to authenticate with your new/desired Google account.{C_RESET}")
        print(f"{C_GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}\n")
        return 0
    else:
        log_warn("Credentials could not be fully purged. Please check Windows Credential Manager.")
        return 1


def main():
    parser = argparse.ArgumentParser(description="Log out of Google Colab CLI and switch accounts.")
    parser.add_argument("-f", "--force", action="store_true", help="Skip confirmation prompts")
    parser.add_argument("--stop", action="store_true", help="Stop active sessions before logging out")
    args = parser.parse_args()

    return logout(force=args.force, stop_sessions=args.stop)


if __name__ == "__main__":
    sys.exit(main())

