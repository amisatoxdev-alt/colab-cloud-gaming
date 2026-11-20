"""Take over a live Colab assignment the local store cannot address.

Two situations leave a healthy, billing VM unreachable:

  * `colab sessions` prints  [?] <runtime-id>   -- the server holds an
    assignment but ~/.config/colab-cli/sessions.json has no entry at all;
  * `colab sessions` prints  [abc123] <runtime-id> -- a leftover name from an
    earlier `colab new` that was called without `-s`.

Neither can be used by `colab exec -s t4`, and neither can be stopped by the
name we ask for, because the name we ask for is not the name that exists. Both,
however, carry `runtimeProxyInfo.token` / `.url` on the server record, which is
everything `SessionState` needs -- so the assignment can be re-registered
locally under our name.

This is NON-DESTRUCTIVE: the VM keeps running and stays billed.

Usage:
    python colab_adopt.py                    # adopt as 't4' (auto-pick a VM)
    python colab_adopt.py --name t4          # explicit name
    python colab_adopt.py --endpoint gpu-... # adopt that one specifically
    python colab_adopt.py --list             # only report

Exit code 0 means the name is now registered locally.
"""
import sys

from colab_cli.common import state
from colab_cli.state import SessionState

DEFAULT_NAME = "t4"


def _arg(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _pick(assignments, sessions, want_name, want_endpoint):
    """Choose the assignment to adopt."""
    if want_endpoint:
        return next((a for a in assignments if a.endpoint == want_endpoint), None)

    # Already tracked under the right name? Keep exactly that VM.
    want = sessions.get(want_name)
    if want:
        for a in assignments:
            if a.endpoint == want.endpoint:
                return a

    # Otherwise prefer a GPU assignment (a leftover CPU runtime is useless here).
    gpu = [a for a in assignments if a.accelerator.value != "NONE"]
    pool = gpu or assignments
    # Prioritize matching want_name accelerator (e.g. L4 or T4)
    exact = [a for a in pool if a.accelerator.value.lower() == (want_name or "").lower()]
    if exact:
        return exact[0]
    preferred = [a for a in pool if a.accelerator.value in ("L4", "T4")]
    return (preferred or pool)[0]


def main():
    name = _arg("--name", DEFAULT_NAME)
    endpoint = _arg("--endpoint")
    list_only = "--list" in sys.argv

    sessions, assignments = state.sync_sessions()
    if not assignments:
        print("[adopt] No server-side assignments.")
        return 1

    local_by_endpoint = {s.endpoint: s.name for s in sessions.values()}
    for a in assignments:
        tag = local_by_endpoint.get(a.endpoint, "?")
        print(f"[srv]   [{tag}] {a.endpoint}  "
              f"{a.accelerator.value}  {a.machine_shape.name}")
    if list_only:
        return 0

    target = _pick(assignments, sessions, name, endpoint)
    if target is None:
        print(f"[adopt] FAIL: no assignment matched endpoint={endpoint!r}")
        return 1

    s = SessionState(
        name=name,
        token=target.runtime_proxy_info.token,
        url=target.runtime_proxy_info.url,
        endpoint=target.endpoint,
        token_expires_at=target.runtime_proxy_info.expires_at(),
        variant=target.variant.name,
        accelerator=target.accelerator.value,
        machine_shape=target.machine_shape.name,
    )
    try:
        state.store.add(s)
    except Exception as exc:                                    # noqa: BLE001
        print(f"[adopt] FAIL: could not write the local store: {exc}")
        return 1

    # Drop local names that duplicate the adopted VM or point at a VM that no
    # longer exists server-side; leaving them makes `colab sessions` print
    # duplicates and makes a later `colab stop` unassign the wrong record.
    live_endpoints = {a.endpoint for a in assignments}
    for other_name, old in list(sessions.items()):
        if other_name == name:
            continue
        if old.endpoint == target.endpoint or old.endpoint not in live_endpoints:
            state.store.remove(other_name)
            print(f"[adopt] dropped stale local name '{other_name}'")

    state.history.log_event(name, "session_adopted", {
        "endpoint": target.endpoint,
        "accelerator": target.accelerator.value,
        "reason": "local_name_missing",
    })
    print(f"[adopt] OK {target.endpoint} -> '{name}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
