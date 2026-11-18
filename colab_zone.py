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
import re
import sys

from colab_cli.common import state

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
    name = _arg("--name", DEFAULT_NAME)
    endpoint = url = zone = ""
    sessions, assignments = {}, []
    try:
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
