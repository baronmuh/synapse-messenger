#!/usr/bin/env python3
"""Standalone performance gate for Synapse.

Runs a quick benchmark against a running server and emits a gate verdict.
Can be used without systemd — just a server started with `synapse server start`.

Usage:
    python scripts/run_perf_gate.py --socket /path/to/synapse.sock --duration 10

Exit codes: 0 = pass, 1 = fail (regression above threshold).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse.client import Client


async def scenario(client: Client, me: str, pw: str, duration_s: int) -> dict:
    """Measure send_message + get_messages throughput for duration_s seconds."""
    sent = 0
    errors = 0
    latencies: list[float] = []
    deadline = time.monotonic() + duration_s
    counter = 0

    while time.monotonic() < deadline:
        counter += 1
        start = time.monotonic()
        try:
            client.send_message(
                "bob", f"perf-msg-{counter}", f"perf-{counter}", me, pw
            )
            sent += 1
        except Exception:
            errors += 1
        latencies.append((time.monotonic() - start) * 1000)

    latencies.sort()
    n = len(latencies)
    return {
        "sent": sent,
        "errors": errors,
        "duration_s": duration_s,
        "rps": round(sent / duration_s, 1),
        "p50_ms": round(latencies[n // 2], 2) if n else 0,
        "p95_ms": round(latencies[int(n * 0.95)], 2) if n else 0,
        "p99_ms": round(latencies[int(n * 0.99)], 2) if n else 0,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Synapse perf gate (standalone)")
    parser.add_argument("--socket", required=True, help="Path to synapse socket")
    parser.add_argument("--duration", type=int, default=10, help="Duration per scenario (s)")
    parser.add_argument("--min-rps", type=float, default=50.0, help="Minimum RPS to pass")
    parser.add_argument("--org", default="perf_org", help="Organization name for test")
    args = parser.parse_args()

    client = Client(args.socket)

    # Use web token for local auth (no password needed)
    run_dir = os.path.join(os.path.dirname(args.socket))
    web_token_path = os.path.join(run_dir, "web_token")
    web_token = None
    if os.path.exists(web_token_path):
        try:
            web_token = open(web_token_path).read().strip()
        except OSError:
            pass

    # Setup: ensure org + agents exist
    org_password = "perf-pw-123456"
    if web_token:
        # Use web token for local org creation
        try:
            from synapse.web import _WEB_LOCAL
            client.create_org(args.org, org_password, _WEB_LOCAL, web_token)
        except Exception:
            pass  # already exists
    else:
        # Try to create org with dummy auth
        try:
            client.create_org(args.org, org_password, "admin", "admin-pw")
        except Exception:
            pass  # already exists

    try:
        client.create_agent("alice", "alice-pw-123", "Perf agent alice", args.org, org_password)
    except Exception:
        pass  # already exist
    try:
        client.create_agent("bob", "bob-pw-123", "Perf agent bob", args.org, org_password)
    except Exception:
        pass  # already exist

    print(f"Running perf gate: duration={args.duration}s, min_rps={args.min_rps}")
    result = await scenario(client, "alice", "alice-pw-123", args.duration)

    print(json.dumps(result, indent=2))

    if result["rps"] < args.min_rps:
        print(f"FAIL: RPS {result['rps']} < {args.min_rps}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
