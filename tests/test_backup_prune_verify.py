"""Tests for retention (prune) and restore proof (verify)
introduced by SPEC_PRODUCTION §3: bounded deletion, verification in
isolated storage, refusal of dangerous directories, error cases.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from synapse.backup import BackupError, backup, prune, verify
from synapse.config import Config

from tests.cli_helpers import run_cli
from tests.conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD


def _seed_state(fx):
    fx.send(ALICE, ALICE_PASSWORD, BOB, "message important", "cmid-bk-v1")
    fx.send(BOB, BOB_PASSWORD, ALICE, "reply", "cmid-bk-v2")


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


def test_prune_keeps_newest(config, tmp_path):
    backup_dir = Path(config.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    base = time.time()
    for i in range(5):
        path = backup_dir / f"synapse-backup-{i}.synbk"
        path.write_bytes(b"fake")
        os.utime(path, (base + i, base + i))
    deleted = prune(config, keep=2)
    assert len(deleted) == 3
    remaining = sorted(p.name for p in backup_dir.glob("*.synbk"))
    assert remaining == ["synapse-backup-3.synbk", "synapse-backup-4.synbk"]


def test_prune_only_synbk(config, tmp_path):
    """prune NEVER touches files other than *.synbk."""
    backup_dir = Path(config.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "notes.txt").write_text("important")
    (backup_dir / "synapse-backup-1.synbk").write_bytes(b"x")
    (backup_dir / "synapse-backup-2.synbk").write_bytes(b"x")
    deleted = prune(config, keep=1)
    assert len(deleted) == 1
    assert (backup_dir / "notes.txt").read_text() == "important"
    assert len(list(backup_dir.glob("*.synbk"))) == 1


def test_prune_keep_zero_refused(config):
    with pytest.raises(ValueError):
        prune(config, keep=0)


def test_prune_empty_dir(config, tmp_path):
    backup_dir = Path(config.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    assert prune(config, keep=14) == []
    assert prune(config, keep=1) == []


def test_prune_missing_dir(config):
    """Nonexistent backup_dir: no results, no error."""
    assert prune(config, keep=14) == []


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def test_verify_ok(fx, config):
    _seed_state(fx)
    path = backup(config)
    report = verify(config, path)
    assert report["integrity"] == "ok"
    assert report["format"] == 1
    assert report["archive"] == path
    assert report["tables"] >= 1
    assert report["created_at"]
    # production storage was not modified (the database is still there)
    assert os.path.exists(config.db_path)


def test_verify_while_server_running(fx, config):
    """Unlike restore, verify does NOT require the service to be stopped."""
    _seed_state(fx)
    path = backup(config)
    report = verify(config, path)
    assert report["integrity"] == "ok"


def test_verify_scratch_inside_storage_refused(fx, config):
    """Refusal of a scratch that would contain the production storage."""
    _seed_state(fx)
    path = backup(config)
    with pytest.raises(BackupError, match="invalid verification directory"):
        verify(config, path, scratch_dir=config.storage_dir)
    # production intact
    assert os.path.exists(config.db_path)


def test_verify_scratch_explicit(config, fx, tmp_path):
    """Explicit scratch: report produced, database file cleaned up after."""
    _seed_state(fx)
    path = backup(config)
    scratch = tmp_path / "scratch"
    report = verify(config, path, scratch_dir=str(scratch))
    assert report["integrity"] == "ok"
    assert not list(Path(scratch).glob("*.db"))  # database file destroyed after


def test_verify_corrupted(fx, config):
    _seed_state(fx)
    path = backup(config)
    raw = bytearray(Path(path).read_bytes())
    raw[-1] ^= 0xFF  # corrupt the last byte of the ciphertext
    Path(path).write_bytes(bytes(raw))
    with pytest.raises(BackupError, match="Decryption failed"):
        verify(config, path)


def test_verify_wrong_key(fx, config, tmp_path):
    """A different key (other storage) cannot decrypt the archive."""
    _seed_state(fx)
    path = backup(config)
    other_storage = tmp_path / "other"
    other_storage.mkdir()
    other = Config.from_dict({**config.to_dict(),
                              "storage_dir": str(other_storage)})
    with pytest.raises(BackupError, match="Decryption failed"):
        verify(other, path)


def test_verify_missing_archive(config):
    with pytest.raises(BackupError, match="Backup not found"):
        verify(config, "/chemin/inexistant.synbk")


# ---------------------------------------------------------------------------
# CLI : backup prune / backup verify
# ---------------------------------------------------------------------------


def test_cli_backup_verify_latest(cli_env):
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    run_cli(env, "server", "start")
    try:
        proc = run_cli(env, "backup", "create")
        assert proc.returncode == 0, proc.stderr.decode()

        proc = run_cli(env, "backup", "verify", "--latest", "--json")
        assert proc.returncode == 0, proc.stderr.decode()
        data = json.loads(proc.stdout.decode())["data"]
        assert data["integrity"] == "ok"
        assert data["tables"] >= 1

        proc = run_cli(env, "backup", "verify", data["archive"], "--json")
        assert proc.returncode == 0, proc.stderr.decode()
    finally:
        run_cli(env, "server", "stop")


def test_cli_backup_verify_no_archive(cli_env):
    _, _, env = cli_env
    proc = run_cli(env, "backup", "verify")
    assert proc.returncode == 1
    assert "an archive is required" in proc.stderr.decode()


def test_cli_backup_verify_scratch_guard(cli_env, config):
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    run_cli(env, "server", "start")
    try:
        run_cli(env, "backup", "create")
        proc = run_cli(env, "backup", "verify", "--latest",
                       "--dir", config.storage_dir)
        assert proc.returncode == 1
        assert "invalid verification directory" in proc.stderr.decode()
    finally:
        run_cli(env, "server", "stop")


def test_cli_backup_prune(cli_env, tmp_path):
    _, _, env = cli_env
    bdir = tmp_path / "bkdir"
    bdir.mkdir()
    for i in range(4):
        (bdir / f"fake-{i}.synbk").write_bytes(b"x")
    (bdir / "garde.txt").write_text("ne pas toucher")

    proc = run_cli(env, "backup", "prune", "--dir", str(bdir),
                   "--keep", "2", "--json")
    assert proc.returncode == 0, proc.stderr.decode()
    deleted = json.loads(proc.stdout.decode())["data"]["deleted"]
    assert len(deleted) == 2
    assert (bdir / "garde.txt").read_text() == "ne pas toucher"

    proc = run_cli(env, "backup", "prune", "--dir", str(bdir),
                   "--keep", "0", "--json")
    assert proc.returncode == 1
    assert "keep" in proc.stderr.decode()


def test_cli_backup_prune_nothing_to_do(cli_env, tmp_path):
    _, _, env = cli_env
    proc = run_cli(env, "backup", "prune", "--dir", str(tmp_path / "vide"))
    assert proc.returncode == 0, proc.stderr.decode()
    assert "retention respected" in proc.stdout.decode()
