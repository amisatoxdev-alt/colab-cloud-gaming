"""Inspect and release Colab server-side assignments.

`colab stop` resolves a name through the local store, so it cannot touch an
assignment that has no local name (`[?]`) or that lives under a different name
than the one you ask for. The only way to release those is the client API's
unassign(endpoint), which is what this does.

DESTRUCTIVE: unassign() tears the VM down. Use colab_adopt.py instead when the
goal is merely to reach a VM that is already running.

Usage:
    python colab_free.py            # list
    python colab_free.py --free     # unassign every assignment but --name
    python colab_free.py --free --name t4
    python colab_free.py --free --all   # unassign even the tracked name
"""
import sys

from colab_cli.common import state

DEFAULT_NAME = "t4"


def _arg(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    free = "--free" in sys.argv
    free_all = "--all" in sys.argv
    keep = _arg("--name", DEFAULT_NAME)

    sessions, assignments = state.sync_sessions()
    if not assignments:
        print("[ok] No server-side assignments.")
        return 0

    name_by_endpoint = {s.endpoint: s.name for s in sessions.values()}
    victims = []
    for a in assignments:
        local = name_by_endpoint.get(a.endpoint)
        tag = local if local else "<orphan>"
        print(f"  {tag:12s} {a.endpoint}  acc={a.accelerator.value} "
              f"shape={a.machine_shape.name}")
        # Anything not tracked under `keep` is a stale allocation that blocks
        # the next `colab new` with a 412 precondition failure.
        if free_all or local != keep:
            victims.append(a)

    if not victims:
        print(f"[ok] Nothing to release (only '{keep}' is assigned).")
        return 0

    if not free:
        print(f"\n{len(victims)} assignment(s) would be released. "
              f"Re-run with --free to do it.")
        return 0

    failed = 0
    for a in victims:
        try:
            state.client.unassign(a.endpoint)
            print(f"[freed] {a.endpoint}")
            for n, s in list(sessions.items()):
                if s.endpoint == a.endpoint:
                    state.store.remove(n)
        except Exception as exc:                                # noqa: BLE001
            failed += 1
            print(f"[fail ] {a.endpoint}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
