"""Focused re-measurement of the 4 gate-flagged scenarios (noise vs regression).

The full gate run flagged 4 high-concurrency scenarios (regime_etabli:W16,
sweep:W24:pass1, sweep:W48:pass1, sweep:W64:pass2) with >10% movement on
RPS or p95. The initial analysis shows pass1/pass2 contradiction and
improved p50 — this script re-measures each flagged scenario 3 times on
the same isolated server to decide noise vs regression.

Usage: python benchmarks/results/gate_rerun_flagged.py --socket /tmp/synapse-bench/synapse.sock
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib.util  # noqa: E402

_BENCH_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "bench.py")
_spec = importlib.util.spec_from_file_location("bench_mod", _BENCH_PATH)
bench_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench_mod)
CONV_B = bench_mod.CONV_B
READ_IDS = bench_mod.READ_IDS
SERVER_PID = bench_mod.SERVER_PID
_counter = bench_mod._counter
bootstrap = bench_mod.bootstrap
mixed_payload = bench_mod.mixed_payload
run_scenario = bench_mod.run_scenario
run_steady = bench_mod.run_steady
from synapse.config import Config  # noqa: E402


async def main(socket_path: str, config: Config, out: str) -> None:
    results = []
    # regime_etabli:W16 (steady-state, stable connections)
    print("== regime_etabli:W16 x3 ==")
    for i in range(3):
        st = await run_steady(16, 10, socket_path)
        results.append(st)
        print(f"  run{i+1}: RPS {st['rps']:>7.2f} p50 {st['p50_ms']:>7.2f} "
              f"p95 {st['p95_ms']:>7.2f} p99 {st['p99_ms']:>7.2f} "
              f"err {st['taux_erreur_pct']}%")
    # sweep scenarios (mixed load, one pass each, x3)
    for name, w in (("sweep:W24", 24), ("sweep:W48", 48), ("sweep:W64", 64)):
        print(f"== {name} x3 ==")
        for i in range(3):
            st = await run_scenario(f"{name}:rerun{i+1}", mixed_payload, w,
                                    10, socket_path)
            results.append(st)
            print(f"  run{i+1}: RPS {st['rps']:>7.2f} p50 {st['p50_ms']:>7.2f} "
                  f"p95 {st['p95_ms']:>7.2f} p99 {st['p99_ms']:>7.2f} "
                  f"err {st['taux_erreur_pct']}% codes={st['codes_erreur']}")
    # write-path tail check: mark_conversation_no_reply (W=8) x3
    print("== commande:mark_conversation_no_reply W=8 x3 ==")
    for i in range(3):
        st = await run_scenario(f"commande:mark_no_reply:rerun{i+1}", bench_mod.req_mark,
                                8, 10, socket_path)
        results.append(st)
        print(f"  run{i+1}: RPS {st['rps']:>7.2f} p50 {st['p50_ms']:>7.2f} "
              f"p95 {st['p95_ms']:>7.2f} p99 {st['p99_ms']:>7.2f} "
              f"err {st['taux_erreur_pct']}% codes={st['codes_erreur']}")
    with open(out, "w") as f:
        json.dump({"scenarios": results}, f, indent=1)
    print(f"saved: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--out", default="benchmarks/results/gate-rerun.json")
    args = parser.parse_args()
    config = Config.from_dict({"socket_path": args.socket,
                               "storage_dir": "/tmp/synapse-bench/var",
                               "run_dir": "/tmp/synapse-bench/run",
                               "log_dir": "/tmp/synapse-bench/logs",
                               "backup_dir": "/tmp/synapse-bench/backups"})
    bench_mod.CONV_B = bootstrap(config, "mot-de-passe-bench-admin-1")
    bench_mod.SERVER_PID = int(open(config.lock_path).read().strip())
    bench_mod.READ_IDS = READ_IDS
    out = args.out if os.path.isabs(args.out) else os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        args.out)
    asyncio.run(main(args.socket, config, out))
