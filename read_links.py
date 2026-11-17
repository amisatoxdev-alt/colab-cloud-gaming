import json
import os

if os.path.exists('/tmp/links.json'):
    print(open('/tmp/links.json').read())
else:
    print("{}")

