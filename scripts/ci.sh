#!/usr/bin/env bash
# CI locale Synapse (SPEC_PRODUCTION §2).
#
# Canonical pipeline: reusable dedicated venv → dependency lock check
# (§7) → full test suite. Used by the git pre-push hook and the
# pre-push (gate de la branche main) et par le timer systemd nocturne
# nightly timer (regression net without pushes).
#
# La CI travaille exclusivement sur des configurations temporaires (les
# tests create their own storages): it NEVER touches the
# configuration, au stockage, au socket ou aux journaux de production.
#
# Variables d'environnement :
#   SYNAPSE_CI_VENV          venv location (default: ~/.cache/synapse-ci/venv)
#   SYNAPSE_CI_PYTEST_ARGS   pytest arguments (default: tests/ -q -n ${SYNAPSE_CI_WORKERS:-3})
#   SYNAPSE_CI_WORKERS       pytest-xdist workers (default: 3 — the suite is
#                            parallelizable: random ports, tmp_path,
#                            hacheur rapide en session par worker)
#   SYNAPSE_CI_FORCE_FRESH   1 = venv 100 % frais (certification release)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${SYNAPSE_CI_VENV:-$HOME/.cache/synapse-ci/venv}"
PYTEST_ARGS="${SYNAPSE_CI_PYTEST_ARGS:-tests/ -q -n ${SYNAPSE_CI_WORKERS:-3}}"
FORCE_FRESH="${SYNAPSE_CI_FORCE_FRESH:-0}"

cd "$REPO"

echo "==> CI Synapse ($(date -u +%Y-%m-%dT%H:%M:%SZ))"

if [[ "$FORCE_FRESH" == "1" ]]; then
    echo "==> fresh venv requested: removing $VENV_DIR"
    rm -rf "$VENV_DIR"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "==> creating the CI venv: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -e ".[dev]"
fi

echo "==> checking the dependency lock (§7)"
"$REPO/scripts/check_lock.sh"

echo "==> test suite: pytest $PYTEST_ARGS"
# shellcheck disable=SC2086
"$VENV_DIR/bin/python" -m pytest $PYTEST_ARGS

echo "==> CI done: green suite"
