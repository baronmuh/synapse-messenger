"""Unit coverage for ``synapse/transport.py`` (Unix + token branches).

Complements the existing ``test_transport_tcp.py`` lifecycle tests by
covering the pure resolution/token helpers and the Unix-socket branches
directly (monkeypatched sockets, no server).
"""

from __future__ import annotations

import os

import pytest

from synapse import platform
from synapse import transport as tr
from synapse.config import Config


def _cfg(**over):
    base = {
        "storage_dir": "/tmp/x/d",
        "socket_path": "/tmp/x/run/synapse.sock",
        "log_dir": "/tmp/x/logs",
        "backup_dir": "/tmp/x/backups",
    }
    base.update(over)
    return Config.from_dict(base)


def test_resolve_transport_explicit_unix_tcp():
    assert tr.resolve_transport(_cfg(transport="unix")) == "unix"
    assert tr.resolve_transport(_cfg(transport="tcp")) == "tcp"
    assert tr.resolve_transport(_cfg(transport=" TCP ")) == "tcp"  # stripped/lower
    assert tr.resolve_transport(_cfg()) == platform.default_transport()


def test_resolve_transport_unknown_raises():
    with pytest.raises(ValueError):
        tr.resolve_transport(_cfg(transport="carrier-pigeon"))


def test_transport_port_default_and_explicit():
    assert tr.transport_port(_cfg()) == tr.DEFAULT_TRANSPORT_PORT
    assert tr.transport_port(_cfg(transport_port=9999)) == 9999


def test_run_dir_explicit():
    assert tr.run_dir(_cfg(run_dir="/custom/run")) == "/custom/run"


def test_run_dir_unix_parent():
    cfg = _cfg(transport="unix")
    assert tr.run_dir(cfg) == "/tmp/x/run"


def test_run_dir_tcp_platform_default(monkeypatch):
    cfg = _cfg(transport="tcp")
    monkeypatch.setattr(platform, "default_paths", lambda: {"run": "/plat/run"})
    assert tr.run_dir(cfg) == "/plat/run"


def test_token_path_under_run_dir():
    cfg = _cfg(run_dir="/r")
    assert tr.token_path(cfg) == os.path.join("/r", tr.TOKEN_FILENAME)


def test_read_token_from_none_and_missing():
    assert tr.read_token_from(None) is None
    assert tr.read_token_from("/no/such/dir") is None


def test_read_token_from_present_and_empty(tmp_path):
    p = tmp_path / tr.TOKEN_FILENAME
    p.write_text("abc123\n", encoding="ascii")
    assert tr.read_token_from(str(tmp_path)) == "abc123"
    p.write_text("   \n", encoding="ascii")
    assert tr.read_token_from(str(tmp_path)) is None


def test_ensure_token_generates_and_persists(tmp_path):
    cfg = _cfg(run_dir=str(tmp_path))
    t1 = tr.ensure_token(cfg)
    assert len(t1) == tr.TOKEN_BYTES * 2  # hex
    assert tr.read_token(cfg) == t1
    # second call returns the same persisted token
    assert tr.ensure_token(cfg) == t1
    # file is 0600 on POSIX
    st = os.stat(tr.token_path(cfg))
    assert st.st_mode & 0o777 == 0o600


def test_remove_token_missing_is_noop(tmp_path):
    cfg = _cfg(run_dir=str(tmp_path))
    tr.remove_token(cfg)  # must not raise
    assert tr.read_token(cfg) is None


def test_remove_token_removes(tmp_path):
    cfg = _cfg(run_dir=str(tmp_path))
    tr.ensure_token(cfg)
    tr.remove_token(cfg)
    assert tr.read_token(cfg) is None


# ---------------------------------------------------------------------------
# transport_responds / connect (Unix branch, monkeypatched)
# ---------------------------------------------------------------------------


def test_transport_responds_unix_up(monkeypatch):
    cfg = _cfg(transport="unix", socket_path="/tmp/fake.sock")
    sock_inst = type("S", (), {"settimeout": lambda self, t: None,
                               "connect": lambda self, p: None,
                               "close": lambda self: None})()
    monkeypatch.setattr(tr.socket, "socket",
                        lambda *a, **k: sock_inst)
    assert tr.transport_responds(cfg) is True


def test_transport_responds_unix_down(monkeypatch):
    cfg = _cfg(transport="unix", socket_path="/tmp/fake.sock")

    class _Sock:
        def settimeout(self, t):
            pass
        def connect(self, p):
            raise ConnectionRefusedError
        def close(self):
            pass

    monkeypatch.setattr(tr.socket, "socket", lambda *a, **k: _Sock())
    assert tr.transport_responds(cfg) is False


def test_transport_responds_tcp_up(monkeypatch):
    cfg = _cfg(transport="tcp", transport_port=7999)
    ctx = type("C", (), {"__enter__": lambda self: self,
                         "__exit__": lambda self, *a: False})()
    monkeypatch.setattr(tr.socket, "create_connection",
                        lambda addr, timeout: ctx)
    assert tr.transport_responds(cfg) is True


def test_transport_responds_tcp_down(monkeypatch):
    cfg = _cfg(transport="tcp", transport_port=7999)
    monkeypatch.setattr(
        tr.socket, "create_connection",
        lambda addr, timeout: (_ for _ in ()).throw(OSError("refused")))
    assert tr.transport_responds(cfg) is False


def test_connect_unix(monkeypatch):
    cfg = _cfg(transport="unix", socket_path="/tmp/fake.sock")
    sock_inst = type("S", (), {"settimeout": lambda self, t: None,
                               "connect": lambda self, p: None})()
    monkeypatch.setattr(tr.socket, "socket", lambda *a, **k: sock_inst)
    assert tr.connect(cfg) is sock_inst


def test_connect_tcp_missing_token(monkeypatch, tmp_path):
    cfg = _cfg(transport="tcp", transport_port=7999, run_dir=str(tmp_path))
    sock_inst = type("S", (), {"close": lambda self: None})()
    monkeypatch.setattr(tr.socket, "create_connection",
                        lambda addr, timeout: sock_inst)
    with pytest.raises(OSError, match="transport token missing"):
        tr.connect(cfg)


def test_connect_tcp_with_token(monkeypatch, tmp_path):
    cfg = _cfg(transport="tcp", transport_port=7999, run_dir=str(tmp_path))
    tr.ensure_token(cfg)
    sent = []

    class _Sock:
        def close(self):
            pass
        def sendall(self, data):
            sent.append(data)

    monkeypatch.setattr(tr.socket, "create_connection", lambda addr, timeout: _Sock())
    tr.connect(cfg)
    assert sent and sent[0].startswith(tr.read_token(cfg).encode("ascii"))
