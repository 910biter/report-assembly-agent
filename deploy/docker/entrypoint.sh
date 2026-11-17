#!/usr/bin/env bash
set -euo pipefail

python - <<'PY'
import os
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

url = os.environ.get("IRA_QDRANT_URL", "http://qdrant:6333").rstrip("/") + "/healthz"
for attempt in range(60):
    try:
        with urlopen(url, timeout=2) as response:
            if response.status == 200:
                break
    except (OSError, URLError):
        pass
    time.sleep(2)
else:
    sys.exit(f"Qdrant did not become ready: {url}")
PY

exec python /opt/app/run.py
