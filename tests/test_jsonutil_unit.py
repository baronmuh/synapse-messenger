"""Unit coverage for ``synapse/jsonutil.py`` accelerated serialization.

Tests both the orjson path (when available) and the stdlib fallback branch
by monkeypatching the module-level serializer reference.
"""

from __future__ import annotations

from synapse import jsonutil


def test_dumps_orjson_path(monkeypatch):
    fake = type("F", (), {"dumps": staticmethod(lambda obj: b"<orjson>")})
    monkeypatch.setattr(jsonutil, "_orjson", fake)
    assert jsonutil.dumps({"a": 1}) == b"<orjson>"


def test_dumps_stdlib_fallback(monkeypatch):
    monkeypatch.setattr(jsonutil, "_orjson", None)
    out = jsonutil.dumps({"msg": "héllo", "n": 5})
    assert isinstance(out, bytes)
    # ensure_ascii=False + compact separators
    assert b"h\xc3\xa9llo" in out
    assert b'{"msg":' in out
    assert b": 5}" not in out  # compact separators (no space)


def test_dumps_none_orjson_absent_bytes():
    # When orjson IS present, output must still be UTF-8 bytes and
    # equivalent in content.
    out = jsonutil.dumps([1, "x", None])
    assert isinstance(out, bytes)
    assert b"[1,\"x\",null]" in out
