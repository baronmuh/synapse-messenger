"""Unit coverage for shared ``synapse/cli/common.py`` helpers.

Pure, monkeypatched tests (no server subprocess) that close coverage gaps in
the output formatting (``table``/``colorize``/``emit``/``emit_error``),
error mapping (``api_error``), local HTTP probe (``http_get``), token/PID
file helpers and the ``level_int`` parser.
"""

from __future__ import annotations

import json


from synapse.cli import common as common_mod
from synapse.client import ApiClientError, ClientTransportError


# ---------------------------------------------------------------------------
# table / colorize
# ---------------------------------------------------------------------------


def test_table_empty():
    assert common_mod.table([]) == "(no results)"


def test_table_with_headers():
    out = common_mod.table([["a", "longer"], ["x", "y"]], ["h1", "h2"])
    lines = out.splitlines()
    assert lines[0].startswith("h1  h2")
    assert lines[1] == "--  ------"          # separator row
    assert lines[2].startswith("a   longer")
    assert "y" in out


def test_table_no_headers():
    out = common_mod.table([["1", "b"], ["22", "c"]])
    assert "1 " in out and "22" in out


def test_colorize_disabled_and_unknown(monkeypatch):
    # disabled -> plain text
    assert common_mod.colorize("x", "red", enabled=False) == "x"
    # enable tty detection by faking an isatty stdout
    class _Tty:
        def isatty(self):
            return True
    monkeypatch.setattr(common_mod.sys, "stdout", _Tty())
    # unknown color -> plain text
    assert common_mod.colorize("x", "purple") == "x"
    assert common_mod.colorize("x", "red") == "\x1b[31mx\x1b[0m"
    assert common_mod.colorize("x", "bold") == "\x1b[1mx\x1b[0m"


# ---------------------------------------------------------------------------
# emit / emit_error / api_error
# ---------------------------------------------------------------------------


def test_emit_json(capsys):
    args = _args(json=True)
    assert common_mod.emit(args, {"a": 1}) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"success": True, "data": {"a": 1}, "error": None}


def test_emit_human(capsys):
    args = _args(json=False)
    assert common_mod.emit(args, None, human="hello world") == 0
    assert capsys.readouterr().out == "hello world\n"


def test_emit_default_json_when_no_human(capsys):
    args = _args(json=False)
    assert common_mod.emit(args, {"x": 2}) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"] == {"x": 2}


def test_emit_error_json_and_stderr(capsys):
    code = common_mod.emit_error("bad thing", code=7, api_code="X")
    assert code == 7
    out = capsys.readouterr()
    payload = json.loads(out.out)
    assert payload["success"] is False
    assert payload["error"] == {"code": "X", "message": "bad thing"}
    assert "bad thing" in out.err


def test_api_error_transport_is_3(capsys):
    code = common_mod.api_error(ClientTransportError("down"))
    assert code == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False


def test_api_error_apiclient_is_1(capsys):
    code = common_mod.api_error(ApiClientError("DENIED", "no"))
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "DENIED"


# ---------------------------------------------------------------------------
# http_get
# ---------------------------------------------------------------------------


def test_http_get_success(monkeypatch):
    class _Resp:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b'{"ok": true}'
    calls = {}
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout: calls.setdefault("n", []).append(url) or _Resp())
    code, body = common_mod.http_get(8080, "/api/status")
    assert code == 200
    assert body == {"ok": True}
    assert calls["n"] == ["http://127.0.0.1:8080/api/status"]


def test_http_get_nonjson_body(monkeypatch):
    class _Resp:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b"not json"
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: _Resp())
    code, body = common_mod.http_get(8080, "/")
    assert code == 200 and body is None


def test_http_get_http_error(monkeypatch):
    class _HTTPError(Exception):
        code = 503
    # urllib.error.HTTPError is an exception with .code
    monkeypatch.setattr("urllib.error.HTTPError", _HTTPError)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout: (_ for _ in ()).throw(_HTTPError()))
    code, body = common_mod.http_get(8080, "/")
    assert code == 503 and body is None


def test_http_get_transport_error(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, timeout: (_ for _ in ()).throw(OSError("refused")))
    code, body = common_mod.http_get(8080, "/")
    assert code == -1 and body is None


# ---------------------------------------------------------------------------
# level_int / token / pid helpers
# ---------------------------------------------------------------------------


def test_level_int_none_is_info():
    import logging
    assert common_mod.level_int(None) == logging.INFO
    assert common_mod.level_int("debug") == logging.DEBUG
    assert common_mod.level_int("error") == logging.ERROR


def test_read_web_token_missing(tmp_path):
    config = _config(tmp_path)
    assert common_mod.read_web_token(config) is None


def test_read_web_token_present(tmp_path):
    config = _config(tmp_path)
    from synapse.cli.common import run_dir
    d = run_dir(config)
    import os
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "web_token")
    with open(path, "w", encoding="ascii") as fh:
        fh.write("secret-token\n")
    assert common_mod.read_web_token(config) == "secret-token"


def test_read_web_token_empty_file(tmp_path):
    config = _config(tmp_path)
    from synapse.cli.common import run_dir
    import os
    d = run_dir(config)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "web_token"), "w", encoding="ascii").close()
    assert common_mod.read_web_token(config) is None


def test_pid_file_remove(tmp_path):
    config = _config(tmp_path)
    common_mod.write_pid_file(config, "web", {"port": 1})
    assert common_mod.read_pid_file(config, "web")["port"] == 1
    common_mod.remove_pid_file(config, "web")
    assert common_mod.read_pid_file(config, "web") is None
    # removing a missing file is a no-op
    common_mod.remove_pid_file(config, "web")


def test_read_pid_file_corrupt(tmp_path):
    config = _config(tmp_path)
    from synapse.cli.common import pid_file_path
    import os
    d = os.path.dirname(pid_file_path(config, "x"))
    os.makedirs(d, exist_ok=True)
    open(pid_file_path(config, "x"), "w", encoding="utf-8").write("{corrupt")
    assert common_mod.read_pid_file(config, "x") is None
    # non-dict JSON
    open(pid_file_path(config, "x"), "w", encoding="utf-8").write("[1,2]")
    assert common_mod.read_pid_file(config, "x") is None


def test_pid_alive_current_process():
    import os
    assert common_mod.pid_alive(os.getpid()) is True
    assert common_mod.pid_alive(99999999) is False


# ---------------------------------------------------------------------------
# normalize_datetime / now_iso
# ---------------------------------------------------------------------------


def test_normalize_datetime_variants():
    assert common_mod.normalize_datetime(None) is None
    assert common_mod.normalize_datetime("2026-08-11T10:00:00Z") == \
        "2026-08-11T10:00:00.000Z"
    assert common_mod.normalize_datetime("2026-08-11T10:00:00.123Z") == \
        "2026-08-11T10:00:00.123Z"


def test_now_iso_format():
    from datetime import datetime
    parsed = datetime.strptime(common_mod.now_iso(), "%Y-%m-%dT%H:%M:%S.%fZ")
    assert parsed.tzinfo is None  # produced from a UTC datetime, Z suffix


def _args(json):
    return type("A", (), {"json": json})()


def _config(tmp_path):
    from synapse.config import Config
    conf = {
        "storage_dir": str(tmp_path / "d"),
        "socket_path": str(tmp_path / "s.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "backups"),
    }
    return Config.from_dict(conf)
