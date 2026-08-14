"""H1/H2 — the causal-time migration: hlc columns + deterministic,
idempotent backfill on pre-C1 databases.

A synthetic legacy DB (the pre-C1 schema: events/task_events/audit_log
WITHOUT the hlc columns, plus historical rows with equal-at groups) is
migrated through the real ``db._migrate`` path. Asserts:

- H1: hlc columns added to all three tables (existing DB path).
- H2: backfill is deterministic (same input DB -> identical hlc),
  idempotent (re-run is a no-op), all rows non-NULL, canonical format,
  monotone in (at, id/seq), strictly increasing for equal-at rows
  (the ``l_prev + 1`` guard), l derived from at (ms).
- R2 feed-in: the prev_event column is added (history stays NULL —
  backfill is forward-only by design).
"""

from __future__ import annotations

import sqlite3

from synapse import db as db_mod

_AT = [
    "2026-01-01T00:00:00.000Z",   # earliest
    "2026-01-01T00:00:00.100Z",
    "2026-01-01T00:00:00.100Z",   # equal at -> must still be strictly increasing
    "2026-01-01T00:00:00.100Z",   # equal at, third row
    "2026-02-01T00:00:00.000Z",
    "2026-02-01T00:00:01.000Z",
    "2026-03-01T12:30:00.000Z",
]


def _make_legacy_db(config) -> sqlite3.Connection:
    """Full modern schema, then strips the causal columns (SQLite
    DROP COLUMN, 3.35+) and seeds historical rows — a faithful pre-C1
    database with data."""
    db_mod.ensure_storage(config)
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    try:
        # strip the causal columns so the DB looks pre-C1
        for col in ("hlc", "prev_event"):
            conn.execute(f"ALTER TABLE events DROP COLUMN {col}")
        for table in ("task_events", "audit_log"):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN hlc")
        for i, at in enumerate(_AT):
            conn.execute(
                "INSERT INTO events (principal, event_type, ref_id, by_username, at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("legacy_agent", f"type_{i}", f"ref_{i}", "legacy_agent", at),
            )
            conn.execute(
                "INSERT INTO task_events (task_id, event, by_username, note, at) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"task_{i}", f"event_{i}", "legacy_agent", None, at),
            )
            conn.execute(
                "INSERT INTO audit_log (organization_name, at, actor_username, "
                "command, target_type, target_username, outcome) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("root_org", at, "legacy_agent", f"cmd_{i}", "task",
                 f"task_{i}", "ok"),
            )
        conn.commit()
        return conn
    except BaseException:
        conn.close()
        raise


def _hlc_columns(conn: sqlite3.Connection) -> dict[str, list[str]]:
    return {
        table: [
            r["hlc"] for r in conn.execute(
                f"SELECT hlc FROM {table} ORDER BY "
                f"{'seq' if table == 'events' else 'id'}"
            ).fetchall()
        ]
        for table in ("events", "task_events", "audit_log")
    }


def test_migration_adds_columns_and_backfills(config):
    from synapse.hlc import is_valid

    conn = _make_legacy_db(config)
    try:
        db_mod._migrate(conn)
        conn.commit()
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(events)")
        }
        assert {"hlc", "prev_event"} <= columns
        for table in ("task_events", "audit_log"):
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert "hlc" in cols
        values = _hlc_columns(conn)
        for table in ("events", "task_events", "audit_log"):
            rows = values[table]
            assert len(rows) == len(_AT)
            assert all(is_valid(h) for h in rows)          # canonical (H2)
            assert all(h is not None for h in rows)        # non-NULL (H2)
            assert rows == sorted(rows)                    # monotone in order key
        # l derived from at (ms): the first row's l == ms(2026-01-01T00:00:00Z)
        first_l = int(values["events"][0].split(".")[0])
        from datetime import datetime, timezone

        expected = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert first_l == expected
        # equal-at rows are strictly increasing via the l_prev + 1 guard
        # (H2): l bumps by 1 ms per equal-at row, c stays 0 (the +1 guard
        # is what keeps the order, per DESIGN §4.2)
        group = values["events"][1:4]
        l_vals = [int(h.split(".")[0]) for h in group]
        c_vals = [int(h.split(".")[1]) for h in group]
        assert l_vals == [l_vals[0], l_vals[0] + 1, l_vals[0] + 2]
        assert c_vals == [0, 0, 0]
        # prev_event is present but NULL for history (R2 feed-in: no backfill)
        prevs = [
            r["prev_event"]
            for r in conn.execute("SELECT prev_event FROM events").fetchall()
        ]
        assert all(p is None for p in prevs)
    finally:
        conn.close()


def test_migration_idempotent_rerun(config):
    conn = _make_legacy_db(config)
    try:
        db_mod._migrate(conn)
        conn.commit()
        first = _hlc_columns(conn)
        db_mod._migrate(conn)  # second pass: must be a no-op (NULL guard)
        conn.commit()
        second = _hlc_columns(conn)
        assert first == second
    finally:
        conn.close()


def test_backfill_deterministic_across_identical_dbs(config, tmp_path):
    """Same input DB -> identical hlc columns, byte for byte (H2)."""
    import shutil

    from synapse.config import Config

    conn = _make_legacy_db(config)
    conn.close()
    legacy_file = config.db_path

    results = []
    for i in range(2):
        clone = tmp_path / f"clone_{i}"
        shutil.copytree(config.storage_dir, clone)
        conf2 = Config.from_dict({
            "storage_dir": str(clone),
            "socket_path": str(tmp_path / f"run_{i}" / "synapse.sock"),
            "log_dir": str(tmp_path / f"logs_{i}"),
            "backup_dir": str(tmp_path / f"backups_{i}"),
        })
        c2 = sqlite3.connect(conf2.db_path)
        c2.row_factory = sqlite3.Row
        db_mod._migrate(c2)
        c2.commit()
        results.append(_hlc_columns(c2))
        c2.close()
    assert results[0] == results[1]
    from pathlib import Path

    assert Path(legacy_file).exists()


def test_fresh_db_has_hlc_via_extra_schema(config):
    """H1 (fresh DB path): EXTRA_SCHEMA creates the hlc columns."""
    db_mod.ensure_storage(config)
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    try:
        for table in ("events", "task_events", "audit_log"):
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert "hlc" in cols, table
        assert "prev_event" in {
            row[1] for row in conn.execute("PRAGMA table_info(events)")
        }
    finally:
        conn.close()
