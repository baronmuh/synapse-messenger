"""Accelerated JSON serialization: orjson if available, stdlib otherwise.

orjson is measured ~10× faster than the stdlib on response shapes
de Synapse (docs/PERFORMANCE.md §13) : pages de messages, notifications,
``help`` documentation. Its use is deliberately limited to
**serialization** (``dumps``):

* outputs are equivalent for Synapse types (dicts, lists,
  str, int, bool, None — no NaN/Infinity); ``orjson.dumps`` returns
  directly compact UTF-8 bytes (ideal for the socket);
* le **parsing** (``loads``) reste sur la stdlib : il applique le hook
  ``_reject_duplicate_keys`` (duplicate rejection, security requirement) that
  orjson ne supporte pas (orjson accepte les doublons, dernier gagnant) ;
  parsing costs ~9 µs vs ~90 µs for page serialization —
  le gain est bien sur ``dumps``.

The stdlib is the transparent fallback if orjson is not installed.
"""

from __future__ import annotations

import json as _stdlib_json
from typing import Any

try:  # pragma: no cover - branche selon l'environnement
    import orjson as _orjson
except ImportError:  # pragma: no cover - fallback without the dependency
    _orjson = None


def dumps(obj: Any) -> bytes:
    """Serializes ``obj`` into compact UTF-8 JSON bytes (stdlib-equivalent
    ``ensure_ascii=False, separators=(",", ":")``)."""
    if _orjson is not None:
        return _orjson.dumps(obj)
    return _stdlib_json.dumps(
        obj, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
