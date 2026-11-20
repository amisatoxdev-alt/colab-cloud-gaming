#!/usr/bin/env python3
"""Builds a self-contained base64 bundle of cloud_portal for remote Colab deployment."""

import os
import io
import gzip
import tarfile
import base64

def build_bundle():
    portal_dir = os.path.dirname(os.path.abspath(__file__))
    out_file = os.path.join(portal_dir, "portal_bundle.py")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(portal_dir):
            if "__pycache__" in root:
                continue
            for f in sorted(files):
                if f.endswith(".pyc") or f.startswith("test_") or f in ("bundle.py", "portal_bundle.py"):
                    continue
                full_path = os.path.join(root, f)
                arcname = os.path.relpath(full_path, portal_dir)
                tar.add(full_path, arcname=arcname)
                print(f"  Added to bundle: {arcname}")

    compressed_bytes = buf.getvalue()
    b64_str = base64.b64encode(compressed_bytes).decode("ascii")

    content = f'''#!/usr/bin/env python3
"""Self-contained standalone bundle of Nebula Cloud Gaming Portal.
Automatically generated. Extracts portal files to /opt/cloud_portal.
"""

import os
import io
import tarfile
import base64

PORTAL_TAR_GZ_B64 = """{b64_str}"""

def extract_portal(target_dir="/opt/cloud_portal"):
    os.makedirs(target_dir, exist_ok=True)
    raw = base64.b64decode(PORTAL_TAR_GZ_B64.strip())
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        tar.extractall(target_dir)
    print(f"[Portal] Extracted Nebula Cloud Gaming Portal to {{target_dir}}")

if __name__ == "__main__":
    extract_portal()
'''

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\n[Bundle Complete] Wrote {out_file} ({len(b64_str)} base64 chars, {len(compressed_bytes)} bytes compressed).")

if __name__ == "__main__":
    build_bundle()

