"""Issue #2868 repro: hit the admin-page endpoints concurrently and count the
requests that fail with Postgres' "could not resize shared memory segment".

The admin page loads /api/serverstats/, /api/stats/ and the thing list
together; a few tabs or a running job make that concurrent. Each thread is
one Django connection, as a uvicorn worker would be.

Run with: manage.py shell -c "exec(open(r'<this file>').read())"
Env: HAMMER_THREADS (6), HAMMER_SECONDS (90), HAMMER_OUT (json path).
"""

import collections
import json
import os
import threading
import time
from unittest import mock

from django.conf import settings
from django.db import connection
from rest_framework.test import APIClient

from api.models import User

settings.ALLOWED_HOSTS = ["*"]
THREADS = int(os.environ.get("HAMMER_THREADS", "6"))
SECONDS = float(os.environ.get("HAMMER_SECONDS", "90"))
user = User.objects.get(username="big")


def build_index():
    from api.search import similarity

    class _Resp:
        def json(self):
            return {"status": True, "index_size": 0}

    with mock.patch.object(similarity.sidecars, "post", return_value=_Resp()):
        similarity.build_image_similarity_index(user)


PATHS = ["/api/serverstats/", "/api/stats/", "/api/albums/thing/list/", "/api/albums/place/list/"]
lock = threading.Lock()
ok = collections.Counter()
shm_errors = collections.Counter()
other_errors = collections.Counter()
latencies = collections.defaultdict(list)
deadline = time.monotonic() + SECONDS


def worker(n):
    client = APIClient(raise_request_exception=True)
    client.force_authenticate(user)
    i = n
    try:
        while time.monotonic() < deadline:
            path = PATHS[i % len(PATHS)]
            i += 1
            start = time.perf_counter()
            try:
                response = client.get(path)
                status = response.status_code
                error = None
            except Exception as exc:  # noqa: BLE001
                status = None
                error = exc
            took = time.perf_counter() - start
            with lock:
                if error is not None:
                    text = str(error)
                    if "shared memory" in text or type(error).__name__ == "DiskFull" or "DiskFull" in repr(type(getattr(error, "__cause__", None))):
                        shm_errors[path] += 1
                    else:
                        other_errors[f"{path} {type(error).__name__}: {text[:120]}"] += 1
                elif status == 200:
                    ok[path] += 1
                    latencies[path].append(took)
                else:
                    other_errors[f"{path} HTTP {status}"] += 1
    finally:
        connection.close()


threads = [threading.Thread(target=worker, args=(n,)) for n in range(THREADS)]
for t in threads:
    t.start()
for t in threads:
    t.join()

result = {
    "threads": THREADS,
    "seconds": SECONDS,
    "ok": dict(ok),
    "shm_errors": dict(shm_errors),
    "other_errors": dict(other_errors),
    "median_s": {p: round(sorted(v)[len(v) // 2], 2) for p, v in latencies.items() if v},
}
print(json.dumps(result, indent=1), flush=True)
out = os.environ.get("HAMMER_OUT")
if out:
    with open(out, "w") as f:
        json.dump(result, f, indent=1)
