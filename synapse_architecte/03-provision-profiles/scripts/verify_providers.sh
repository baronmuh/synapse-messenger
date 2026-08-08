#!/usr/bin/env bash
# Verifies that a Hermes profile inherited the Architect's providers and
# can reach the LLM.
# Usage: bash scripts/verify_providers.sh <profile> [parent-profile]
# The parent defaults to the current profile (HERMES_HOME basename) when
# not given. Exits 0 only with: matching model/provider + a real answer.
set -euo pipefail

PROFILE="${1:?usage: verify_providers.sh <profile> [parent-profile]}"
PARENT="${2:-$(basename "${HERMES_HOME:-$HOME/.hermes/profiles/synapse-architect}")}"

echo "== 1. Model/Provider =="
child=$(hermes profile show "$PROFILE" 2>&1 | grep -E "^Model:" || true)
parent=$(hermes profile show "$PARENT" 2>&1 | grep -E "^Model:" || true)
echo "  child : ${child:-MISSING}"
echo "  parent: ${parent:-MISSING}"
if [ "$child" != "$parent" ]; then
  echo "FAIL: model/provider differ from parent" >&2
  exit 1
fi

echo "== 2. auth.json (0600) =="
AUTH="$HOME/.hermes/profiles/$PROFILE/auth.json"
if [ ! -f "$AUTH" ]; then
  echo "FAIL: auth.json missing in $PROFILE" >&2
  exit 1
fi
mode=$(stat -c '%a' "$AUTH" 2>/dev/null || stat -f '%Lp' "$AUTH" 2>/dev/null)
echo "  mode: $mode"
if [ "$mode" != "600" ]; then
  echo "FAIL: auth.json must be 0600 (got $mode)" >&2
  exit 1
fi

echo "== 3. Live LLM query =="
answer=$(timeout 120 hermes -p "$PROFILE" chat -q "Reply with exactly: PROVISION-OK" 2>&1 \
  | grep "PROVISION-OK" | head -1 || true)
if [ -z "$answer" ]; then
  echo "FAIL: no real LLM answer from profile $PROFILE" >&2
  exit 1
fi
echo "  $answer"

echo "OK: profile $PROFILE has working providers"
