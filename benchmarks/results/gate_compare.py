#!/usr/bin/env python3
"""Perf gate comparator: current-run results vs pre-optimization baseline.

Same methodology as t_af15796f (bench.py --duration 10 --passes 2, full mode,
production Argon2id). Compares scenario-by-scenario RPS, mean, p50, p95, p99
and error rate. Verdict rule (gate contract): any hot-path regression > 10%
on RPS (down) or p50/p95/p99 (up) is flagged; the gate verdict is FAIL if any
hot-path scenario regresses >10%, else PASS.

Usage: python benchmarks/results/gate_compare.py <baseline.json> <current.json>
"""
import json
import sys


def pct_delta(new, old):
    if old in (0, None):
        return None
    return (new - old) / old * 100.0


def load(path):
    with open(path) as f:
        return json.load(f)


def main():
    base_path, cur_path = sys.argv[1], sys.argv[2]
    base = load(base_path)
    cur = load(cur_path)
    base_scen = {s["scenario"]: s for s in base["scenarios"]}
    cur_scen = {s["scenario"]: s for s in cur["scenarios"]}

    print(f"baseline: {base['environnement'].get('commit')} | "
          f"current:  {cur['environnement'].get('commit')}")
    print(f"scenarios: baseline={len(base_scen)} current={len(cur_scen)}")
    print()
    hdr = (f"{'scenario':<32} {'RPS Δ%':>8} {'mean Δ%':>8} {'p50 Δ%':>8} "
           f"{'p95 Δ%':>8} {'p99 Δ%':>8}  verdict")
    print(hdr)
    print("-" * len(hdr))

    rows = []
    for name in sorted(base_scen):
        b, c = base_scen[name], cur_scen.get(name)
        if c is None:
            print(f"{name:<32} MISSING in current run")
            continue
        d = {
            "scenario": name,
            "rps": pct_delta(c["rps"], b["rps"]),
            "mean": pct_delta(c["lat_moy_ms"], b["lat_moy_ms"]),
            "p50": pct_delta(c["p50_ms"], b["p50_ms"]),
            "p95": pct_delta(c["p95_ms"], b["p95_ms"]),
            "p99": pct_delta(c["p99_ms"], b["p99_ms"]),
            "err_base": b["taux_erreur_pct"],
            "err_cur": c["taux_erreur_pct"],
            "rps_base": b["rps"], "rps_cur": c["rps"],
            "p50_base": b["p50_ms"], "p50_cur": c["p50_ms"],
            "p95_base": b["p95_ms"], "p95_cur": c["p95_ms"],
            "p99_base": b["p99_ms"], "p99_cur": c["p99_ms"],
            "mean_base": b["lat_moy_ms"], "mean_cur": c["lat_moy_ms"],
        }
        rows.append(d)
        fmt = lambda v: f"{v:>8.1f}" if v is not None else "     n/a"
        flag = ""
        # hot-path regression rule: RPS down >10% OR latency up >10%
        if (d["rps"] is not None and d["rps"] < -10) or (
            d["p50"] is not None and d["p50"] > 10
        ) or (d["p95"] is not None and d["p95"] > 10):
            flag = "  <<< REGRESSION"
        print(f"{name:<32} {fmt(d['rps'])} {fmt(d['mean'])} {fmt(d['p50'])} "
              f"{fmt(d['p95'])} {fmt(d['p99'])}{flag}")

    print()
    fails = [r for r in rows if (r["rps"] is not None and r["rps"] < -10)
             or (r["p50"] is not None and r["p50"] > 10)
             or (r["p95"] is not None and r["p95"] > 10)]
    if fails:
        print(f"VERDICT: FAIL — {len(fails)} scenario(s) regressed >10% "
              f"(hot-path rule).")
        for r in fails:
            print(f"  - {r['scenario']}: RPS {r['rps_base']} -> {r['rps_cur']} "
                  f"({r['rps']:+.1f}%), p50 {r['p50_base']} -> {r['p50_cur']} "
                  f"({r['p50']:+.1f}%), p95 {r['p95_base']} -> {r['p95_cur']} "
                  f"({r['p95']:+.1f}%)")
        sys.exit(1)
    print("VERDICT: PASS — no scenario regressed >10% on RPS or p50/p95.")
    sys.exit(0)


if __name__ == "__main__":
    main()
