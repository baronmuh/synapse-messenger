"""MVE-1 — Hybrid Logical Clock unit tests (C1, DESIGN_CAUSAL_TIME_HLC_v2).

Covers the acceptance H3/H5/H6 slices that are unit-testable:
- the update rules (local event + all 4 receive-merge branches, Kulkarni
  2014 as implemented by CockroachDB — the reference contract, §3.1a),
- the canonical fixed-width encoding (SQLite TEXT order == causal order),
- the invariants I1-I4 (happens-before order, bounded lag, monotonicity
  across backwards physical jumps, seq order within one instance),
- the skew seam (§8.2: injectable physical provider, +30000 ms),
- rehydration from the persisted upper bound (§3.3),
- the boundary validators (validate_hlc H5, {agent, hlc} deadline
  acceptance — R2 feed-in, no semantics),
- service-level: events/task_events/audit rows carry hlc (non-NULL,
  monotone), the Events API exposes it, purge still keys on at (H6).

MVE-2 (two instances + real bridge + +30 s skew) lives in
tests/test_mve_causal.py.
"""

from __future__ import annotations

import threading
import time

import pytest

from .conftest import ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD

# ---------------------------------------------------------------------------
# Clock math (pure unit, no server)
# ---------------------------------------------------------------------------


def test_encode_decode_roundtrip():
    from synapse.hlc import decode, encode

    assert decode(encode(1786462715123, 42)) == (1786462715123, 42)
    assert decode(encode(0, 0)) == (0, 0)
    assert decode(encode(9999999999999, 999999)) == (9999999999999, 999999)


def test_encode_fixed_width():
    from synapse.hlc import encode

    assert encode(1786462715123, 42) == "1786462715123.000042"
    assert encode(0, 0) == "0000000000000.000000"
    assert encode(9999999999999, 999999) == "9999999999999.999999"


def test_encode_rejects_out_of_range():
    from synapse.hlc import encode

    with pytest.raises(ValueError):
        encode(-1, 0)
    with pytest.raises(ValueError):
        encode(10**13, 0)
    with pytest.raises(ValueError):
        encode(0, -1)
    with pytest.raises(ValueError):
        encode(0, 10**6)


def test_is_valid_accepts_canonical():
    from synapse.hlc import is_valid

    assert is_valid("1786462715123.000042")
    assert is_valid("0000000000000.000000")
    assert is_valid("9999999999999.999999")


def test_is_valid_rejects_malformed():
    from synapse.hlc import is_valid

    assert not is_valid("1786462715123.00042")       # short logical
    assert not is_valid("178646271512.000042")       # short physical
    assert not is_valid("1786462715123.0000420")     # long
    assert not is_valid("1786462715123")             # no dot
    assert not is_valid("1786462715123.00004a")      # letter
    assert not is_valid("-1786462715123.000042")     # negative
    assert not is_valid("")
    assert not is_valid("1786462715123.000042\n")    # trailing newline
    assert not is_valid(1786462715123)  # type: ignore[arg-type]  # not a string
    assert not is_valid(None)  # type: ignore[arg-type]


def test_local_rule_first_stamp():
    from synapse.hlc import HLC

    clock = HLC(physical=lambda: 1000)
    assert clock.stamp() == "0000000001000.000000"


def test_local_rule_same_physical_increments_logical():
    from synapse.hlc import HLC

    clock = HLC(physical=lambda: 1000)
    assert clock.stamp() == "0000000001000.000000"
    assert clock.stamp() == "0000000001000.000001"
    assert clock.stamp() == "0000000001000.000002"


def test_local_rule_physical_ahead_resets_logical():
    from synapse.hlc import HLC

    physical = iter([1000, 1000, 1000, 1000, 2000])
    clock = HLC(physical=lambda: next(physical))
    for _ in range(4):
        clock.stamp()
    assert clock.stamp() == "0000000002000.000000"


def test_local_rule_physical_behind_keeps_l():
    """I3: hlc.l never decreases, even across a backwards physical jump."""
    from synapse.hlc import HLC

    physical = iter([2000, 2000, 2000, 2000, 1500])
    clock = HLC(physical=lambda: next(physical))
    for _ in range(4):
        clock.stamp()
    # physical fell back to 1500: the clock stays at l=2000, c advances
    assert clock.stamp() == "0000000002000.000004"


def test_merge_rule_all_four_branches():
    """The RECEIVE rule: all 4 branches of DESIGN §3.2."""
    from synapse.hlc import HLC

    # branch 1: l == l_r == l'  ->  c' = max(c, c_r) + 1
    clock = HLC(physical=lambda: 1000, initial="0000000001000.000005")
    clock.observe("0000000001000.000007")
    assert clock.peek() == "0000000001000.000008"

    # branch 2: l == l' > l_r   ->  c' = c + 1
    clock = HLC(physical=lambda: 1000, initial="0000000001000.000005")
    clock.observe("0000000000900.000003")
    assert clock.peek() == "0000000001000.000006"

    # branch 3: l_r == l' > l   ->  c' = c_r + 1
    clock = HLC(physical=lambda: 1000, initial="0000000001000.000005")
    clock.observe("0000000001100.000003")
    assert clock.peek() == "0000000001100.000004"

    # branch 4 (defensive else, c' = 0): unreachable through observe()
    # because l' = max(l, l_r) — the paper's general form keeps it for
    # the LOCAL rule (physical ahead of both, c' = 0), tested in
    # test_local_rule_physical_ahead_resets_logical. What observe()
    # must never do is consult the physical clock: the merge is purely
    # between logical clocks, so a physical ahead of both changes
    # nothing here.
    clock = HLC(physical=lambda: 1300, initial="0000000001000.000005")
    clock.observe("0000000001100.000003")
    assert clock.peek() == "0000000001100.000004"


def test_merge_rule_kulkarni_plus_one_on_receive():
    """Contract check: the +1 on receive is Kulkarni's rule, kept per
    the scout contract checks (do NOT switch to CockroachDB's Update()
    max-without-increment)."""
    from synapse.hlc import HLC

    clock = HLC(physical=lambda: 1000, initial="0000000001000.000005")
    clock.observe("0000000001000.000002")
    assert clock.peek() == "0000000001000.000006"  # max(c, c_r) + 1 = 5+1


def test_observe_rejects_malformed():
    from synapse.hlc import HLC

    clock = HLC(physical=lambda: 1000)
    with pytest.raises(ValueError):
        clock.observe("not-an-hlc")


def test_decode_rejects_malformed():
    from synapse.hlc import decode

    with pytest.raises(ValueError):
        decode("not-an-hlc")
    with pytest.raises(ValueError):
        decode("1786462715123")  # missing the logical component


def test_skew_seam_absorbs_remote_clock():
    """MVE-2's unit-level half: a +30000 ms provider behaves like an
    NTP-skewed instance; a peer observing it jumps 30 s into the
    future (the merge rule), never backwards."""
    from synapse.hlc import HLC, default_pt

    skewed = HLC(physical=lambda: default_pt() + 30000)
    stamp = skewed.stamp()
    assert int(stamp.split(".")[0]) >= default_pt() + 30000 - 5

    correct = HLC(physical=default_pt)
    correct.observe(stamp)
    after = correct.stamp()
    # the correct clock absorbed the remote +30 s: l never regresses
    assert int(after.split(".")[0]) >= default_pt() + 30000 - 5


def test_rehydration_starts_at_persisted_upper_bound():
    from synapse.hlc import HLC, encode

    upper = encode(1786462715123, 42)
    clock = HLC(physical=lambda: 1786462715000, initial=upper)
    assert clock.peek() == upper
    # next local event stays above the persisted bound (I3 across restarts)
    assert clock.stamp() > upper


def test_rehydration_rejects_malformed():
    from synapse.hlc import HLC

    with pytest.raises(ValueError):
        HLC(physical=lambda: 1000, initial="junk")


def test_thread_safety_unique_strict_order():
    """The clock is a single process-wide instance guarded by a lock:
    concurrent stamps are unique and strictly increasing."""
    from synapse.hlc import HLC

    clock = HLC(physical=lambda: int(time.time() * 1000))
    stamps: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(100):
            s = clock.stamp()
            with lock:
                stamps.append(s)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(stamps) == 800
    assert len(set(stamps)) == 800
    assert stamps == sorted(stamps)  # lexicographic order == causal order


# ---------------------------------------------------------------------------
# Boundary validators (H5 + {agent, hlc} deadline acceptance, R2 feed-in)
# ---------------------------------------------------------------------------


def test_validate_hlc_accepts_canonical():
    from synapse.validation import validate_hlc

    value = "1786462715123.000042"
    assert validate_hlc(value) == value


def test_validate_hlc_rejects_malformed():
    from synapse.validation import _invalid, validate_hlc

    for bad in ("1786462715123", "1786462715123.00004a", 42, None, {}):
        with pytest.raises(Exception) as exc:
            validate_hlc(bad)
        assert type(exc.value) is type(_invalid("x"))  # INVALID_ARGUMENT family


def test_deadline_accepted_at_boundary_without_semantics(fx):
    """R2 feed-in: create_task accepts a {agent, hlc} deadline, validates
    its shape, and implements NO semantics (not stored, not enforced —
    phase-2 T1 seam for AP4)."""
    from synapse.hlc import encode

    deadline = {"agent": BOB, "hlc": encode(1786462715123, 7)}
    task = fx.client.create_task(
        "deadline-acceptance task", BOB, ALICE, ALICE_PASSWORD, deadline=deadline,
    )
    assert task["title"] == "deadline-acceptance task"
    # the deadline is accepted, not interpreted: no field on the task
    assert "deadline" not in task


def test_deadline_malformed_hlc_rejected(fx):
    from synapse.client import ApiClientError

    with pytest.raises(ApiClientError) as exc:
        fx.client.create_task(
            "bad deadline", BOB, ALICE, ALICE_PASSWORD,
            deadline={"agent": BOB, "hlc": "not-an-hlc"},
        )
    assert exc.value.code == "INVALID_ARGUMENT"


def test_deadline_wrong_shape_rejected(fx):
    from synapse.client import ApiClientError

    for bad in (
        {"hlc": "1786462715123.000042"},          # missing agent
        {"agent": BOB},                           # missing hlc
        {"agent": "", "hlc": "1786462715123.000042"},  # empty agent
        {"agent": BOB, "hlc": "1786462715123.000042", "extra": 1},  # unknown key
        "1786462715123.000042",                   # not an object
    ):
        with pytest.raises(ApiClientError) as exc:
            fx.client.create_task(
                "bad deadline", BOB, ALICE, ALICE_PASSWORD, deadline=bad,
            )
        assert exc.value.code == "INVALID_ARGUMENT"


# ---------------------------------------------------------------------------
# Service-level: hlc on the journal rows, Events API, I4, purge (H5/H6)
# ---------------------------------------------------------------------------


def _db(fx):
    from synapse.db import connect

    return connect(fx.config)


def test_write_paths_carry_hlc_non_null_and_monotone(fx):
    """Every journal row written by the task lifecycle carries a
    non-NULL, canonical hlc; hlc order matches seq order (I4)."""
    from synapse.hlc import is_valid

    task = fx.client.create_task("hlc task", BOB, ALICE, ALICE_PASSWORD)
    fx.client.update_task_state(task["task_id"], "in_progress", BOB, BOB_PASSWORD)
    fx.client.update_task_state(task["task_id"], "completed", BOB, BOB_PASSWORD,
                                result="done")

    with _db(fx) as conn:
        events_rows = conn.execute(
            "SELECT seq, hlc, event_type FROM events WHERE principal = ? ORDER BY seq",
            (ALICE,),
        ).fetchall()
        task_rows = conn.execute(
            "SELECT id, hlc FROM task_events WHERE task_id = ? ORDER BY id",
            (task["task_id"],),
        ).fetchall()
        audit_rows = conn.execute(
            "SELECT hlc FROM audit_log WHERE target_username = ?",
            (task["task_id"],),
        ).fetchall()

    assert len(events_rows) >= 2  # task.created + task.state_changed (for alice)
    assert all(is_valid(r["hlc"]) for r in events_rows)
    assert all(is_valid(r["hlc"]) for r in task_rows)
    assert all(is_valid(r["hlc"]) for r in audit_rows)
    # I4: within one instance, hlc order == seq order (strictly increasing)
    assert [r["hlc"] for r in events_rows] == sorted(r["hlc"] for r in events_rows)
    assert [r["hlc"] for r in task_rows] == sorted(r["hlc"] for r in task_rows)
    # one stamp per transaction (§4.3): the task_events rows and the
    # journal rows of the same transaction carry the SAME stamp
    tx_stamps = sorted({r["hlc"] for r in task_rows})
    event_stamps = sorted({r["hlc"] for r in events_rows})
    assert tx_stamps == event_stamps
    assert len(tx_stamps) == 3  # created + in_progress + completed


def test_events_api_exposes_hlc_and_prev_event(fx):
    """H5: the Events API returns hlc per event; the prev-event DAG edge
    (R2 feed-in) links each event to its immediate predecessor."""
    fx.client.create_task("hlc api task 1", BOB, ALICE, ALICE_PASSWORD)
    fx.client.create_task("hlc api task 2", BOB, ALICE, ALICE_PASSWORD)

    data = fx.client.get_events(ALICE, ALICE_PASSWORD)
    events = data["events"]
    assert len(events) >= 2
    for e in events:
        assert "hlc" in e and e["hlc"]
        assert "prev_event" in e
    # exact DAG edge: each event (after the first) points at the seq of
    # the previous event of the same principal
    by_seq = {e["seq"]: e for e in events}
    for e in events:
        if e["prev_event"] is not None:
            assert int(e["prev_event"]) in by_seq
            assert by_seq[int(e["prev_event"])]["seq"] < e["seq"]


def test_events_prev_event_chain_is_exact(fx):
    """The prev-event link is the EXACT chain: seq[i].prev_event ==
    seq[i-1] for consecutive events of one principal."""
    fx.client.create_task("chain 1", BOB, ALICE, ALICE_PASSWORD)
    fx.client.create_task("chain 2", BOB, ALICE, ALICE_PASSWORD)
    fx.client.create_task("chain 3", BOB, ALICE, ALICE_PASSWORD)

    data = fx.client.get_events(ALICE, ALICE_PASSWORD)
    seqs = [e["seq"] for e in data["events"]]
    prevs = [e["prev_event"] for e in data["events"]]
    assert seqs == sorted(seqs)
    assert prevs[0] is None
    for i in range(1, len(seqs)):
        assert prevs[i] == str(seqs[i - 1])  # TEXT column, str vs int


def test_purge_still_keys_on_at(fx):
    """H6: retention purge is unchanged — it keys on at, never on hlc."""
    import synapse.db as db_mod

    task = fx.client.create_task("purge task", BOB, ALICE, ALICE_PASSWORD)
    with _db(fx) as conn:
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE ref_id = ?", (task["task_id"],)
        ).fetchone()["n"]
        # purge_old with a negative retention removes everything older
        # than a cutoff in the future — driven by `at`, like production.
        from synapse.store import events as events_store

        events_store.purge_old(conn, 0, "9999-12-31T23:59:59.999Z")
        after = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE ref_id = ?", (task["task_id"],)
        ).fetchone()["n"]
    assert before >= 1
    assert after == 0


def test_rehydration_after_restart_keeps_upper_bound(fx):
    """The clock rehydrates from MAX(hlc): a fresh service on the same
    DB never stamps below the persisted upper bound (I3 across
    restarts)."""
    import synapse.db as db_mod
    from synapse.hlc import decode

    fx.client.create_task("rehydrate", BOB, ALICE, ALICE_PASSWORD)
    with _db(fx) as conn:
        upper = db_mod.max_hlc(conn)
    assert upper is not None

    # a brand-new clock built like Service._build_clock starts at upper
    from synapse.hlc import HLC, default_pt

    clock = HLC(physical=default_pt, initial=upper)
    assert clock.peek() == upper
    assert decode(clock.stamp()) >= decode(upper)
