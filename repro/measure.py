"""Which queries behind the issue #2868 endpoints go parallel, and how big their
shared (DSM) hash tables get.

Runs each endpoint once through the DRF test client, records every SQL
statement, then EXPLAIN ANALYZEs each one and sums the Parallel Hash nodes.
A Parallel Hash keeps its hash table in dynamic shared memory, i.e. /dev/shm
in Docker; so does a Parallel Bitmap Heap Scan's bitmap.

Run with: manage.py shell -c "exec(open(r'<this file>').read())"
Env: MEASURE_OUT (json path), MEASURE_ONLY (comma list of endpoint names).
"""

import json
import os
import time
from unittest import mock

from django.db import connection
from rest_framework.test import APIClient

from api.models import User

user = User.objects.get(username="big")
from django.conf import settings
settings.ALLOWED_HOSTS = ["*"]
client = APIClient()
client.force_authenticate(user)


def build_index():
    from api.search import similarity

    class _Resp:
        def json(self):
            return {"status": True, "index_size": 0}

    with mock.patch.object(similarity.sidecars, "post", return_value=_Resp()):
        similarity.build_image_similarity_index(user)


ENDPOINTS = {
    "stats": lambda: client.get("/api/stats/"),
    "serverstats": lambda: client.get("/api/serverstats/"),
    "thing_list": lambda: client.get("/api/albums/thing/list/"),
    "place_list": lambda: client.get("/api/albums/place/list/"),
    "people": lambda: client.get("/api/persons/"),
    "similarity_index": build_index,
}
only = [e for e in os.environ.get("MEASURE_ONLY", "").split(",") if e]


def walk(node, acc):
    t = node.get("Node Type")
    if t in ("Gather", "Gather Merge"):
        acc["gathers"] += 1
        acc["workers"] += node.get("Workers Launched", 0) or 0
    if t == "Hash" and node.get("Parallel Aware"):
        acc["parallel_hash"] += 1
        acc["hash_kb"] += node.get("Peak Memory Usage", 0) or 0
        acc["hash_batches"] = max(acc["hash_batches"], node.get("Hash Batches", 1))
    if t == "Bitmap Heap Scan" and node.get("Parallel Aware"):
        acc["parallel_bitmap"] += 1
    for child in node.get("Plans", []) or []:
        walk(child, acc)
    for key in ("InitPlan", "SubPlan"):
        pass


results = {}
for name, call in ENDPOINTS.items():
    if only and name not in only:
        continue
    captured = []

    def capture(execute, sql, params, many, context):
        named = getattr(getattr(context["cursor"], "cursor", None), "name", None)
        captured.append((sql, params, bool(named)))
        return execute(sql, params, many, context)

    start = time.perf_counter()
    with connection.execute_wrapper(capture):
        response = call()
    wall = time.perf_counter() - start
    status = getattr(response, "status_code", 200)

    summary = {
        "status": status,
        "wall_s": round(wall, 2),
        "queries": len(captured),
        "parallel_queries": 0,
        "parallel_hash_queries": 0,
        "parallel_hash_nodes": 0,
        "parallel_bitmap_nodes": 0,
        "max_hash_kb_per_query": 0,
        "sum_hash_kb": 0,
        "top": [],
    }
    for sql, params, named in captured:
        if not sql.lstrip().upper().startswith("SELECT") or named:
            continue
        with connection.cursor() as c:
            c.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params)
            plan = c.fetchone()[0]
        if isinstance(plan, str):
            plan = json.loads(plan)
        acc = dict(gathers=0, workers=0, parallel_hash=0, hash_kb=0, hash_batches=1, parallel_bitmap=0)
        walk(plan[0]["Plan"], acc)
        exec_ms = plan[0].get("Execution Time", 0)
        if acc["gathers"]:
            summary["parallel_queries"] += 1
        if acc["parallel_hash"]:
            summary["parallel_hash_queries"] += 1
        summary["parallel_hash_nodes"] += acc["parallel_hash"]
        summary["parallel_bitmap_nodes"] += acc["parallel_bitmap"]
        summary["sum_hash_kb"] += acc["hash_kb"]
        summary["max_hash_kb_per_query"] = max(summary["max_hash_kb_per_query"], acc["hash_kb"])
        if acc["gathers"]:
            summary["top"].append(
                {"ms": round(exec_ms, 1), "hash_kb": acc["hash_kb"], "workers": acc["workers"], "sql": sql[:300]}
            )
    summary["top"] = sorted(summary["top"], key=lambda r: -r["hash_kb"])[:6]
    results[name] = summary
    print(
        f"{name:18s} status={status} wall={wall:6.2f}s queries={summary['queries']:4d} "
        f"parallel={summary['parallel_queries']:3d} parallel_hash={summary['parallel_hash_queries']:3d} "
        f"max_hash={summary['max_hash_kb_per_query'] / 1024:6.1f}MB sum_hash={summary['sum_hash_kb'] / 1024:7.1f}MB "
        f"pbitmap={summary['parallel_bitmap_nodes']}",
        flush=True,
    )

out = os.environ.get("MEASURE_OUT")
if out:
    with open(out, "w") as f:
        json.dump(results, f, indent=1)
