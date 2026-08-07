#!/usr/bin/env bash
# Installe le hook git pre-push : gate de la branche main (SPEC_PRODUCTION
# §2) — no push without a green full suite.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_DIR="$REPO/.git/hooks"
HOOK="$HOOK_DIR/pre-push"

if [[ ! -d "$REPO/.git" ]]; then
    echo "error: $REPO is not a git repository" >&2
    exit 1
fi

mkdir -p "$HOOK_DIR"

cat > "$HOOK" <<'EOF'
#!/usr/bin/env bash
# main branch gate (SPEC_PRODUCTION §2): full suite before push.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
echo "==> pre-push: local CI (full suite)…"
if ! "$REPO/scripts/ci.sh"; then
    echo "==> pre-push REFUSED: the local CI failed (see above)" >&2
    exit 1
fi
echo "==> pre-push: CI green, push allowed"
EOF

chmod +x "$HOOK"
echo "pre-push hook installed: $HOOK"
