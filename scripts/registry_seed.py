#!/usr/bin/env python3
"""A2A registry MVE demo (scout_architecture REGISTRY_FEATURE_DESIGN
t_68dcd793 R-F).

Registers three cards on a live A2A bridge and runs the MVE acceptance
queries end to end over HTTP:

  1. Synapse's own card   — the bridge's own ``/.well-known/agent-card.json``
  2. one external card    — a remote origin's well-known URI (network
                            dependent; when unreachable the demo falls back
                            to a bundled sample card registered with
                            ``verified=False`` — the verified bit honestly
                            reflects the evidence)
  3. one local test card  — a tiny card server started by this script

It prints the PASS evidence: recall=1.0 / precision=1.0, idempotent
registration, anonymous public query, resolved verified bit and stale-card
eviction (the latter with ``--ttl-demo``).

Usage:
    echo "$AGENT_PASSWORD" | python scripts/registry_seed.py \
        --config /path/to/synapse-config.json --agent-name alice \
        [--port 8090] [--token my-bridge-token] [--ttl-demo 1]
        [--set-card "cap1,cap2"]

The agent password is ALWAYS read from stdin (never argv, never env). The
bridge token is a local demo secret (not an org credential); when omitted
a random one is generated and printed.
"""

from __future__ import annotations

import argparse
import contextlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse.a2a_bridge import A2ABridge  # noqa: E402

# A realistic external AgentCard v1.0 used when no live external well-known
# origin is reachable (the design flags external reachability as
# environment dependent). Registered with verified=False in that case.
_SAMPLE_EXTERNAL_CARD = {
    "name": "acme-weather",
    "description": "Weather forecast and alert agent (sample card)",
    "version": "1.0",
    "supportedInterfaces": [{
        "url": "https://weather.example.org/",
        "protocolBinding": "JSONRPC",
        "protocolVersion": "1.0",
    }],
    "capabilities": {"streaming": False, "pushNotifications": False},
    "skills": [
        {"id": "weather.forecast", "name": "weather.forecast",
         "description": "Get a forecast", "tags": ["meteo"]},
        {"id": "weather.alerts", "name": "weather.alerts",
         "description": "Severe weather alerts", "tags": ["meteo", "urgent"]},
    ],
}

_LOCAL_TEST_CARD = {
    "name": "local-test",
    "description": "Local test agent for the registry demo",
    "version": "1.0",
    "supportedInterfaces": [{
        "url": "http://127.0.0.1:0/",
        "protocolBinding": "JSONRPC",
        "protocolVersion": "1.0",
    }],
    "capabilities": {"streaming": False, "pushNotifications": False},
    "skills": [
        {"id": "translate", "name": "translate",
         "description": "Translate text", "tags": ["i18n"]},
        {"id": "weather.forecast", "name": "weather.forecast",
         "description": "Local forecast capability", "tags": ["dev"]},
    ],
}


@contextlib.contextmanager
def _card_server(card: dict):
    """Serves a fixed AgentCard at /.well-known/agent-card.json on loopback."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path != "/.well-known/agent-card.json":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(card).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args):  # noqa: A002
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _http_post(port: int, path: str, body: dict, token: str) -> tuple[int, dict]:
    """POSTs JSON to the bridge; returns (status, parsed body)."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Synapse-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"error": {"message": raw[:200]}}


def _http_get(port: int, path: str) -> tuple[int, dict]:
    """Anonymous GET (no token) — the search must be public."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"error": {"message": raw[:200]}}


def _print_evidence(port: int, label: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Synapse JSON configuration file")
    parser.add_argument("--agent-name", required=True, help="agent exposed by the bridge")
    parser.add_argument("--port", type=int, default=0, help="bridge port (0 = pick a free one)")
    parser.add_argument("--token", default=None, help="bridge access token (generated if omitted)")
    parser.add_argument("--set-card", default=None,
                        help='comma-separated capabilities to set on the agent card '
                             '(ensures the own card has searchable skills)')
    parser.add_argument("--ttl-demo", type=float, default=0.0,
                        help="seconds; when >0, demo stale-card eviction with this TTL")
    args = parser.parse_args()

    print("== A2A registry MVE demo (scripts/registry_seed.py) ==")
    print(f"config: {args.config}  agent: {args.agent_name}")

    # The agent password always comes from stdin — never argv, never env.
    password = sys.stdin.readline().rstrip("\n")
    if not password:
        print("error: agent password expected on stdin", file=sys.stderr)
        return 2

    import json as _json

    from synapse.config import Config
    from synapse.client import Client

    with open(args.config, encoding="utf-8") as fh:
        cfg = Config.from_dict(_json.load(fh))

    # Optional: declare the agent's own capabilities so the own card has
    # searchable skills (set_agent_card is the agent's own card).
    if args.set_card:
        caps = [c.strip() for c in args.set_card.split(",") if c.strip()]
        client = Client(cfg.socket_path)
        client.set_agent_card(caps, args.agent_name, password)
        print(f"agent card capabilities set: {caps}")

    token = args.token or secrets.token_hex(16)
    print(f"bridge token: {token}")

    bridge = A2ABridge(cfg, args.agent_name, password, port=args.port, token=token)
    bridge.start()
    try:
        port = bridge._server.server_address[1]  # type: ignore[union-attr]
        print(f"bridge up: http://127.0.0.1:{port} (agent {args.agent_name})")

        # -- 1. Synapse's own card ---------------------------------------
        own_url = f"http://127.0.0.1:{port}/.well-known/agent-card.json"
        print("\n[1] register Synapse's own card (bridge well-known URI)")
        status, body = _http_post(port, "/v1/registry/cards", {"url": own_url}, token)
        if status == 201:
            _print_evidence(port, f"own card registered (HTTP {status})", True,
                            f"name={body['card']['name']} "
                            f"capabilities={body['card']['capabilities']} "
                            f"verified={body['card']['verified']}")
        else:
            _print_evidence(port, f"own card registration (HTTP {status})", False,
                            str(body.get("error", body))[:200])
            return 1

        # -- 2. one external well-known card -----------------------------
        external_url = "https://a2a-protocol.org/.well-known/agent-card.json"
        print(f"\n[2] register external well-known card ({external_url})")
        external_fell_back = False
        status, body = _http_post(port, "/v1/registry/cards", {"url": external_url}, token)
        if status == 201:
            _print_evidence(port, f"external card registered (HTTP {status})", True,
                            f"name={body['card']['name']} verified={body['card']['verified']}")
        else:
            # Network-dependent (design caveat): fall back to the bundled
            # sample card with verified=False — the verified bit honestly
            # reflects the missing evidence.
            external_fell_back = True
            print(f"  (external origin unreachable: HTTP {status} "
                  f"{body.get('error', {}).get('message', '')})")
            entry = bridge.registry.register_payload(
                _SAMPLE_EXTERNAL_CARD,
                url=external_url, verified=False)
            _print_evidence(port, "external card registered (bundled sample)", True,
                            f"name={entry['name']} verified={entry['verified']} "
                            "(origin unreachable: verified=False is the honest bit)")

        # -- 3. one local test card --------------------------------------
        print("\n[3] register local test card")
        with _card_server(_LOCAL_TEST_CARD) as local_base:
            status, body = _http_post(port, "/v1/registry/cards", {"url": local_base}, token)
            if status == 201:
                _print_evidence(port, f"local test card registered (HTTP {status})", True,
                                f"name={body['card']['name']} "
                                f"capabilities={body['card']['capabilities']} "
                                f"verified={body['card']['verified']}")
            else:
                _print_evidence(port, f"local card registration (HTTP {status})", False,
                                str(body.get("error", body))[:200])
                return 1

            # -- idempotent registration --------------------------------
            print("\n[4] idempotent registration (re-register every URL)")
            for label, url in (("own", own_url), ("local", local_base)):
                status, _ = _http_post(port, "/v1/registry/cards", {"url": url}, token)
                _print_evidence(port, f"re-register {label} (HTTP {status})", status == 201)
            if external_fell_back:
                # The external origin is unreachable, so HTTP re-registration
                # cannot succeed; refresh the entry directly (idempotent
                # upsert at the registry level) and say why.
                bridge.registry.register_payload(_SAMPLE_EXTERNAL_CARD,
                                                 url=external_url, verified=False)
                _print_evidence(port, "re-register external (direct refresh)", True,
                                "origin unreachable: HTTP re-register would 502")
            else:
                status, _ = _http_post(port, "/v1/registry/cards",
                                       {"url": external_url}, token)
                _print_evidence(port, f"re-register external (HTTP {status})", status == 201)
            _, all_cards = _http_get(port, "/v1/registry/cards")
            _print_evidence(port, "registry still holds exactly 3 cards",
                            all_cards.get("count") == 3,
                            f"count={all_cards.get('count')}")

            # -- anonymous public query ---------------------------------
            print("\n[5] anonymous public query (no token)")
            _, by_cap = _http_get(port, "/v1/registry/cards?capability=weather.forecast")
            expected = {_SAMPLE_EXTERNAL_CARD["name"], _LOCAL_TEST_CARD["name"]}
            names = {c["name"] for c in by_cap.get("cards", [])}
            # recall = 1.0: every owner of the capability is returned
            recall_ok = by_cap.get("count") == 2 and names == expected
            _print_evidence(port, "recall=1.0 for weather.forecast (both owners found)",
                            recall_ok, f"count={by_cap.get('count')} owners={sorted(names)}")
            # precision = 1.0: capability+tag returns exactly the match
            _, by_tag = _http_get(port,
                                  "/v1/registry/cards?capability=weather.forecast&tag=meteo")
            precision_ok = (by_tag.get("count") == 1
                            and by_tag["cards"][0]["name"] == _SAMPLE_EXTERNAL_CARD["name"])
            _print_evidence(port, "precision=1.0 for weather.forecast&tag=meteo",
                            precision_ok,
                            f"count={by_tag.get('count')} "
                            f"names={[c['name'] for c in by_tag.get('cards', [])]}")
            _, own_search = _http_get(port, "/v1/registry/cards?capability=translate")
            _print_evidence(port, "local capability resolves to the local card",
                            own_search.get("count") == 1
                            and own_search["cards"][0]["name"] == _LOCAL_TEST_CARD["name"],
                            f"count={own_search.get('count')}")

            # -- verified bit -------------------------------------------
            print("\n[6] verified bit (evidence-of-working)")
            _, all_cards = _http_get(port, "/v1/registry/cards")
            verified = {c["name"]: c["verified"] for c in all_cards.get("cards", [])}
            _print_evidence(port, "verified resolves to the fetch evidence",
                            verified.get(_LOCAL_TEST_CARD["name"]) is True
                            and verified.get("acme-weather") is False,
                            f"verified={verified}")

            # -- stale-card eviction ------------------------------------
            print("\n[7] stale-card eviction")
            if args.ttl_demo > 0:
                # The demo owns the in-process bridge; shrink the heartbeat
                # TTL so eviction is observable in seconds instead of hours.
                bridge.registry._ttl = args.ttl_demo  # noqa: SLF001
                time.sleep(args.ttl_demo + 0.5)
                # every card is now stale; refresh only the own card, then
                # search immediately — it must survive, the others must not
                bridge.registry.heartbeat(own_url)
                _, after = _http_get(port, "/v1/registry/cards")
                names_after = [c["name"] for c in after.get("cards", [])]
                ok = after.get("count") == 1 and names_after == [args.agent_name]
                _print_evidence(port, "stale cards evicted, heartbeated own card kept",
                                ok, f"remaining={names_after}")
            else:
                print("  (skipped: pass --ttl-demo <seconds> to demo eviction)")

        print("\n== MVE demo complete ==")
        return 0
    finally:
        bridge.stop()


if __name__ == "__main__":
    sys.exit(main())
