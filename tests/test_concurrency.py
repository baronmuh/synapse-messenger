"""Concurrency tests (section 14): transactions, idempotency races,
conversation uniqueness, send/mark serialization."""

from __future__ import annotations

import threading



from .conftest import ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD


def _run_threads(fn, n=8):
    barrier = threading.Barrier(n)
    results = []
    errors = []

    def worker(i):
        barrier.wait()
        try:
            results.append(fn(i))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, errors


def test_concurrent_distinct_sends_all_delivered(fx):
    """N simultaneous distinct sends: all delivered, a single conversation."""

    def send(i):
        return fx.client.send_message(BOB, f"message {i}", f"cmid-conc-{i}", ALICE, ALICE_PASSWORD)

    results, errors = _run_threads(send, 8)
    assert not errors
    assert len(results) == 8
    conv_ids = {m["conversation_id"] for m in results}
    assert len(conv_ids) == 1  # a single conversation, even under concurrency
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert len(inbox["messages"]) == 8


def test_concurrent_same_client_message_id_single_message(fx):
    """N simultaneous sends with the same client_message_id: a single message,
    all receive the same message_id."""

    def send(_):
        return fx.client.send_message(BOB, "identique", "cmid-conc-same", ALICE, ALICE_PASSWORD)

    results, errors = _run_threads(send, 10)
    assert not errors
    ids = {m["message_id"] for m in results}
    assert len(ids) == 1
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert len(inbox["messages"]) == 1


def test_concurrent_bidirectional_first_send_single_conversation(fx):
    """Simultaneous sends in both directions: a single conversation."""

    def send_a(i):
        return fx.client.send_message(BOB, f"a{i}", f"cmid-ba-{i}", ALICE, ALICE_PASSWORD)

    def send_b(i):
        return fx.client.send_message(ALICE, f"b{i}", f"cmid-bb-{i}", BOB, BOB_PASSWORD)

    results_a, err_a = _run_threads(send_a, 4)
    results_b, err_b = _run_threads(send_b, 4)
    assert not err_a and not err_b
    conv_ids = {m["conversation_id"] for m in results_a + results_b}
    assert len(conv_ids) == 1


def test_concurrent_reads_same_first_read_date(fx):
    m = fx.send(ALICE, ALICE_PASSWORD, BOB, "lu en course", "cmid-cr-1")

    def read(_):
        return fx.client.read_message(m["message_id"], BOB, BOB_PASSWORD)["read_at"]

    results, errors = _run_threads(read, 8)
    assert not errors
    assert len(set(results)) == 1
    assert results[0] is not None


def test_concurrent_mark_and_send_serialized(fx):
    """Concurrent send and mark are serialized per conversation; the
    last committed transaction determines the final state."""
    m1 = fx.send(ALICE, ALICE_PASSWORD, BOB, "premier", "cmid-ms-1")
    conv_id = m1["conversation_id"]
    fx.client.read_message(m1["message_id"], BOB, BOB_PASSWORD)
    results = []
    errors = []

    def mark():
        try:
            results.append(("mark", fx.client.mark_conversation_no_reply(conv_id, BOB, BOB_PASSWORD)))
        except Exception as exc:  # noqa: BLE001
            errors.append(("mark", exc))

    def send():
        try:
            results.append(("send", fx.client.send_message(BOB, "nouveau", "cmid-ms-2", ALICE, ALICE_PASSWORD)))
        except Exception as exc:  # noqa: BLE001
            errors.append(("send", exc))

    barrier = threading.Barrier(2)
    t1 = threading.Thread(target=lambda: (barrier.wait(), mark()))
    t2 = threading.Thread(target=lambda: (barrier.wait(), send()))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert not errors
    # regardless of commit order, the final state is consistent:
    # - mark committed last: it targets the last received message (m2,
    #   created by the concurrent send, or m1 otherwise) -> no_reply_needed;
    # - send committed last: new unread message -> no_reply_needed.
    conv = fx.client.get_conversation(ALICE, BOB, BOB_PASSWORD)
    assert conv["reply_status"] == "no_reply_needed"
    assert len(fx.client.get_messages(BOB, BOB_PASSWORD)["messages"]) == 2


def test_concurrent_mixed_traffic_consistency(fx):
    """Mixed load (sends + reads + notifications): no errors, no
    lost messages."""

    def worker(i):
        if i % 2 == 0:
            m = fx.client.send_message(BOB, f"w{i}", f"cmid-mix-{i}", ALICE, ALICE_PASSWORD)
            fx.client.read_message(m["message_id"], BOB, BOB_PASSWORD)
        else:
            fx.client.get_notifications(BOB, BOB_PASSWORD)
            fx.client.get_messages(BOB, BOB_PASSWORD)
        return True

    results, errors = _run_threads(worker, 10)
    assert not errors
    inbox = fx.client.get_messages(BOB, BOB_PASSWORD)
    assert len(inbox["messages"]) == 5


def test_concurrent_writes_keep_database_integrity(fx):
    """Concurrent writes (8 threads) then PRAGMA integrity_check.

    The "one writer at a time" discipline (application lock against
    WAL-reset, db.begin_immediate) leaves no corruption: the database
    must pass SQLite integrity after the burst."""
    def send(i):
        return fx.client.send_message(BOB, f"integrity {i}", f"cmid-int-{i}",
                                      ALICE, ALICE_PASSWORD)

    results, errors = _run_threads(send, 8)
    assert not errors
    assert len(results) == 8
    import sqlite3

    conn = sqlite3.connect(fx.config.db_path)
    try:
        status = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    assert status == "ok"
