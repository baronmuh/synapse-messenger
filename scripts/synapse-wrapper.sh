#!/usr/bin/env bash
# synapse-wrapper — makes the user-install CLI work from ANY terminal.
#
# Install as ~/.local/bin/synapse (that directory must be first in PATH).
# Without it, the CLI only finds the user config when the shell happens to
# export SYNAPSE_CONFIG / Synapse_CONFIG (e.g. via .bashrc) — an old or
# non-login terminal falls back to the system config and `server start`
# fails. The wrapper fills in the user config only when nothing is set; an
# explicit --config or an existing SYNAPSE_CONFIG / Synapse_CONFIG is
# always honored.
#
# Env overrides:
#   SYNAPSE_BASE   install root (default: ~/.local/share/synapse)
set -u

BASE="${SYNAPSE_BASE:-$HOME/.local/share/synapse}"
if [ -z "${Synapse_CONFIG:-}" ] && [ -z "${SYNAPSE_CONFIG:-}" ]; then
    export Synapse_CONFIG="$BASE/config.json"
fi
exec "$BASE/venv/bin/synapse" "$@"
