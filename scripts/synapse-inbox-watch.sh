#!/usr/bin/env bash
# synapse-inbox-watch.sh — inbox watchdog for a Synapse agent.
#
# Every agent of the organization MUST watch its own synapse inbox: a
# mailbox that nobody reads breaks coordination (messages from peers and
# from the orchestrator go unanswered). This script is the deterministic
# watchdog: it checks the agent's unread count and, ONLY when unread
# messages exist, spawns the agent's Hermes profile session to handle them.
# Quiet ticks cost nothing (one cheap CLI call, no LLM session).
#
# Usage:
#   synapse-inbox-watch.sh <agent_name> [--check]
#     <agent_name>  synapse username (== Hermes profile name in nova_mycelium)
#     --check       only report the unread count; do NOT spawn the agent
#
# Conventions (must hold, see INSTALL-agent.md step 12):
#   - $SYNAPSE_CONFIG points to the synapse config (exported in ~/.bashrc).
#   - The agent's password file is ~/.secrets/agents/<agent_name>.pass (0600).
#     It is read via stdin only, never printed, never put in argv.
#   - Cron cadence: every 5 minutes (adjustable). One job per agent.
#
# Behaviour:
#   - 0 unread messages  -> silent exit (0), nothing spawned.
#   - unread > 0         -> log the tick, spawn the agent session detached
#                           (setsid + nohup) so the cron time limit cannot
#                           kill the reply, print a one-line notice.
#
# The spawned session instructs the agent to: read the unread inbox, reply
# in English to messages that need an answer, NEVER write to the human
# account (nova_mycelium_humain — human communication belongs exclusively
# to the orchestrator; a human message is escalated, not answered), and
# mark every handled message as read (`synapse message read <id>`) so the
# next tick is silent again.

set -u -o pipefail

AGENT="${1:-}"
MODE="${2:-run}"

if [ -z "$AGENT" ]; then
  echo "usage: $0 <agent_name> [--check]" >&2
  exit 2
fi

SYNAPSE_BIN="${SYNAPSE_BIN:-$(command -v synapse || echo "$HOME/.local/bin/synapse")}"
# Must be EXPORTED: the synapse CLI reads it from the environment; the cron
# environment does not carry it (and a global ~/.bashrc export would break
# the project's hermetic test suite — see INSTALL-agent.md Step 12).
export SYNAPSE_CONFIG="${SYNAPSE_CONFIG:-$HOME/.local/share/synapse/config.json}"
PASS_FILE="$HOME/.secrets/agents/${AGENT}.pass"
STATE_DIR="$HOME/.local/state/synapse-inbox-watch"
LOG_FILE="$STATE_DIR/${AGENT}.log"

if [ ! -x "$SYNAPSE_BIN" ]; then
  echo "$(ts) error: synapse CLI not found for '$AGENT' (SYNAPSE_BIN=$SYNAPSE_BIN)" >> "$LOG_FILE"
  echo "error: synapse CLI not found (set SYNAPSE_BIN)" >&2
  exit 1
fi
if [ ! -f "$PASS_FILE" ]; then
  echo "error: password file $PASS_FILE missing (must be 0600)" >&2
  exit 1
fi
if [ "$(stat -c '%a' "$PASS_FILE" 2>/dev/null)" != "600" ]; then
  echo "error: $PASS_FILE must be 0600" >&2
  exit 1
fi
mkdir -p "$STATE_DIR"

ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }

# Count unread messages via the machine-readable notifications endpoint.
# A failed API call must NEVER be treated as "0 unread" (a message could be
# missed silently) — on failure we exit 1 so the cron job records an error
# alert (the no_agent watchdog pattern: a broken watchdog must not fail
# silently).
UNREAD_JSON="$(printf '%s\n' "$(cat "$PASS_FILE")" \
  | "$SYNAPSE_BIN" message notifications --json \
      --my-name "$AGENT" --password-stdin 2>/dev/null)"
PARSE="$(printf '%s' "$UNREAD_JSON" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    ok = bool(d.get("success"))
    u = ((d.get("data") or {}).get("unread_by_sender") or {}) if ok else {}
    n = sum(int(v) for v in u.values() if str(v).isdigit())
except Exception:
    ok, n = False, 0
print(f"{int(ok)} {n}")
' 2>/dev/null)"
OK="${PARSE%% *}"
UNREAD="${PARSE##* }"
UNREAD="${UNREAD:-0}"

if [ "$OK" != "1" ]; then
  echo "$(ts) error: notifications API call failed for '$AGENT' (not counted as empty)" >> "$LOG_FILE"
  echo "error: could not read unread count for '$AGENT' (API failure)" >&2
  exit 1
fi

if [ "$MODE" = "--check" ]; then
  echo "$UNREAD"
  exit 0
fi

if [ "$UNREAD" -le 0 ]; then
  exit 0  # silent tick — no unread, nothing to do
fi

echo "$(ts) tick: $UNREAD unread message(s) for '$AGENT' — spawning agent session" >> "$LOG_FILE"

PROMPT="You are the Hermes agent of profile '$AGENT' (synapse username '$AGENT', organization nova_mycelium). You have $UNREAD unread synapse message(s). Handle them NOW, and nothing else:
1) Read them: export SYNAPSE_CONFIG=\"\$HOME/.local/share/synapse/config.json\"; printf '%s\\n' \"\$(cat \$HOME/.secrets/agents/$AGENT.pass)\" | synapse message inbox --unread --my-name $AGENT --password-stdin
2) For each message that needs a response, reply in ENGLISH (organization rule): printf '%s\\n' \"\$(cat \$HOME/.secrets/agents/$AGENT.pass)\" | synapse message send <sender> '<reply>' --my-name $AGENT --password-stdin
3) NEVER send anything to nova_mycelium_humain (the human). If a message is from the human account, do NOT reply to it; post a short note to the 'orchestration' synapse group so the orchestrator picks it up.
4) After handling each message, mark it read: printf '%s\\n' \"\$(cat \$HOME/.secrets/agents/$AGENT.pass)\" | synapse message read <message_id> --my-name $AGENT --password-stdin (message ids are in the inbox JSON output).
5) Keep this short. Do not start any other work. Do not modify code."

LOG_AGENT="$STATE_DIR/${AGENT}.session.log"
setsid nohup hermes -p "$AGENT" chat -q "$PROMPT" -Q >> "$LOG_AGENT" 2>&1 < /dev/null &
echo "$(ts) spawned pid $! (session log: $LOG_AGENT)" >> "$LOG_FILE"
echo "spawned $AGENT session for $UNREAD unread message(s)"
exit 0
