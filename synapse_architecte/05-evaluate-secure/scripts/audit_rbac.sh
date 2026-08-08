#!/usr/bin/env bash
# Grep audit for agent-facing skills: reserved-command calls, admin
# tools, and credential patterns must be ABSENT.
# Usage: bash scripts/audit_rbac.sh <skill-directory> [extra-patterns...]
# Exits 0 only when no occurrence is found.
set -uo pipefail

TARGET="${1:?usage: audit_rbac.sh <skill-directory> [patterns...]}"
if [ ! -d "$TARGET" ]; then
  echo "FAIL: not a directory: $TARGET" >&2
  exit 2
fi

# Reserved/admin command CALLS (Synapse project) — adjust to the project.
RESERVED_CALLS=(
  'create_org(' 'create_agent(' 'disable_org(' 'set_organization_policy('
  'synapse org init' 'synapse agent create' 'synapse server start'
  'synapse backup' 'synapse web ' 'synapse diag' 'synapse update apply'
)

# Admin tools / binaries.
ADMIN_TOOLS=(
  'synapse-server' 'synapse-init-org' 'backup.key' '/etc/synapse'
)

# Credential hygiene patterns.
CRED_PATTERNS=(
  'SYNAPSE_USERNAME' 'SYNAPSE_PASSWORD' 'SYNAPSE_SOCKET'
  '--password [^s]' 'password=[A-Za-z0-9]'
)

found=0
check() {
  local label="$1"; shift
  local hits
  hits=$(grep -rnE -- "$@" "$TARGET" 2>/dev/null || true)
  if [ -n "$hits" ]; then
    echo "FOUND [$label]:" >&2
    echo "$hits" | head -10 >&2
    found=1
  fi
}

for pat in "${RESERVED_CALLS[@]}"; do
  check "reserved-call: $pat" "$pat"
done
for pat in "${ADMIN_TOOLS[@]}"; do
  check "admin-tool: $pat" "$pat"
done
for pat in "${CRED_PATTERNS[@]}"; do
  check "cred-pattern: $pat" "$pat"
done

if [ "$found" -eq 0 ]; then
  echo "OK: no reserved/admin/credential patterns in $TARGET"
  exit 0
fi
echo "FAIL: audit found issues (see above)" >&2
exit 1
