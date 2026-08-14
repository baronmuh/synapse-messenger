"""Benchmark: read_message write-path transactions (hot path #2).

Measures how many write transactions (db.begin_immediate — the global
writer lock) a read_message request opens, plus full-service latency at
W=1/W=8 (warm auth cache, real config, production bench DB pool).

BEFORE (committed pre-fix): every read_message wrapped the ENTIRE read
in db.begin_immediate — the application-wide write lock — even when
nothing was written (sender reads, already-read messages with a no-op
mark_read_conditional), PLUS the mandatory audit transaction: 2 write
txns per read, each serialized against every send/mark.

AFTER: only the recipient's first read writes. Sender reads and
already-read reads open only the mandatory audit transaction: 1 write
txn per read.

Modes (both are pure-read paths, no pool mutation):
  already-read : bench5 (recipient) re-reads a pool message (read_at set)
  sender       : bench6 (sender) reads its own sent pool message

Run the two states back-to-back with the same command and compare:
  git stash push -- synapse/service.py   # BEFORE
  python benchmarks/bench_read_write_path.py BEFORE already-read
  git stash pop                          # AFTER
  python benchmarks/bench_read_write_path.py AFTER already-read
"""
import json
import statistics
import sys
import threading
import time

sys.path.insert(0, "/home/baron/Projects/A2A")
from synapse import db as db_mod
from synapse.config import Config
from synapse.service import Service

config = Config.load("/home/baron/.local/share/synapse/config.json")
service = Service(config)

import sqlite3

_db = sqlite3.connect(config.db_path)
_READ_IDS = [
    r[0]
    for r in _db.execute(
        "SELECT message_id FROM messages WHERE sender_username='bench6' "
        "AND recipient_username='bench5' ORDER BY created_at"
    ).fetchall()
]
_db.close()

COUNTER = {"n": 0}
_orig_begin = db_mod.begin_immediate


def counting_begin(conn):
    COUNTER["n"] += 1
    return _orig_begin(conn)


db_mod.begin_immediate = counting_begin


def _payload(mid: str, user: str) -> bytes:
    return (
        json.dumps(
            {
                "api_version": "v2",
                "command": "read_message",
                "parameters": {
                    "my_name_auth": user,
                    "my_password_auth": "mot-de-passe-bench-1",
                    "message_id": mid,
                },
            }
        )
        + "\n"
    ).encode()


MODE = sys.argv[2] if len(sys.argv) > 2 else "already-read"
LABEL = sys.argv[1] if len(sys.argv) > 1 else "?"
if MODE == "already-read":
    mid = _READ_IDS[0]
    user = "bench5"  # recipient; pool messages are already read
elif MODE == "sender":
    mid = _READ_IDS[0]
    user = "bench6"  # sender reads its own message: never writes
else:  # pragma: no cover
    sys.exit(f"unknown mode {MODE!r} (already-read|sender)")
PAYLOAD = _payload(mid, user)

service.process(PAYLOAD)  # warm the auth cache
COUNTER["n"] = 0


def run_one() -> float:
    t0 = time.perf_counter_ns()
    resp, _ = service.process(PAYLOAD)
    assert resp["success"] is True
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

    return (
        f"  W={W} rps={total / dur:.0f} mean={statistics.fmean(lats):.2f} "
        f"p50={pct(50):.2f} p95={pct(95):.2f} p99={pct(99):.2f} "
        f"write_txns={COUNTER['n']} per_req={COUNTER['n'] / total:.2f}"
    )


if __name__ == "__main__":
    print(f"=== {LABEL} mode={MODE} mid={mid[:8]} user={user} ===")
    for W in (1, 8):
        COUNTER["n"] = 0
        print(bench(W, 150))
