"""Supplementary hot-path measurement: task CRUD + registry (gate t_ef01fc51).

The main bench (bench.py) covers messaging commands; the gate body also
names task CRUD and registry (agent directory). This script measures those
commands on the SAME socket harness (production Argon2id, W=8, 10 s) so the
gate report has real numbers for the full hot-path list. There is no
pre-optimization baseline for these commands (baseline t_af15796f measured
the 8 messaging commands only), so these are first-measurement numbers.

Usage: python benchmarks/bench_task_crud.py --socket /tmp/synapse-bench/synapse.sock
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse.client import Client  # noqa: E402

AGENT_PW = "mot-de-passe-bench-1"
COUNTER = {"n": 0}


def _payload(command: str, parameters: dict) -> bytes:
    return (json.dumps({"api_version": "v2", "command": command,
                        "parameters": parameters}) + "\n").encode()


def _auth(me: str) -> dict:
    return {"my_name_auth": me, "my_password_auth": AGENT_PW}


def req_create_task() -> bytes:
    COUNTER["n"] += 1
    return _payload("create_task", {
        **_auth("bench1"),
        "title": f"gate-task-{COUNTER['n']}",
        "description": None, "priority": "normal", "due_at": None,
        "assignee_username": None, "business_reference": None,
    })


def req_list_tasks() -> bytes:
    return _payload("list_tasks", {**_auth("bench1"), "state": None,
                                   "assignee_username": None, "limit": 50,
                                   "cursor": None})


def req_update_task() -> bytes:
    return _payload("update_task_state", {
        **_auth("bench1"),
        "task_id": "00000000-0000-4000-8000-000000000000",
        "new_state": "in_progress",
    })


def req_task_status() -> bytes:
    return _payload("get_task_status", {
        **_auth("bench1"),
        "task_id": "00000000-0000-4000-8000-000000000000",
    })


def req_registry() -> bytes:
    return _payload("get_agent_description", {**_auth("bench1"),
                                              "username": "bench2"})


COMMANDS = {
    "task:create": req_create_task,
    "task:list": req_list_tasks,
    "task:update(404-path)": req_update_task,
    "task:status(404-path)": req_task_status,
    "registry:get_agent_description": req_registry,
}


async def worker(payload_fn, lats, stop, socket_path):
    reader, writer = await asyncio.open_unix_connection(socket_path,
                                                        limit=256 * 1024)
    try:
        while not stop.is_set():
            t0 = time.perf_counter_ns()
            writer.write(payload_fn())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=20)
            lats.append((time.perf_counter_ns() - t0) / 1e6)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def run_one(name, fn, w, duration, socket_path):
    stop = asyncio.Event()
    lats = []
    tasks = [asyncio.create_task(worker(fn, lats, stop, socket_path))
             for _ in range(w)]
    await asyncio.sleep(2.0)  # warmup, ignored
    lats.clear()
    start = time.perf_counter()
    await asyncio.sleep(duration)
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    dur = time.perf_counter() - start
    s = sorted(lats)
    n = len(s)
    pct = lambda p: s[min(n - 1, int(p / 100 * n))]
    print(f"{name:<32} RPS {n/dur:>7.2f} | mean {sum(s)/n:>7.2f} ms | "
          f"p50 {pct(50):>7.2f} | p95 {pct(95):>7.2f} | p99 {pct(99):>7.2f} | "
          f"n={n}")
    return {"scenario": name, "rps": round(n / dur, 2),
            "lat_moy_ms": round(sum(s) / n, 2) if s else 0,
            "p50_ms": round(pct(50), 2), "p95_ms": round(pct(95), 2),
            "p99_ms": round(pct(99), 2), "requetes": n}


async def main_async(socket_path, duration):
    print(f"== Hot path supplement: task CRUD + registry (W=8, {duration}s) ==")
    print(f"socket: {socket_path}")
    results = []
    for name, fn in COMMANDS.items():
        st = await run_one(name, fn, 8, duration, socket_path)
        results.append(st)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    # verify connectivity + seed one task so list is non-trivial
    c = Client(args.socket)
    try:
        c.create_task("gate-seed-task", "bench2", "bench1", AGENT_PW)
    except Exception:
        pass
    results = asyncio.run(main_async(args.socket, args.duration))
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"scenarios": results}, f, indent=1)
        print(f"saved: {args.out}")


if __name__ == "__main__":
    main()
