"""MVE-2 — the acceptance experiment (DESIGN_CAUSAL_TIME_HLC_v2 §8.2):
two real Synapse instances, agent B's instance with a +30000 ms clock
skew, exchanging tasks through the REAL a2a bridge transport (H4/H7).

Setup:
    instance1 : own SQLite DB, agent A (alice),      skew 0
    instance2 : own SQLite DB, agent B (bob),        skew +30000 ms
                (config.clock_skew_ms consumed by the HLC physical
                provider — the honest seam: skew enters where NTP/clock
                error enters)
    bridge2   : the real A2ABridge of instance2, sharing instance2's
                service clock (the MVE injects it, like the design's
                injectable physical provider)

Scenario (the design's four steps):
    1. A delegates a task-create to B through bridge2 (A2AClient ->
       real JSON-RPC over HTTP). bridge2 observes A's envelope hlc (the
       merge rule) BEFORE creating the task; instance2 stamps create.
    2. B completes the task (real service write on instance2).
    3. A polls tasks/get; bridge2 replies with its envelope hlc; A's
       client absorbs it (instance1's clock jumps 30 s forward — the
       merge rule through the real transport).
    4. A performs a local write; its event is stamped (l_B, c3) while
       its wall at stays real.

PASS criteria (all asserted):
    P1  hlc order correct:  create < complete < read
    P2  wall order WRONG:   at(complete) > at(read)  (+30 s skew)
    P3  causal query true with verifiable chain (ref_id edge + hlc)
    P4  monotone: no hlc.l regression; instance1 absorbed the +30 s

Also asserted here: the bridge boundary rejects a malformed envelope
hlc (H5) without affecting the clock.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from .conftest import (
    ALICE,
    ALICE_PASSWORD,
    BOB,
    BOB_PASSWORD,
    ORG_NAME,
    ORG_PASSWORD,
    make_server,
)

TOKEN = "jeton-mve-causal-123456"
SKEW_MS = 30000


class TwoInstances:
    """Two live Synapse servers (own DBs), A on instance1, B on
    instance2 (clock +30 s), bridge2 exposing B through the real A2A
    transport."""

    def __init__(self, tmp_path) -> None:
        from synapse.config import Config

        self.conf1 = Config.from_dict({
            "storage_dir": str(tmp_path / "i1" / "data"),
            "socket_path": str(tmp_path / "i1" / "run" / "synapse.sock"),
            "log_dir": str(tmp_path / "i1" / "logs"),
            "backup_dir": str(tmp_path / "i1" / "backups"),
        })
        self.conf2 = Config.from_dict({
            "storage_dir": str(tmp_path / "i2" / "data"),
            "socket_path": str(tmp_path / "i2" / "run" / "synapse.sock"),
            "log_dir": str(tmp_path / "i2" / "logs"),
            "backup_dir": str(tmp_path / "i2" / "backups"),
            "clock_skew_ms": SKEW_MS,  # agent B's clock is +30 s (MVE seam)
        })
        self.srv1 = make_server(self.conf1)
        self.srv2 = make_server(self.conf2)
        try:
            self.srv1.create_agent(ALICE, ALICE_PASSWORD, "Agent A (instance 1)")
            self.srv2.create_agent(BOB, BOB_PASSWORD, "Agent B (instance 2)")
            # the service clocks exist after the setup writes
            svc1 = self.srv1.server.service
            svc2 = self.srv2.server.service
            if svc1._clock is None:  # pragma: no cover - setup already stamps
                svc1._stamp()
            if svc2._clock is None:  # pragma: no cover - setup already stamps
                svc2._stamp()
            self.clock1 = svc1._clock
            self.clock2 = svc2._clock
            assert self.clock1 is not None and self.clock2 is not None
        except BaseException:
            self.stop()
            raise

    def stop(self) -> None:
        self.srv1.stop()
        self.srv2.stop()


@pytest.fixture()
def two(tmp_path):
    inst = TwoInstances(tmp_path)
    yield inst
    inst.stop()


def _start_bridge2(two):
    """Starts the real bridge of instance2, sharing its service clock."""
    from synapse.a2a_bridge import A2ABridge

    bridge = A2ABridge(two.conf2, BOB, BOB_PASSWORD, port=0, token=TOKEN,
                       clock=two.clock2)
    bridge.start()
    assert bridge._server is not None
    return bridge, bridge._server.server_address[1]


def _rows(conf, table: str, where: str, *args) -> list:
    from synapse.db import connect

    with connect(conf) as conn:
        return conn.execute(
            f"SELECT * FROM {table} WHERE {where} ORDER BY "
            f"{'seq' if table == 'events' else 'id'}", args
        ).fetchall()


def test_mve_causal_order_with_skewed_clock(two, monkeypatch):
    """The full MVE-2 scenario (P1-P4)."""
    from synapse.client import Client
    from synapse.hlc import decode

    bridge2, port2 = _start_bridge2(two)
    try:
        # agent A talks to bridge2 through the REAL transport (JSON-RPC
        # over HTTP); its envelope hlc is server-stamped by instance1's
        # clock; responses are absorbed into the same clock.
        from synapse.a2a_client import A2AClient

        client_a = A2AClient(
            f"http://127.0.0.1:{port2}", TOKEN,
            hlc_provider=two.clock1.stamp,   # outbound envelope (server-stamped)
            hlc_observer=two.clock1.observe,  # merge rule on every response
        )
        client2 = Client(two.conf2.socket_path)

        # --- the exchange -----------------------------------------------
        # instance2's wall clock is +30 s while its writes happen (the
        # design's "the wall lies": skew enters the time source, exactly
        # like the HLC physical seam). The exchange is sequential, so no
        # instance1 write happens while the patch is active.
        import synapse.service as service_mod
        from synapse.validation import now_utc_offset

        monkeypatch.setattr(service_mod, "now_utc",
                            lambda: now_utc_offset(-SKEW_MS // 1000))

        # 1. A sends task-create to B (bridge2): the merge rule observes
        #    A's envelope hlc before instance2 stamps the create event.
        created = client_a.delegate("causal task from A", assignee=BOB)
        task_id = created["taskId"]
        assert created["hlc"]  # envelope hlc on the reply

        # 2. B completes the task (real write on instance2)
        client2.update_task_state(task_id, "in_progress", BOB, BOB_PASSWORD)
        client2.update_task_state(task_id, "completed", BOB, BOB_PASSWORD,
                                  result="done")

        # 3. A polls tasks/get: bridge2 replies with its envelope hlc;
        #    client_a absorbs it -> instance1's clock jumps 30 s forward
        got = client_a.get(task_id)
        assert got["hlc"]

        monkeypatch.undo()  # instance1's writes now use the real clock

        # 4. A performs a local write: the read event
        read_task = Client(two.conf1.socket_path).create_task(
            "A's own task", ALICE, ALICE, ALICE_PASSWORD,
        )
        assert read_task["task_id"]

        # --- assertions ---------------------------------------------------
        create_rows = _rows(two.conf2, "events", "ref_id = ? AND event_type = ?",
                            task_id, "task.created")
        complete_rows = _rows(two.conf2, "events",
                              "ref_id = ? AND event_type = ?",
                              task_id, "task.state_changed")
        assert len(create_rows) == 1
        assert len(complete_rows) == 2  # in_progress then completed
        create = create_rows[0]
        complete = complete_rows[-1]  # the completed transition
        read_rows = _rows(two.conf1, "events",
                          "ref_id = ? AND event_type = ?",
                          read_task["task_id"], "task.created")
        assert len(read_rows) == 1
        read = read_rows[0]

        # P1: hlc order is correct (lexicographic == causal order)
        assert create["hlc"] < complete["hlc"] < read["hlc"]
        # all three live in the same 30 s-skewed region of B's clock:
        # the physical ms advances between stamps, so the window is a
        # few ms wide, not a single value (the design's (l_B, c) with
        # c ordering — P1 is the counter order, P4 the absorption)
        l_vals = [int(h.split(".")[0]) for h in
                  (create["hlc"], complete["hlc"], read["hlc"])]
        assert max(l_vals) - min(l_vals) <= 5000

        # P2: the wall lies, the clock doesn't: at(complete) = t+30000
        # > at(read) = t+3, while hlc(complete) < hlc(read)
        assert complete["at"] > read["at"]
        # sharpest form: on instance1, the read event's hlc is ~30 s
        # ahead of its own wall at — the causal clock absorbed the
        # remote skew, the wall did not
        from datetime import datetime, timezone

        from synapse.hlc import decode

        l_read, _ = decode(read["hlc"])
        at_ms = int(datetime.fromisoformat(read["at"].replace("Z", "+00:00"))
                    .timestamp() * 1000)
        assert l_read - at_ms >= SKEW_MS - 2000

        # P3: causal query "did create happen-before complete?" is TRUE
        # with a verifiable chain: complete.ref_id == the task created
        # by the create event, and hlc(create) < hlc(complete)
        assert complete["ref_id"] == create["ref_id"] == task_id
        assert create["hlc"] < complete["hlc"]
        # the chain is also exact at the journal level (prev-event edge):
        # complete -> in_progress -> create, one hop per event
        assert int(complete["prev_event"]) == complete_rows[0]["seq"]
        assert int(complete_rows[0]["prev_event"]) == create["seq"]

        # P4: monotonicity — no hlc.l regression anywhere, and
        # instance1 absorbed the remote +30 s (its clock now sits at
        # l_B, the max of instance2's journal)
        b_max = max(decode(r["hlc"])[0] for r in
                    _rows(two.conf2, "events", "hlc IS NOT NULL"))
        a_rows = _rows(two.conf1, "events", "hlc IS NOT NULL")
        a_max = max(decode(r["hlc"])[0] for r in a_rows)
        assert a_max >= b_max            # absorbed, never regressed
        assert a_max - b_max <= 5000     # same +30 s region (ms drift)
        assert decode(two.clock1.peek())[0] == a_max  # clock at the bound
        assert decode(read["hlc"])[0] == a_max        # the read event is at it
        for r in a_rows:                              # nothing beyond
            assert decode(r["hlc"])[0] <= a_max
    finally:
        bridge2.stop()


def test_bridge_rejects_malformed_envelope_hlc(two):
    """H5 through the real bridge: a malformed metadata.hlc is rejected
    (-32602) and never touches the clock; the bridge keeps working."""
    bridge2, port2 = _start_bridge2(two)
    try:
        before = two.clock2.peek()
        body = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tasks/message",
            "params": {"message": {"parts": [{"text": "x"}],
                                   "metadata": {"hlc": "garbage-hlc"}}},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port2}/message", data=body,
            headers={"Content-Type": "application/json", "X-Synapse-Token": TOKEN},
        )
        with urllib.request.urlopen(req) as resp:
            response = json.loads(resp.read().decode("utf-8"))
        assert response["error"]["code"] == -32602
        # the clock was NOT advanced by the garbage value
        assert two.clock2.peek() == before

        # a valid call still works (the bridge was not wedged)
        from synapse.a2a_client import A2AClient

        client = A2AClient(f"http://127.0.0.1:{port2}", TOKEN)
        result = client.delegate("still works")
        assert result["taskId"]
    finally:
        bridge2.stop()
