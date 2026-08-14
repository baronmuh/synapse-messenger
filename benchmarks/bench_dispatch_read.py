"""Benchmark: authenticated read-path account lookups (hot path).

Measures the number of ``accounts.get`` SELECTs a ``get_messages`` read
performs, plus full service latency (warm auth cache, real config, W=1/W=8).

Pre-optimization (committed ``synapse/service.py``): 3.00 accounts.get per
read request — once inside ``_authenticate``, once again in ``_dispatch``,
and once via ``_org_of`` (evaluated eagerly even for non-audited reads).

Post-optimization: ``_authenticate`` returns the row it already fetched and
``_dispatch`` reuses it, and the audit ``org_name`` is read from that same
row only for audited commands — exactly 1.00 accounts.get per read.

Run the two states (``git stash`` the service change to measure before) and
compare with the SAME method. Machine must be quiet for a clean A/B.

Usage:  python benchmarks/bench_dispatch_read.py [LABEL]
"""
import json
import statistics
import sys
import threading
import time

sys.path.insert(0, "/home/baron/Projects/A2A")
from synapse.config import Config
from synapse.service import Service
from synapse.store import accounts as accounts_mod

config = Config.load("/home/baron/.local/share/synapse/config.json")
service = Service(config)

COUNTER = {"n": 0}
_orig = accounts_mod.get


def counting_get(conn, username):
    COUNTER["n"] += 1
    return _orig(conn, username)


accounts_mod.get = counting_get

payload = (
    json.dumps({"api_version": "v2", "command": "get_messages", "parameters": {
        "my_name_auth": "bench2", "my_password_auth": "mot-de-passe-bench-1",
        "status": None, "sender_username": None, "conversation_id": None,
        "limit": 50, "cursor": None}})
    + "\n"
).encode()
service.process(payload)  # warm the auth cache
COUNTER["n"] = 0


def run_one() -> float:
    t0 = time.perf_counter_ns()
    service.process(payload)
    return (time.perf_counter_ns() - t0) / 1e6


def bench(W: int, n: int) -> str:
    lats: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        loc = [run_one() for _ in range(n)]
        with lock:
            lats.extend(loc)

    ts = [threading.Thread(target=worker) for _ in range(W)]
    t0 = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    dur = time.perf_counter() - t0
    lats.sort()
    total = len(lats)

    def pct(p: float) -> float:
        return lats[min(total - 1, int(p / 100 * total))]

    return (f"  W={W} rps={total / dur:.0f} mean={statistics.fmean(lats):.2f} "
            f"p50={pct(50):.2f} p95={pct(95):.2f} accounts.get={COUNTER['n']}")


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "?"
    print(f"=== {label} ===")
    for W in (1, 8):
        COUNTER["n"] = 0
        print(bench(W, 150))
