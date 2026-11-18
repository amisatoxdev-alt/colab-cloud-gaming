"""Inspect and free Colab server-side assignments that have no local name.

`colab sessions` prints  [?] <runtime-id>  when a server assignment exists but
the local sessions.json has no matching entry -- usually after a failed or
interrupted re-allocation. `colab stop` cannot touch those: it resolves by name
through the local store, so it answers "Session '?' not found". The only way to
release them is the client API's unassign(endpoint).

Usage:
    python colab_free.py            # list
    python colab_free.py --free     # unassign every orphaned assignment
"""
import sys

from colab_cli.common import state


def main():
    free = "--free" in sys.argv
    sessions, assignments = state.sync_sessions()

    if not assignments:
        print("[ok] No server-side assignments.")
        return 0

    name_by_endpoint = {s.endpoint: s.name for s in sessions.values()}
    orphans = []
    for a in assignments:
        name = name_by_endpoint.get(a.endpoint)
        tag = name if name else "<orphan>"
        print(f"  {tag:12s} {a.endpoint}  acc={a.accelerator.value} "
              f"shape={a.machine_shape.name}")
        if not name:
            orphans.append(a)

    if not orphans:
        print("[ok] Every assignment is tracked locally by name.")
        return 0

    if not free:
        print(f"\n{len(orphans)} orphaned assignment(s). Re-run with --free to release.")
        return 0

    for a in orphans:
        try:
            state.client.unassign(a.endpoint)
            print(f"[freed] {a.endpoint}")
        except Exception as exc:                      # noqa: BLE001
            print(f"[fail ] {a.endpoint}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
