#!/usr/bin/env python3
"""Dedicated remote command executor for Google Colab GPU sessions.

Usage:
    python exec_colab.py "nvidia-smi"
    python exec_colab.py check_ts.py
    python exec_colab.py -s t4 "tailscale status"
    python exec_colab.py   (interactive mode)
"""

import sys
import os
import json
import time
import subprocess

CONFIG_PATH = os.path.expanduser("~/.config/colab-cli/sessions.json")

def get_default_session():
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            if data:
                return next(iter(data.keys()))
    except Exception:
        pass
    return "t4"

SESSION_NAME = get_default_session()

def reset_kernel(session=SESSION_NAME):
    """Clear cached kernel_id/session_id so the next exec boots a fresh kernel."""
    try:
        if not os.path.exists(CONFIG_PATH):
            return False
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
        entry = data.get(session)
        if not entry:
            return False
        dropped = [k for k in ("kernel_id", "session_id") if entry.pop(k, None)]
        if not dropped:
            return False
        with open(CONFIG_PATH, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[*] Reset remote kernel for '{session}' ({', '.join(dropped)} cleared).")
        return True
    except Exception as e:
        print(f"[!] Warning: failed to reset kernel: {e}")
        return False

def execute(target, session=SESSION_NAME, timeout=60):
    """Execute a file or command string on the remote session."""
    target_clean = target.strip()
    if not target_clean:
        return 0

    is_file = os.path.isfile(target_clean) or target_clean.endswith((".py", ".sh", ".ipynb"))

    if is_file:
        cmd = f'colab exec -s {session} --timeout {timeout} -f "{target_clean}"'
        res = subprocess.run(cmd, shell=True)
    else:
        # Prepend '!' so IPython executes it as a shell command with real-time output
        wrapper = "\n".join(f"!{line}" if not line.startswith("!") and not line.startswith("%") else line
                            for line in target_clean.splitlines())
        cmd = f'colab exec -s {session} --timeout {timeout}'
        res = subprocess.run(cmd, input=wrapper, text=True, shell=True)

    if res.returncode != 0:
        print("[!] Execution returned non-zero code. Resetting kernel and retrying once...")
        reset_kernel(session)
        time.sleep(2)
        if is_file:
            res = subprocess.run(cmd, shell=True)
        else:
            res = subprocess.run(cmd, input=wrapper, text=True, shell=True)
    return res.returncode

def interactive(session=SESSION_NAME):
    print(f"\n=======================================================")
    print(f"  Colab Remote Execution Helper (Session: {session})")
    print(f"  Type any shell command or .py file to execute.")
    print(f"  Type 'reset' to reboot the kernel, 'exit' / 'q' to quit.")
    print(f"=======================================================\n")
    while True:
        try:
            line = input(f"colab[{session}]> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break
        if not line:
            continue
        if line.lower() in ("exit", "quit", "q"):
            break
        if line.lower() in ("reset", "/reset", "kernel", "/kernel"):
            reset_kernel(session)
            continue
        execute(line, session=session)

def main():
    args = sys.argv[1:]
    session = get_default_session()
    if args and args[0] in ("-s", "--session") and len(args) > 2:
        session = args[1]
        args = args[2:]

    if not args:
        interactive(session)
    else:
        cmd_str = " ".join(args)
        sys.exit(execute(cmd_str, session=session))

if __name__ == "__main__":
    main()
