"""Single source of truth for the project version (One-Version Rule).

The version lives in exactly one place: ``pyproject.toml``
(``[project] version``). Everything else derives from it:

- installed package: ``importlib.metadata.version("synapse-messenger")``
- development tree (package not installed): the ``pyproject.toml`` of the
  repository is read and parsed — no hard-coded copy anywhere.

Changing the version = editing ``pyproject.toml`` only. Nothing else
stores or duplicates the number.
"""

from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

_PACKAGE = "synapse-messenger"

# ``pyproject.toml`` at the repository root (next to this package).
_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _from_pyproject() -> str:
    """Reads the version declared in ``pyproject.toml``."""
    try:
        text = _PYPROJECT.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - packaging without the repo
        return ""
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else ""


def project_version() -> str:
    """The current project version.

    Priority: the installed package metadata, then the repository
    ``pyproject.toml``. Raises nothing: on total failure an empty string
    is returned (callers treat it as "unknown").
    """
    try:
        return importlib.metadata.version(_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return _from_pyproject()
