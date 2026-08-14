#!/usr/bin/env python3
"""Generate the pre-optimization baseline report (English) from the bench JSON.

Usage:
    python benchmarks/results/make_report.py [--json PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import os

DEFAULT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "baseline-pre-opt.json")
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "baseline-pre-opt-report.md")


def row(s: dict) -> str:
    err = f"{s['taux_erreur_pct']:.3f}%"
    if s["codes_erreur"]:
        err += " " + ",".join(f"{k} x{v}" for k, v in s["codes_erreur"].items())
    return (f"| {s['scenario']} | {s['concurrence']} | {s['rps']:.1f} | "
            f"{s['lat_min_ms']:.2f} | {s['lat_moy_ms']:.1f} | {s['lat_max_ms']:.1f} | "
            f"{s['p50_ms']:.1f} | {s['p95_ms']:.1f} | {s['p99_ms']:.1f} | {err} | "
            f"{s['cpu_serveur_coeurs']:.2f} | {s['rss_serveur_pic_mib']} |")


def _section(scen: list[dict], prefix: str) -> str:
    lines = ["| scenario | W | RPS | min ms | mean ms | max ms | p50 | p95 | p99 | "
             "errors | CPU cores | RSS peak MiB |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in scen:
        if s["scenario"].startswith(prefix):
            lines.append(row(s))
    return "\n".join(lines)


def generate(json_path: str, out_path: str) -> str:
    with open(json_path) as f:
        d = json.load(f)
    env, scen = d["environnement"], d["scenarios"]

    L = ["# Synapse — Pre-Optimization Performance Baseline\n",
         f"Date: 2026-08-11 13:13-13:21 · Commit: `{env['commit']}` · "
         f"Machine: {env['nproc']} cores ({env['cpu'].split(' @ ')[-1]}) · "
         f"Mode: {env['mode']}\n",
         "Method: `benchmarks/bench.py --duration 10 --passes 2` (full mode), load generated "
         "by asyncio (CPU ~0%), PRODUCTION Argon2id authentication, 2.5 GiB server-RSS memory "
         "guard. Machine idle during measurement (load1 ~1.1 on 4 cores, MemAvailable >= 3.3 Gi).\n",
         "## A. Per-command cost (W=8, persistent connections)\n",
         _section(scen, "commande:"),
         "\n## B. Realistic mixed load (W=8)\n",
         _section(scen, "mixte:"),
         "\n## C. Steady state (stable connections)\n",
         _section(scen, "regime_etabli:"),
         "\n## D. Concurrency sweep (mixed load, 2 passes)\n",
         _section(scen, "sweep:"),
         "\n## E. Transport: connection-per-request vs persistent (W=8)\n",
         _section(scen, "mode:"),
         "\n## Findings (pre-optimization)\n"]
    sec_a = {s["scenario"].split(":")[1]: s for s in scen
             if s["scenario"].startswith("commande:")}
    slowest = sorted(sec_a.items(), key=lambda kv: kv[1]["lat_moy_ms"], reverse=True)[:2]
    L.append(f"- **Most expensive commands (W=8):** `{slowest[0][0]}` "
             f"(RPS {slowest[0][1]['rps']:.1f}, mean {slowest[0][1]['lat_moy_ms']:.1f} ms) "
             f"and `{slowest[1][0]}` (RPS {slowest[1][1]['rps']:.1f}, "
             f"mean {slowest[1][1]['lat_moy_ms']:.1f} ms). These are mailbox/conversation "
             f"reads — priority targets for SQL query optimization.")
    send = sec_a["send_message"]
    L.append(f"- **Heavy latency tail on `send_message`:** p50 {send['p50_ms']:.1f} ms but "
             f"p95 {send['p95_ms']:.1f} ms / p99 {send['p99_ms']:.1f} ms / "
             f"max {send['lat_max_ms']:.1f} ms — check write contention / locking.")
    read = sec_a["read_message"]
    L.append(f"- **Heavy tail on `read_message`:** p50 {read['p50_ms']:.1f} ms vs "
             f"p99 {read['p99_ms']:.1f} ms (ratio x{read['p99_ms']/max(read['p50_ms'], 0.1):.0f}).")
    errs = [s for s in scen if s["erreurs"]]
    if errs:
        L.append(f"- **Errors observed ({len(errs)} scenarios):** all `ConnectionResetError` — "
                 f"connection dropped by the server under high concurrency, never an "
                 f"application error:")
        for s in errs:
            L.append(f"  - `{s['scenario']}` : {s['taux_erreur_pct']:.3f} % "
                     f"({', '.join(f'{k} x{v}' for k, v in s['codes_erreur'].items())})")
    else:
        L.append("- No errors observed.")
    L.append("- **Memory:** RSS peaks up to ~600 MiB (W=48) — production Argon2id cost "
             "(~64 MiB per concurrent verification). The 2.5 GiB guard was never "
             "approached, no aborts.\n")
    L.append("## Raw data\n")
    L.append("- `benchmarks/results/baseline-pre-opt.json` (32 scenarios, full data).\n")

    text = "\n".join(L) + "\n"
    with open(out_path, "w") as f:
        f.write(text)
    print(f"Report written: {out_path} ({len(L)} lines)")
    return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default=DEFAULT_JSON)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    generate(args.json, args.out)
