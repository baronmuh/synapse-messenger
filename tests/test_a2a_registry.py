"""F20 — A2A registry MVE (scout_architecture REGISTRY_FEATURE_DESIGN
t_68dcd793 R-F): register cards by URL (idempotent, AgentCard v1.0
validated), public anonymous capability/tag search (discover-before-talk),
the verified bit as evidence-of-working, stale-card eviction and the
capability-minting stub.

The MVE acceptance criteria proven here against a real HTTP bridge:

- recall = 1.0 / precision = 1.0 on the capability search;
- idempotent registration (re-registering a URL refreshes, never
  duplicates);
- anonymous public query (no token on the GET search);
- the verified bit resolves to the fetch evidence (never a trust
  statement — provocateur t_02ceab8b guardrail);
- stale cards are evicted after the heartbeat TTL;
- zero regressions in the existing bridge tests (test_a2a_bridge.py runs
  unchanged in the same suite).
"""

from __future__ import annotations

import contextlib
import copy
import email.message
import email.policy
import http.server
import io
import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from synapse.registry import (
    DEFAULT_TTL_SECONDS,
    Registry,
    RegistryError,
    _SafeRedirectHandler,
    _assert_safe_fetch_url,
    _default_fetch,
    _is_global_ip,
)

from .conftest import ALICE, ALICE_PASSWORD

TOKEN = "jeton-de-test-a2a-123456"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _skill(skill_id: str, tags: list[str] | None = None) -> dict:
    return {"id": skill_id, "name": skill_id, "tags": tags or []}


def _card(name: str, skills: list[dict]) -> dict:
    """A minimal-but-valid AgentCard v1.0."""
    return {
        "name": name,
        "description": f"{name} test agent",
        "version": "1.0",
        "supportedInterfaces": [{
            "url": f"http://example.test/{name}",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        }],
        "capabilities": {"streaming": False, "pushNotifications": False},
        "skills": skills,
    }


VALID_CARD = _card("weather-agent", [
    _skill("weather.forecast", ["meteo"]),
    _skill("weather.alerts", ["meteo", "urgent"]),
])


def _fetch_returning(payload: bytes, *, record: list | None = None):
    """An injected registry fetch returning a fixed payload."""

    def fetch(url: str) -> bytes:
        if record is not None:
            record.append(url)
        return payload

    return fetch


def _clock(start: float = 1000.0):
    """An injectable clock: ``now()`` plus ``advance(delta)``."""
    state = {"t": start}

    def now() -> float:
        return state["t"]

    def advance(delta: float) -> None:
        state["t"] += delta

    return now, advance


@contextlib.contextmanager
def _card_server(card: dict | None = None, *, raw: bytes | None = None,
                 status: int = 200):
    """Serves a fixed AgentCard (or a raw/error response) on loopback.

    Yields the base URL; the card is served at
    ``/.well-known/agent-card.json``.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path != "/.well-known/agent-card.json":
                self.send_response(404)
                self.end_headers()
                return
            if raw is not None:
                body = raw
            elif card is not None:
                body = json.dumps(card).encode("utf-8")
            else:
                body = b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def log_message(self, format: str, *args):  # noqa: A002 - silence test noise
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _make_bridge(fx):
    from synapse.a2a_bridge import A2ABridge

    bridge = A2ABridge(fx.config, ALICE, ALICE_PASSWORD, port=0, token=TOKEN)
    bridge.start()
    assert bridge._server is not None
    return bridge, bridge._server.server_address[1]


def _get(port: int, path: str):
    """Anonymous GET (no token) — the registry search must be public."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    return urllib.request.urlopen(req)


def _post(port: int, path: str, body: dict, token: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Synapse-Token"] = token
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
    )
    return urllib.request.urlopen(req)


# ---------------------------------------------------------------------------
# Registry core (unit)
# ---------------------------------------------------------------------------


def test_register_by_url_fetches_well_known_and_stores():
    fetched: list[str] = []
    reg = Registry(fetch=_fetch_returning(json.dumps(VALID_CARD).encode(),
                                          record=fetched),
                   now=lambda: 42.0)
    entry = reg.register("https://agents.example.org/weather")
    # the well-known path is appended (RFC 8615 convention)
    assert fetched == ["https://agents.example.org/weather/.well-known/agent-card.json"]
    assert entry["url"] == "https://agents.example.org/weather/.well-known/agent-card.json"
    assert entry["name"] == "weather-agent"
    assert entry["description"] == "weather-agent test agent"
    assert entry["capabilities"] == ["weather.alerts", "weather.forecast"]
    assert entry["tags"] == ["meteo", "urgent"]
    assert entry["verified"] is True
    assert entry["registered_at"] == entry["last_seen"] == 42.0


def test_register_card_url_used_as_is_and_trailing_slash():
    reg = Registry(fetch=_fetch_returning(json.dumps(VALID_CARD).encode()))
    entry = reg.register("https://agents.example.org/.well-known/agent-card.json")
    assert entry["url"] == "https://agents.example.org/.well-known/agent-card.json"
    entry = reg.register("https://agents.example.org/weather/")
    assert entry["url"] == "https://agents.example.org/weather/.well-known/agent-card.json"


def test_register_idempotent_refreshes_single_entry():
    now, advance = _clock(100.0)
    reg = Registry(fetch=_fetch_returning(json.dumps(VALID_CARD).encode()),
                   now=now)
    first = reg.register("https://agents.example.org/weather")
    advance(10.0)
    second = reg.register("https://agents.example.org/weather")
    assert second["last_seen"] == 110.0
    assert second["last_seen"] > first["last_seen"]
    assert len(reg.list_all()) == 1


def test_register_invalid_json_and_encoding_rejected():
    for raw in (b"pas du json", b"\xff\xfe\x00"):
        reg = Registry(fetch=_fetch_returning(raw))
        with pytest.raises(RegistryError) as exc:
            reg.register("https://agents.example.org/weather")
        assert exc.value.http_status == 502
        assert "invalid card JSON" in exc.value.message


def test_register_fetch_http_error_502():
    with _card_server(status=404) as base:
        # allow_private: the local card server is on loopback; the fetch
        # error mapping (HTTPError -> 502) is exercised for real.
        reg = Registry(allow_private=True)
        with pytest.raises(RegistryError) as exc:
            reg.register(base)
        assert exc.value.http_status == 502
        assert "HTTP 404" in exc.value.message


def test_register_fetch_unreachable_502():
    reg = Registry(allow_private=True)
    with pytest.raises(RegistryError) as exc:
        reg.register("http://127.0.0.1:1/.well-known/agent-card.json")
    assert exc.value.http_status == 502


@pytest.mark.parametrize("mutation,message", [
    (lambda c: c.pop("name"), "missing 'name'"),
    (lambda c: c.__setitem__("name", "   "), "missing 'name'"),
    (lambda c: c.__setitem__("name", None), "missing 'name'"),
    (lambda c: c.__setitem__("supportedInterfaces", []), "supportedInterfaces"),
    (lambda c: c.__setitem__("skills", "nope"), "'skills' must be a list"),
    (lambda c: c.__setitem__("skills", [{"name": "no id"}]), "no skill 'id'"),
    (lambda c: c.__setitem__("skills", ["not-a-dict"]), "no skill 'id'"),
])
def test_register_invalid_card_rejected(mutation, message):
    card = copy.deepcopy(VALID_CARD)
    mutation(card)
    reg = Registry(fetch=_fetch_returning(json.dumps(card).encode()))
    with pytest.raises(RegistryError) as exc:
        reg.register("https://agents.example.org/weather")
    assert exc.value.http_status == 400
    assert message in exc.value.message


def test_register_payload_rejects_non_object():
    reg = Registry()
    with pytest.raises(RegistryError):
        reg.register_payload(["not", "a", "card"],  # type: ignore[arg-type]
                             url="https://x.example/card.json")


def test_register_payload_unverified_evidence():
    """verified=False records that the card was NOT resolved against a
    live well-known URI — the bit is evidence-of-working, not trust."""
    reg = Registry(now=lambda: 5.0)
    entry = reg.register_payload(VALID_CARD, url="https://local.test/card.json",
                                 verified=False)
    assert entry["verified"] is False
    stored = reg.get("https://local.test/card.json")
    assert stored is not None and stored["verified"] is False


def test_malformed_skills_ignored_gracefully():
    reg = Registry()
    card = _card("odd", [
        {"id": 42},
        {"id": "", "tags": "nope"},
        {"id": "good", "tags": ["t", 7, ""]},
    ])
    entry = reg.register_payload(card, url="https://odd.example/card.json")
    assert entry["capabilities"] == ["good"]
    assert entry["tags"] == ["t"]


def _seeded(reg: Registry) -> Registry:
    reg.register_payload(
        _card("weather-agent", [_skill("weather.forecast", ["meteo"])]),
        url="https://a.example/.well-known/agent-card.json")
    reg.register_payload(
        _card("audit-agent", [_skill("audit.trail", ["compliance"])]),
        url="https://b.example/.well-known/agent-card.json")
    reg.register_payload(
        _card("hybrid-agent", [_skill("weather.forecast", ["compliance"])]),
        url="https://c.example/.well-known/agent-card.json")
    return reg


def test_search_by_capability_case_insensitive():
    reg = _seeded(Registry())
    for needle in ("weather.forecast", "WEATHER.FORECAST"):
        urls = [e["url"] for e in reg.search(capability=needle)]
        assert urls == ["https://a.example/.well-known/agent-card.json",
                        "https://c.example/.well-known/agent-card.json"]


def test_search_by_tag():
    reg = _seeded(Registry())
    urls = [e["url"] for e in reg.search(tag="compliance")]
    assert urls == ["https://b.example/.well-known/agent-card.json",
                    "https://c.example/.well-known/agent-card.json"]
    assert reg.search(tag="compliance".upper()) == reg.search(tag="compliance")


def test_search_capability_and_tag_combined():
    reg = _seeded(Registry())
    urls = [e["url"] for e in reg.search(capability="weather.forecast",
                                         tag="compliance")]
    assert urls == ["https://c.example/.well-known/agent-card.json"]


def test_search_no_filters_returns_all_sorted():
    reg = _seeded(Registry())
    urls = [e["url"] for e in reg.search()]
    assert urls == ["https://a.example/.well-known/agent-card.json",
                    "https://b.example/.well-known/agent-card.json",
                    "https://c.example/.well-known/agent-card.json"]


def test_search_unknown_filter_empty():
    reg = _seeded(Registry())
    assert reg.search(capability="nope") == []
    assert reg.search(tag="nope") == []


def test_search_evicts_stale_before_returning():
    now, advance = _clock(0.0)
    reg = Registry(now=now)
    reg.register_payload(_card("fresh", [_skill("x")]),
                         url="https://fresh.example/card.json")
    reg.register_payload(_card("stale", [_skill("y")]),
                         url="https://stale.example/card.json")
    advance(DEFAULT_TTL_SECONDS + 1)
    reg.heartbeat("https://fresh.example/card.json")
    urls = [e["url"] for e in reg.search()]
    assert urls == ["https://fresh.example/card.json"]


def test_evict_stale_returns_evicted_urls():
    now, advance = _clock(0.0)
    reg = Registry(now=now)
    reg.register_payload(VALID_CARD, url="https://old.example/card.json")
    advance(DEFAULT_TTL_SECONDS + 1)
    assert reg.evict_stale() == ["https://old.example/card.json"]
    assert reg.get("https://old.example/card.json") is None
    # a second pass has nothing left to evict
    assert reg.evict_stale() == []


def test_heartbeat_refreshes_and_unknown_returns_false():
    now, advance = _clock(0.0)
    reg = Registry(now=now)
    reg.register_payload(VALID_CARD, url="https://h.example/card.json")
    assert reg.heartbeat("https://nope.example/card.json") is False
    advance(DEFAULT_TTL_SECONDS + 1)
    assert reg.heartbeat("https://h.example/card.json") is True
    assert reg.get("https://h.example/card.json") is not None


def test_remove_returns_whether_present():
    reg = Registry()
    reg.register_payload(VALID_CARD, url="https://r.example/card.json")
    assert reg.remove("https://r.example/card.json") is True
    assert reg.remove("https://r.example/card.json") is False
    assert reg.list_all() == []


def test_get_unknown_returns_none():
    assert Registry().get("https://nope.example/card.json") is None


def test_list_all_sorted_copies():
    reg = Registry()
    reg.register_payload(VALID_CARD, url="https://z.example/card.json")
    reg.register_payload(_card("a", [_skill("s")]), url="https://a.example/card.json")
    urls = [e["url"] for e in reg.list_all()]
    assert urls == ["https://a.example/card.json", "https://z.example/card.json"]


def test_concurrent_register_search_consistent():
    reg = Registry()
    urls = [f"https://t{i}.example/card.json" for i in range(20)]
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            reg.register_payload(_card(f"agent-{i}", [_skill(f"cap-{i}")]),
                                 url=urls[i])
            reg.search(capability=f"cap-{i}")
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(reg.list_all()) == 20


# ---------------------------------------------------------------------------
# Bridge endpoints (HTTP)
# ---------------------------------------------------------------------------


def test_registry_search_is_public_anonymous(fx):
    """Discover-before-talk: the capability search needs NO token."""
    bridge, port = _make_bridge(fx)
    try:
        bridge.registry.register_payload(
            _card("weather-agent", [_skill("weather.forecast")]),
            url="https://a.example/card.json")
        with _get(port, "/v1/registry/cards") as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert body["count"] == 1
        assert body["cards"][0]["name"] == "weather-agent"
        # capability filter, still anonymous
        with _get(port, "/v1/registry/cards?capability=weather.forecast") as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["count"] == 1
        with _get(port, "/v1/registry/cards?capability=nope") as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["count"] == 0
    finally:
        bridge.stop()


def test_registry_search_capability_and_tag_filters(fx):
    bridge, port = _make_bridge(fx)
    try:
        bridge.registry.register_payload(
            _card("weather", [_skill("weather.forecast", ["meteo"])]),
            url="https://w.example/card.json")
        bridge.registry.register_payload(
            _card("audit", [_skill("audit.trail", ["compliance"])]),
            url="https://a.example/card.json")
        bridge.registry.register_payload(
            _card("hybrid", [_skill("weather.forecast", ["compliance"])]),
            url="https://h.example/card.json")
        with _get(port, "/v1/registry/cards?tag=compliance") as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["count"] == 2
        # capability AND tag — precision: only the exact match survives
        with _get(port, "/v1/registry/cards?capability=weather.forecast&tag=compliance") as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["count"] == 1
        assert body["cards"][0]["url"] == "https://h.example/card.json"
        # case-insensitive capability
        with _get(port, "/v1/registry/cards?capability=WEATHER.FORECAST") as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["count"] == 2
    finally:
        bridge.stop()


def test_registry_search_version_negotiation_applies(fx):
    """Even the public search honours A2A §3.6 version negotiation."""
    bridge, port = _make_bridge(fx)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/registry/cards",
            headers={"A2A-Version": "9.0"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 400
    finally:
        bridge.stop()


def test_registry_search_503_on_internal_failure(fx, monkeypatch):
    bridge, port = _make_bridge(fx)
    try:
        def boom(**kwargs):
            raise RuntimeError("boom")
        monkeypatch.setattr(bridge.registry, "search", boom)
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(port, "/v1/registry/cards")
        assert exc.value.code == 503
    finally:
        bridge.stop()


def test_registry_register_requires_token(fx):
    bridge, port = _make_bridge(fx)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(port, "/v1/registry/cards", {"url": "https://a.example/card.json"})
        assert exc.value.code == 401
    finally:
        bridge.stop()


def test_registry_register_by_url_creates_card(fx):
    bridge, port = _make_bridge(fx)
    # The card server is on loopback: the explicit test/local escape
    # hatch (allow_private) permits it — the default Registry is
    # fail-closed (see the SSRF guard tests below).
    bridge.registry = Registry(allow_private=True)
    try:
        with _card_server(_card("weather-agent", [_skill("weather.forecast", ["meteo"])])) as base:
            with _post(port, "/v1/registry/cards", {"url": base}, token=TOKEN) as resp:
                assert resp.status == 201
                body = json.loads(resp.read().decode("utf-8"))
        assert body["registered"] is True
        card = body["card"]
        # verified = evidence-of-working: fetched live from the well-known URI
        assert card["verified"] is True
        assert card["capabilities"] == ["weather.forecast"]
        assert card["tags"] == ["meteo"]
        # the card is now discoverable anonymously
        with _get(port, "/v1/registry/cards?capability=weather.forecast") as resp:
            found = json.loads(resp.read().decode("utf-8"))
        assert found["count"] == 1
        assert found["cards"][0]["url"] == card["url"]
    finally:
        bridge.stop()


def test_registry_register_idempotent(fx):
    bridge, port = _make_bridge(fx)
    bridge.registry = Registry(allow_private=True)
    try:
        with _card_server(_card("weather-agent", [_skill("weather.forecast")])) as base:
            for _ in range(2):
                with _post(port, "/v1/registry/cards", {"url": base}, token=TOKEN) as resp:
                    assert resp.status == 201
        assert len(bridge.registry.list_all()) == 1
        with _get(port, "/v1/registry/cards") as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["count"] == 1
    finally:
        bridge.stop()


def test_registry_register_missing_url_400(fx):
    bridge, port = _make_bridge(fx)
    try:
        for payload in ({}, {"url": 42}, {"url": "   "}):
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post(port, "/v1/registry/cards", payload, token=TOKEN)
            assert exc.value.code == 400
            body = json.loads(exc.value.read().decode("utf-8"))
            assert body["error"]["code"] == -32602
            assert "url" in body["error"]["message"]
    finally:
        bridge.stop()


def test_registry_register_fetch_error_502(fx):
    bridge, port = _make_bridge(fx)
    bridge.registry = Registry(allow_private=True)
    try:
        with _card_server(status=404) as base:
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post(port, "/v1/registry/cards", {"url": base}, token=TOKEN)
            assert exc.value.code == 502
    finally:
        bridge.stop()


def test_registry_register_invalid_card_502(fx):
    bridge, port = _make_bridge(fx)
    bridge.registry = Registry(allow_private=True)
    try:
        # non-JSON payload at the URL -> 502
        with _card_server(raw=b"pas du json") as base:
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post(port, "/v1/registry/cards", {"url": base}, token=TOKEN)
            assert exc.value.code == 502
        # valid JSON but not a valid AgentCard -> 400 (validation error,
        # the fetch evidence is fine — the payload itself is bad)
        with _card_server(raw=json.dumps({"skills": []}).encode()) as base:
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post(port, "/v1/registry/cards", {"url": base}, token=TOKEN)
            assert exc.value.code == 400
    finally:
        bridge.stop()


def test_registry_register_oversized_card_502(fx):
    """Anti-abuse: a card document larger than the read bound is never
    stored (the truncated read cannot parse, so the fetch evidence fails
    cleanly instead of letting memory balloon)."""
    bridge, port = _make_bridge(fx)
    bridge.registry = Registry(allow_private=True)
    try:
        big = _card("huge", [_skill("x")])
        big["description"] = "x" * (1024 * 1024 * 2)
        with _card_server(card=big) as base:
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post(port, "/v1/registry/cards", {"url": base}, token=TOKEN)
            assert exc.value.code == 502
        assert bridge.registry.list_all() == []
    finally:
        bridge.stop()


def test_registry_register_503_on_internal_failure(fx, monkeypatch):
    bridge, port = _make_bridge(fx)
    try:
        def boom(url):
            raise RuntimeError("boom")
        monkeypatch.setattr(bridge.registry, "register", boom)
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(port, "/v1/registry/cards", {"url": "https://a.example/card.json"},
                  token=TOKEN)
        assert exc.value.code == 503
    finally:
        bridge.stop()


def test_registry_mint_stub(fx):
    bridge, port = _make_bridge(fx)
    try:
        with _post(port, "/v1/registry/capabilities/mint",
                   {"capability": "weather.forecast"}, token=TOKEN) as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode("utf-8"))
        assert body["capability"] == "weather.forecast"
        assert body["minted"] is True
        assert body["stub"] is True
        # the "id" alias is accepted too
        with _post(port, "/v1/registry/capabilities/mint",
                   {"id": "audit.trail"}, token=TOKEN) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert body["capability"] == "audit.trail"
    finally:
        bridge.stop()


def test_registry_mint_requires_token_and_capability(fx):
    bridge, port = _make_bridge(fx)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(port, "/v1/registry/capabilities/mint", {"capability": "x"})
        assert exc.value.code == 401
        for payload in ({}, {"capability": 42}, {"capability": ""}):
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post(port, "/v1/registry/capabilities/mint", payload, token=TOKEN)
            assert exc.value.code == 400
        # a bad body is refused by the shared reader before the stub
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/registry/capabilities/mint", data=b"{oops",
            headers={"X-Synapse-Token": TOKEN}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 400
    finally:
        bridge.stop()


def test_registry_register_bad_bodies(fx):
    """The shared body reader: bad Content-Length, oversized body, invalid
    JSON and non-object bodies are all refused before touching the
    registry — and the JSON-RPC /message path still works after the
    refactor."""
    bridge, port = _make_bridge(fx)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/registry/cards", data=b"{}",
            headers={"X-Synapse-Token": TOKEN, "Content-Length": "abc"},
            method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 400

        big = json.dumps({"url": "https://a.example/" + "x" * (1024 * 1024)}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/registry/cards", data=big,
            headers={"X-Synapse-Token": TOKEN}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 413

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/registry/cards", data=b"{oops",
            headers={"X-Synapse-Token": TOKEN}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 400

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/registry/cards", data=b"[1, 2]",
            headers={"X-Synapse-Token": TOKEN}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 400

        # the /message path still works after the body-reader refactor
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tasks/list",
                           "params": {}}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/message", data=body,
            headers={"Content-Type": "application/json", "X-Synapse-Token": TOKEN})
        with urllib.request.urlopen(req) as resp:
            assert "error" not in json.loads(resp.read().decode("utf-8"))
    finally:
        bridge.stop()


# ---------------------------------------------------------------------------
# MVE acceptance: three cards, recall/precision, idempotency, eviction
# ---------------------------------------------------------------------------


def test_mve_three_cards_recall_precision_and_eviction(fx):
    """The MVE acceptance scenario, end to end over HTTP with REAL
    fetches: Synapse's own card (the bridge's own well-known URI), one
    external well-known card (a remote origin's
    ``/.well-known/agent-card.json``) and one local test card.

    PASS criteria: recall=1.0/precision=1.0, idempotent registration,
    anonymous public query, verified bit resolves to the fetch evidence,
    stale-card eviction after the heartbeat TTL.
    """
    now, advance = _clock(1000.0)
    fx.client.set_agent_card(["comptabilite", "audit"], ALICE, ALICE_PASSWORD)
    bridge, port = _make_bridge(fx)
    # real fetch, deterministic clock; loopback card servers need the
    # explicit test/local escape hatch (allow_private)
    bridge.registry = Registry(now=now, allow_private=True)
    try:
        own_url = f"http://127.0.0.1:{port}/.well-known/agent-card.json"
        with _card_server(_card("acme-weather", [
                _skill("weather.forecast", ["meteo"])])) as external_base, \
             _card_server(_card("local-test", [
                _skill("translate", ["i18n"]),
                _skill("weather.forecast", ["dev"])])) as local_base:
            # register Synapse's own card through the public API (the
            # registry fetches it from the bridge's own well-known URI)
            with _post(port, "/v1/registry/cards", {"url": own_url},
                       token=TOKEN) as resp:
                own = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 201
            # one external well-known card
            with _post(port, "/v1/registry/cards", {"url": external_base},
                       token=TOKEN) as resp:
                ext = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 201
            # one local test card
            with _post(port, "/v1/registry/cards", {"url": local_base},
                       token=TOKEN) as resp:
                loc = json.loads(resp.read().decode("utf-8"))
            assert resp.status == 201

            # idempotent: re-registering every URL keeps the registry at 3
            for url in (own_url, external_base, local_base):
                with _post(port, "/v1/registry/cards", {"url": url},
                           token=TOKEN) as resp:
                    assert resp.status == 201
            assert len(bridge.registry.list_all()) == 3

            # anonymous public query (no token on GET)
            with _get(port, "/v1/registry/cards") as resp:
                body = json.loads(resp.read().decode("utf-8"))
            assert body["count"] == 3
            assert {c["name"] for c in body["cards"]} == {
                ALICE, "acme-weather", "local-test"}

            # recall = 1.0: every owner of the capability is returned
            with _get(port, "/v1/registry/cards?capability=weather.forecast") as resp:
                body = json.loads(resp.read().decode("utf-8"))
            assert body["count"] == 2
            assert {c["url"] for c in body["cards"]} == {
                ext["card"]["url"], loc["card"]["url"]}

            # precision = 1.0: the capability+tag query returns exactly the
            # matching card, nothing else
            with _get(port,
                      "/v1/registry/cards?capability=weather.forecast&tag=meteo") as resp:
                body = json.loads(resp.read().decode("utf-8"))
            assert body["count"] == 1
            assert body["cards"][0]["url"] == ext["card"]["url"]

            # each capability resolves to its owner (recall per query)
            for capability, expected in {
                "comptabilite": {own["card"]["url"]},
                "audit": {own["card"]["url"]},
                "translate": {loc["card"]["url"]},
            }.items():
                with _get(port, f"/v1/registry/cards?capability={capability}") as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                assert {c["url"] for c in body["cards"]} == expected

            # verified bit = evidence-of-working: every card was fetched
            # from a live well-known URI and validated
            for entry in bridge.registry.list_all():
                assert entry["verified"] is True

            # stale-card eviction: advance past the heartbeat TTL, refresh
            # only the own card, then the search must drop the other two
            advance(DEFAULT_TTL_SECONDS + 1)
            assert bridge.registry.heartbeat(own_url) is True
            with _get(port, "/v1/registry/cards?capability=weather.forecast") as resp:
                body = json.loads(resp.read().decode("utf-8"))
            assert body["count"] == 0
            with _get(port, "/v1/registry/cards") as resp:
                body = json.loads(resp.read().decode("utf-8"))
            assert body["count"] == 1
            assert body["cards"][0]["url"] == own_url
    finally:
        bridge.stop()


# ---------------------------------------------------------------------------
# SSRF guard (auditor N1): registry fetch fails closed on non-http(s)
# schemes and private/loopback/link-local targets.
# ---------------------------------------------------------------------------


def test_fetch_refuses_non_http_schemes():
    """file:// (the audit-proven escape), ftp, gopher, data and
    scheme-less URLs are refused BEFORE any connection."""
    for url in ("file:///etc/hostname", "ftp://example.com/x",
                "gopher://example.com/1", "data:text/plain,hi",
                "//no-scheme/x", "http:no-host"):
        with pytest.raises(RegistryError) as exc:
            _default_fetch(url)
        assert exc.value.http_status == 400
        assert "scheme" in exc.value.message or "host" in exc.value.message


def test_fetch_refuses_private_loopback_and_link_local_targets():
    """The classic SSRF probes: loopback, RFC1918, cloud metadata
    (169.254.169.254), CGNAT, unspecified and IPv6 private/link-local
    literals are all refused fail-closed."""
    for url in (
        "http://127.0.0.1:1/x", "http://127.1.2.3/x", "http://10.0.0.1/x",
        "http://172.16.0.1/x", "http://192.168.1.1/x",
        "http://169.254.169.254/latest/meta-data/", "http://0.0.0.0/x",
        "http://100.64.0.1/x", "http://[::1]/x", "http://[fd00::1]/x",
        "http://[fe80::1]/x", "http://[2001:db8::1]/x",
    ):
        with pytest.raises(RegistryError) as exc:
            _default_fetch(url)
        assert exc.value.http_status == 400
        assert "private" in exc.value.message


def test_fetch_refuses_localhost_and_private_resolutions(monkeypatch):
    """localhost (by name), DNS names resolving (any address) to a
    private target, and unresolvable names all fail closed."""
    for url in ("http://localhost:1/x", "http://LOCALHOST/x"):
        with pytest.raises(RegistryError):
            _default_fetch(url)

    def rebinding(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ]
    monkeypatch.setattr(socket, "getaddrinfo", rebinding)
    with pytest.raises(RegistryError) as exc:
        _default_fetch("http://rebind.example/card.json")
    assert "private" in exc.value.message

    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            socket.gaierror(-2, "Name or service not known")))
    with pytest.raises(RegistryError):
        _default_fetch("http://unresolvable.invalid/card.json")


def test_guard_accepts_public_targets(monkeypatch):
    """Public literals pass without resolution; a DNS name passes only
    when EVERY resolved address is public."""
    _assert_safe_fetch_url(
        "http://93.184.216.34/.well-known/agent-card.json",
        allow_private=False)
    _assert_safe_fetch_url("https://[2606:4700::1111]/x", allow_private=False)

    def public_only(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::1111", 0)),
        ]
    monkeypatch.setattr(socket, "getaddrinfo", public_only)
    _assert_safe_fetch_url("https://agents.example.org/card.json",
                           allow_private=False)


def test_guard_empty_resolution_fails_closed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
    with pytest.raises(RegistryError):
        _default_fetch("http://empty.example/card.json")


def test_global_ip_literal_classification():
    assert _is_global_ip("93.184.216.34") is True
    assert _is_global_ip("2606:4700::1111") is True
    assert _is_global_ip("127.0.0.1") is False
    assert _is_global_ip("169.254.169.254") is False
    assert _is_global_ip("10.1.2.3") is False
    assert _is_global_ip("192.168.0.1") is False
    assert _is_global_ip("100.64.0.1") is False
    assert _is_global_ip("::1") is False
    assert _is_global_ip("fe80::1") is False
    assert _is_global_ip("2001:db8::1") is False
    # IPv4-mapped IPv6 addresses are classified by the mapped IPv4
    assert _is_global_ip("::ffff:127.0.0.1") is False
    assert _is_global_ip("::ffff:93.184.216.34") is True
    # not an IP literal at all
    assert _is_global_ip("agents.example.org") is False


def test_redirect_handler_revalidates_every_hop():
    """A public URL redirecting to a private target or a non-http(s)
    scheme is refused at the hop; public hops still work."""
    handler = _SafeRedirectHandler()
    req = urllib.request.Request(
        "http://93.184.216.34/.well-known/agent-card.json")
    fp = io.BytesIO(b"")
    headers = email.message.Message(policy=email.policy.HTTP)
    for target in ("file:///etc/hostname", "http://169.254.169.254/latest/meta-data/",
                   "http://127.0.0.1:1/x", "ftp://example.com/x"):
        with pytest.raises(RegistryError):
            handler.redirect_request(req, fp, 302, "Found", headers, target)
    new = handler.redirect_request(
        req, fp, 302, "Found", headers, "http://93.184.216.34/moved")
    assert new is not None and new.full_url == "http://93.184.216.34/moved"


def test_allow_private_hatch_still_blocks_bad_schemes():
    """The test/local escape hatch relaxes the address checks but NEVER
    the scheme check: file:// stays refused."""
    with pytest.raises(RegistryError) as exc:
        _default_fetch("file:///etc/hostname", allow_private=True)
    assert exc.value.http_status == 400
    assert "scheme" in exc.value.message


def test_registry_allow_private_fetches_loopback_default_refuses():
    """Default Registry: loopback is refused fail-closed; the explicit
    allow_private Registry can register from a local card server (the
    escape hatch the loopback tests use)."""
    with _card_server(_card("local-agent", [_skill("local.cap")])) as base:
        with pytest.raises(RegistryError) as exc:
            Registry().register(base)
        assert exc.value.http_status == 400

        reg = Registry(allow_private=True)
        entry = reg.register(base)
        assert entry["verified"] is True
        assert entry["capabilities"] == ["local.cap"]


def test_registry_register_ssrf_probes_rejected_over_bridge(fx):
    """The fail-closed guard is reachable through the public API: the
    bridge registry is a default (fail-closed) Registry."""
    bridge, port = _make_bridge(fx)
    try:
        for bad in ("file:///etc/hostname", "http://127.0.0.1:9/x",
                    "http://169.254.169.254/latest/meta-data/",
                    "ftp://example.com/x"):
            with pytest.raises(urllib.error.HTTPError) as exc:
                _post(port, "/v1/registry/cards", {"url": bad}, token=TOKEN)
            assert exc.value.code == 400
            body = json.loads(exc.value.read().decode("utf-8"))
            assert body["error"]["code"] == -32602
            assert "refuses" in body["error"]["message"]
        assert bridge.registry.list_all() == []
    finally:
        bridge.stop()
