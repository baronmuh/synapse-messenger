"""Unit coverage for ``synapse/client.py`` TCP transport branches.

Covers the TCP-specific paths of ``Client`` (default port, transport token
handling, response-size guard, connection-error mapping) with monkeypatched
sockets — no server subprocess.
"""

from __future__ import annotations


import pytest

from synapse import client as cli
from synapse import transport as tr
from synapse.client import ApiClientError, Client, ClientTransportError


def _tcp_client(tmp_path, port=7999, run_dir=None):
    return Client("/tmp/x.sock", transport="tcp", transport_port=port,
                  run_dir=run_dir or str(tmp_path))


def test_tcp_client_default_port_when_none(monkeypatch):
    import synapse.platform as _plat
    monkeypatch.setattr(_plat, "default_transport", lambda: "tcp")
    c = Client("/tmp/x.sock", transport="tcp", transport_port=None)
    assert c._port == tr.DEFAULT_TRANSPORT_PORT


def test_tcp_client_explicit_port():
    c = Client("/tmp/x.sock", transport="tcp", transport_port=7777)
    assert c._port == 7777


def test_request_rejects_oversized_payload(monkeypatch):
    monkeypatch.setattr(cli, "_MAX_REQUEST_BYTES", 16)
    c = Client("/tmp/x.sock")
    with pytest.raises(ClientTransportError, match="Request too large"):
        c.request("help", {"big": "x" * 100})


def test_transact_tcp_missing_token(monkeypatch, tmp_path):
    import synapse.transport as _tr
    monkeypatch.setattr(cli.socket, "create_connection",
                        lambda addr, timeout: _FakeSock())
    monkeypatch.setattr(_tr, "read_token_from", lambda rd: None)
    c = _tcp_client(tmp_path)
    with pytest.raises(ClientTransportError, match="transport token missing"):
        c._transact(b"{}")


def test_transact_tcp_response_too_large(monkeypatch, tmp_path):
    import synapse.transport as _tr
    monkeypatch.setattr(_tr, "read_token_from", lambda rd: "tok")
    monkeypatch.setattr(cli, "_MAX_RESPONSE_BYTES", 4)

    class _Sock:
        def __init__(self):
            self.sent = []
        def sendall(self, d):
            self.sent.append(d)
        def shutdown(self, how):
            pass
        def recv(self, n):
            return b"x" * 8  # bigger than the 4-byte cap -> raises
        def close(self):
            pass

    monkeypatch.setattr(cli.socket, "create_connection",
                        lambda addr, timeout: _Sock())
    c = _tcp_client(tmp_path)
    with pytest.raises(ClientTransportError, match="Response too large"):
        c._transact(b"{}")


def test_transact_tcp_connection_error(monkeypatch, tmp_path):
    import synapse.transport as _tr
    monkeypatch.setattr(_tr, "read_token_from", lambda rd: "tok")

    class _Sock:
        def sendall(self, d):
            raise BrokenPipeError("peer closed")
        def shutdown(self, how):
            pass
        def recv(self, n):
            return b""
        def close(self):
            pass

    monkeypatch.setattr(cli.socket, "create_connection",
                        lambda addr, timeout: _Sock())
    c = _tcp_client(tmp_path)
    with pytest.raises(ClientTransportError, match="Cannot reach the service"):
        c._transact(b"{}")


def test_transact_tcp_success_and_error_mapping(monkeypatch, tmp_path):
    import synapse.transport as _tr
    monkeypatch.setattr(_tr, "read_token_from", lambda rd: "tok")
    sent = []

    class _Sock:
        def sendall(self, d):
            sent.append(d)
        def shutdown(self, how):
            pass
        def recv(self, n):
            # first call returns the envelope, second returns b"" -> EOF
            if not getattr(self, "done", False):
                self.done = True
                return (b'{"success": true, "data": {"ok": 1}}\n')
            return b""
        def close(self):
            pass

    monkeypatch.setattr(cli.socket, "create_connection",
                        lambda addr, timeout: _Sock())
    c = _tcp_client(tmp_path)
    # first line sent is the transport token
    out = c._transact(b"{}")
    assert out["data"]["ok"] == 1
    assert sent[0].startswith(b"tok")


def test_transact_tcp_success_error_response(monkeypatch, tmp_path):
    import synapse.transport as _tr
    monkeypatch.setattr(_tr, "read_token_from", lambda rd: "tok")

    class _Sock:
        def sendall(self, d):
            pass
        def shutdown(self, how):
            pass
        def recv(self, n):
            if not getattr(self, "done", False):
                self.done = True
                return (b'{"success": false, "error": {"code": "DENIED", '
                        b'"message": "no"}}')
            return b""
        def close(self):
            pass

    monkeypatch.setattr(cli.socket, "create_connection",
                        lambda addr, timeout: _Sock())
    c = _tcp_client(tmp_path)
    with pytest.raises(ApiClientError) as exc:
        c.request("get", {})
    assert exc.value.code == "DENIED"
    assert exc.value.message == "no"


class _FakeSock:
    def sendall(self, d):
        pass
    def shutdown(self, how):
        pass
    def recv(self, n):
        return b""
    def close(self):
        pass
