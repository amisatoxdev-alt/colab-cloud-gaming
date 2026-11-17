import json
import os

for path in ['/var/log/services/links.json', '/tmp/links.json']:
    if os.path.exists(path):
        try:
            print(open(path).read())
            exit(0)
        except Exception:
            pass

print("{}")
