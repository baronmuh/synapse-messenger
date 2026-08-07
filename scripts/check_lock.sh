#!/usr/bin/env bash
# Checks that the direct dependencies of pyproject.toml are present
# in requirements.lock (SPEC_PRODUCTION §7) — a forgotten dependency
# bump is detected before the push.
set -euo pipefail

REPO="${SYNAPSE_LOCK_CHECK_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$REPO"

"${PYTHON:-python3}" - <<'PYEOF'
import re
import sys
from pathlib import Path

pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
lock = Path("requirements.lock").read_text(encoding="utf-8").lower()

# Direct dependencies of [project.dependencies]: "name>=version" lines.
deps = re.findall(r'^\s*"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*[<>=!~]',
                  pyproject, re.M)
missing = [d for d in deps if d.lower() not in lock]
if missing:
    print(f"check-lock: dependency(ies) missing from requirements.lock: "
          f"{', '.join(missing)}", file=sys.stderr)
    print("Regenerate the lock: pip-compile --generate-hashes "
          "-o requirements.lock pyproject.toml", file=sys.stderr)
    sys.exit(1)
if not lock.strip():
    print("check-lock : requirements.lock vide", file=sys.stderr)
    sys.exit(1)
print(f"check-lock: {len(deps)} direct dependenc(y/ies) locked")
PYEOF
