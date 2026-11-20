"""Print the real datacenter zone of a locally-tracked Colab session (JSON).

`colab sessions` only shows the runtime id, and its region token is an
abbreviation that is easy to get wrong:

    gpu-t4-s-ass1c2-222r7xhmk9xse   ->  asia-southeast1  (Singapore)

The session URL carries the authoritative region name:

    https://8080-gpu-t4-s-ass1c2-222r7xhmk9xse-c.asia-southeast1.prod.r.colab.dev
                                                 ^^^^^^^^^^^^^^^^^ zone

So read the URL out of the local store and let Google name the region, instead
of guessing from the id.

Usage:
    python colab_zone.py                # our default session name
    python colab_zone.py --name t4

Output (single JSON line on stdout):
    {"name","endpoint","zone","family","url"}   empty strings when unknown
"""
import json
import os
import re
import sys

DEFAULT_NAME = "t4"

# .asia-southeast1.prod.r.colab.dev
ZONE_IN_URL = re.compile(r"\.([a-z]{2,}-[a-z]+\d)\.prod\.")


def _arg(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def family_of_zone(zone):
    """Map a GCP region name to the launcher's preference family."""
    z = (zone or "").strip().lower()
    if z.startswith("asia-southeast"):
        return "sg"
    if z.startswith(("us-east", "us-west", "us-central")):
        return "us"
    if z.startswith("europe-"):
        return "eu"
    return ""


def main():
    name = _arg("--name", None)
    if not name:
        sessions_path = os.path.expanduser("~/.config/colab-cli/sessions.json")
        if os.path.exists(sessions_path):
            try:
                with open(sessions_path, "r") as f:
                    data = json.load(f)
                if data:
                    name = next(iter(data.keys()))
            except Exception:
                pass
    if not name:
        name = DEFAULT_NAME
    endpoint = url = zone = ""

    # Fast path: check local store directly from disk (0.001s, no network call or timeout risk)
    sessions_path = os.path.expanduser("~/.config/colab-cli/sessions.json")
    if os.path.exists(sessions_path):
        try:
            with open(sessions_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                entry = data.get(name)
                if entry and isinstance(entry, dict) and entry.get("endpoint"):
                    endpoint = entry.get("endpoint", "")
                    url = entry.get("url", "")
                    m = ZONE_IN_URL.search(url or "")
                    if m:
                        zone = m.group(1)
        except Exception:
            pass

    # Fallback to network sync only if not in the local sessions store
    if not zone or not endpoint:
        sessions, assignments = {}, []
        try:
            from colab_cli.common import state
            sessions, assignments = state.sync_sessions()
        except (Exception, SystemExit):
            pass

        s = sessions.get(name)
        if s:
            endpoint, url = s.endpoint, s.url
            m = ZONE_IN_URL.search(url or "")
            if m:
                zone = m.group(1)

        if not zone and assignments:
            a = next((x for x in assignments if x.endpoint == endpoint), assignments[0])
            endpoint = a.endpoint
            url = url or a.runtime_proxy_info.url
            m = ZONE_IN_URL.search(url or "")
            if m:
                zone = m.group(1)

    print(json.dumps({
        "name": name,
        "endpoint": endpoint,
        "zone": zone,
        "family": family_of_zone(zone),
        "url": url,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())

