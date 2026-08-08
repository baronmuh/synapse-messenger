"""Atomic tests of security (Argon2, keys) and configuration."""

from __future__ import annotations

import os

import pytest

from synapse import security
from synapse.config import Config, DEFAULT_CONFIG_PATH


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_production_params_detection():
    security.install_fast_hasher()
    assert security.production_params_ok() is False
    security.install_production_hasher()
    assert security.production_params_ok() is True


def test_verify_password_invalid_hash():
    assert security.verify_password("pas-un-hash-argon2", "motdepasse") is False
    assert security.verify_password("", "motdepasse") is False


def test_verify_password_wrong_password():
    h = security.hash_password("mot-de-passe-123")
    assert security.verify_password(h, "mauvais") is False


def test_dummy_hash_cached_and_valid():
    h1 = security.dummy_hash()
    h2 = security.dummy_hash()
    assert h1 == h2  # memoized
    assert h1.startswith("$argon2id$")
    assert security.verify_dummy("nimporte") is False


def test_load_or_create_key_creates_and_reloads(tmp_path):
    path = str(tmp_path / "key.bin")
    key1 = security.load_or_create_key(path)
    key2 = security.load_or_create_key(path)
    assert key1 == key2
    assert len(key1) == 32
    assert os.stat(path).st_mode & 0o077 == 0


def test_load_or_create_key_wrong_size():
    import tempfile
    fd, path = tempfile.mkstemp()
    try:
        os.write(fd, b"trop court")
        os.close(fd)
        with pytest.raises(ValueError):
            security.load_or_create_key(path, bits=256)
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def test_load_or_create_key_race_uses_existing(tmp_path, monkeypatch):
    """If two processes create the key simultaneously, the second reads the
    first one's instead of failing."""
    path = str(tmp_path / "race.bin")
    first = security.load_or_create_key(path)
    real_open = os.open

    def flaky_open(*args, **kwargs):
        # simulates the race: the file already exists when the second opens
        if os.path.exists(path):
            raise FileExistsError()
        return real_open(*args, **kwargs)

    monkeypatch.setattr(os, "open", flaky_open)
    second = security.load_or_create_key(path)
    assert second == first


def test_constant_time_equals():
    assert security.constant_time_equals(b"abc", b"abc") is True
    assert security.constant_time_equals(b"abc", b"abd") is False
    assert security.constant_time_equals(b"abc", b"abcd") is False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_config_defaults():
    cfg = Config()
    assert cfg.storage_dir == "/var/lib/synapse"
    assert cfg.max_request_bytes == 1024 * 1024
    assert cfg.auth_max_failures == 5
    assert cfg.auth_window_seconds == 900
    assert cfg.log_retention_days == 90
    assert cfg.db_path.endswith("synapse.db")
    assert cfg.cursor_key_path.endswith("cursor.key")
    assert cfg.backup_key_path.endswith("backup.key")
    assert cfg.lock_path.endswith("service.lock")


def test_config_from_dict_ignores_unknown():
    cfg = Config.from_dict({"storage_dir": "/tmp/x", "inconnu": 1})
    assert cfg.storage_dir == "/tmp/x"
    assert cfg._extra == {"inconnu": 1}


def test_config_to_dict_roundtrip():
    cfg = Config.from_dict({"storage_dir": "/tmp/x", "log_retention_days": 30})
    data = cfg.to_dict()
    assert data["storage_dir"] == "/tmp/x"
    assert data["log_retention_days"] == 30
    assert "backup_dir" in data
    restored = Config.from_dict(data)
    assert restored == cfg


def test_config_load_missing_file_uses_defaults(tmp_path, monkeypatch):
    missing = str(tmp_path / "absent.json")
    cfg = Config.load(missing)
    assert cfg.storage_dir == "/var/lib/synapse"


def test_config_load_via_env(tmp_path, monkeypatch):
    path = tmp_path / "conf.json"
    path.write_text('{"storage_dir": "/tmp/env-data"}')
    monkeypatch.setenv("Synapse_CONFIG", str(path))
    cfg = Config.load()
    assert cfg.storage_dir == "/tmp/env-data"


def test_config_load_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{pas du json")
    with pytest.raises(ValueError):
        Config.load(str(path))


def test_config_load_non_dict_json(tmp_path):
    path = tmp_path / "arr.json"
    path.write_text("[1, 2, 3]")
    with pytest.raises(ValueError):
        Config.load(str(path))


def test_config_default_path_constant():
    assert str(DEFAULT_CONFIG_PATH) == "/etc/synapse/config.json"
