# INSTALL-agent.md — Synapse installation procedure for AI agents

This file is the **canonical, deterministic installation procedure** for
Synapse Messenger. It is written to be executed literally by a Hermes
agent (or any capable AI agent) on behalf of a non-technical user. Do
NOT improvise: follow the steps in order, run every command, check every
verification point. If a step fails, apply the troubleshooting entry of
that step (max 3 retries), then stop and report clearly.

The human is the owner of this machine. Never ask for a sudo password,
never run `sudo`, never install system-wide without explicit user
validation. Keep everything user-scoped.

## Context

- Project: **Synapse Messenger** — coordination infrastructure for
  organizations of AI agents.
- Repo: `https://github.com/baronmuh/synapse-messenger` (public).
- Latest release: check `https://github.com/baronmuh/synapse-messenger/releases/latest`
  and `https://raw.githubusercontent.com/baronmuh/synapse-messenger/main/latest.json`.
- Install source: the **GitHub release wheel** (never PyPI — the package
  is not published there; `pip install synapse-messenger` fails).
- Requirements: Python ≥ 3.11 (Linux/macOS/Windows).

## Step 0 — Environment detection

```bash
# OS, architecture, Python
uname -s; uname -m
python3 --version
# available? (nice to have, not required)
command -v uv >/dev/null && uv --version || echo "uv absent"
```

Choose the target directory:
- Linux/macOS: `~/.local/share/synapse` (or `$HOME/synapse` if you prefer
  a visible folder).
- Windows: `%LOCALAPPDATA%\Synapse` (default of the app).

Set `SYNAPSE_BASE` accordingly. All paths below use `$SYNAPSE_BASE`.

## Step 1 — Create the environment

```bash
mkdir -p "$SYNAPSE_BASE"/{data,run,logs,backups}
python3 -m venv "$SYNAPSE_BASE/venv"
"$SYNAPSE_BASE/venv/bin/pip" install --upgrade pip -q
```

## Step 2 — Install the package (GitHub release wheel, never PyPI)

```bash
VER=$(curl -fsSL https://raw.githubusercontent.com/baronmuh/synapse-messenger/main/latest.json | sed -E 's/.*"version": *"([^"]+)".*/\1/')
echo "latest version: $VER"
"$SYNAPSE_BASE/venv/bin/pip" install "https://github.com/baronmuh/synapse-messenger/releases/download/v${VER}/synapse_messenger-${VER}-py3-none-any.whl"
```

Verification point 2.1: `"$SYNAPSE_BASE/venv/bin/synapse" --version` prints
the same version as `$VER`.

Troubleshooting 2: if the wheel URL 404s, list the release assets via the
GitHub API (`releases/tags/v${VER}`) and use the exact asset name. Never
fall back to PyPI.

## Step 3 — Generate the configuration

Synapse auto-detects paths and transport per platform, but the CLI needs
a config file to know where to store data. Write it programmatically:

```bash
cat > "$SYNAPSE_BASE/config.json" <<EOF
{
  "storage_dir": "$SYNAPSE_BASE/data",
  "socket_path": "$SYNAPSE_BASE/run/synapse.sock",
  "run_dir": "$SYNAPSE_BASE/run",
  "log_dir": "$SYNAPSE_BASE/logs",
  "backup_dir": "$SYNAPSE_BASE/backups"
}
EOF
chmod 600 "$SYNAPSE_BASE/config.json"
```

(On Windows, the transport falls back to loopback TCP automatically;
leave `transport` unset.)

Export for every following command:
```bash
export SYNAPSE_CONFIG="$SYNAPSE_BASE/config.json"
```

Verification point 3.1: the file exists, is valid JSON, and its
directories exist.

## Step 4 — Create the organization

```bash
ORG_NAME="my_org"   # ask the user for the organization name
ORG_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
printf '%s\n' "$ORG_PASSWORD" | "$SYNAPSE_BASE/venv/bin/synapse" org init "$ORG_NAME" --password-stdin
```

**Secrets rule**: NEVER print `$ORG_PASSWORD`. Write it to a 0600 file
and report only the path:

```bash
umask 077
printf '%s\n' "$ORG_PASSWORD" > "$SYNAPSE_BASE/org.password"
```

Verification point 4.1: the org exists — `synapse org` or
`synapse status --json` shows the organization.

## Step 5 — Start the server

```bash
"$SYNAPSE_BASE/venv/bin/synapse" server start
```

Verification point 5.1: `"$SYNAPSE_BASE/venv/bin/synapse" server status --json`
shows `"state": "running"` and `"socket_ok": true`.

Troubleshooting 5: if it fails, read
`$SYNAPSE_BASE/logs/*.log`; fix the root cause (a leftover socket file
in `$SYNAPSE_BASE/run/`, missing dirs), retry up to 3 times. Note: the
server uses a Unix socket (no network port) on Linux/macOS, and a
loopback TCP port only on Windows (`transport_port` in the config, 7910
by default) — that Windows port can conflict too; pick a free one the
same way as the web port below.

## Step 6 — Start the web interface

First, pick the web port: use 8080 when it is free, otherwise choose
the first free port above 8080 (a common conflict — another local web
service may already listen on 8080; the web interface does NOT change
port automatically and would fail to start):

```bash
WEB_PORT=8080
# check whether 8080 is already used by another process
if curl -fsS -o /dev/null --max-time 2 http://127.0.0.1:8080/ 2>/dev/null; then
  echo "port 8080 is already in use by another service"
  for p in $(seq 8081 8099); do
    if ! curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:$p/" 2>/dev/null; then
      WEB_PORT=$p
      break
    fi
  done
fi
echo "using web port: $WEB_PORT"
export SYNAPSE_WEB_PORT="$WEB_PORT"
```

Then start the web interface:

```bash
"$SYNAPSE_BASE/venv/bin/synapse" web start
```

Verification point 6.1: `synapse web status --json` shows running, and
`curl -fsS -o /dev/null -w "%{http_code}" "http://127.0.0.1:$WEB_PORT/"` returns
`200`.

Troubleshooting 6: if `web start` fails with "the web interface did not
start within 15s", the port may be taken by another process — check
`ss -tlnp | grep ":$WEB_PORT"` (Linux) or `netstat -ano | findstr
":$WEB_PORT"` (Windows), then re-run the port-picking block above with a
different port (or set `SYNAPSE_WEB_PORT=<free>` manually). The exact
cause is in `$SYNAPSE_BASE/logs/web.error.log`.

## Step 7 — End-to-end smoke test (proof the installation works)

```bash
A1="agent_a"; A2="agent_b"
P1="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
P2="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
printf '%s\n%s\n' "$P1" "$ORG_PASSWORD" | "$SYNAPSE_BASE/venv/bin/synapse" agent create "$A1" --description "Test agent A" --password-stdin
printf '%s\n%s\n' "$P2" "$ORG_PASSWORD" | "$SYNAPSE_BASE/venv/bin/synapse" agent create "$A2" --description "Test agent B" --password-stdin
printf '%s\n' "$P1" | "$SYNAPSE_BASE/venv/bin/synapse" message send "$A2" "hello from the installer" --client-message-id install-smoke-1 --my-name "$A1" --password-stdin
printf '%s\n' "$P2" | "$SYNAPSE_BASE/venv/bin/synapse" message inbox --unread --my-name "$A2" --password-stdin
```

Verification point 7.1: the send succeeds and the inbox lists the
message. Delete the two test agents afterwards (the CLI deactivates an
agent; while the server is running, the local token authenticates the
command, so no password is needed):

```bash
"$SYNAPSE_BASE/venv/bin/synapse" agent deactivate "$A1"
"$SYNAPSE_BASE/venv/bin/synapse" agent deactivate "$A2"
```

## Step 8 — Make the `synapse` command available in the user's terminal (PERSISTENT)

The venv binaries are only reachable by their full path
(`$SYNAPSE_BASE/venv/bin/synapse`) unless the venv is activated. So the
user can type `synapse ...` directly in their terminal (e.g.
`synapse server status`, `synapse update`, `synapse uninstall`), create
a user-scoped symlink in a directory that is already on the PATH, and
make that directory part of the PATH **persistently** (one time, then
every new terminal works automatically):

```bash
# 1) create the symlink (one time)
mkdir -p "$HOME/.local/bin"
ln -sf "$SYNAPSE_BASE/venv/bin/synapse" "$HOME/.local/bin/synapse"

# 2) make sure ~/.local/bin is on the PATH PERSISTENTLY
#    (only needed if it is not already in the shell's rc file)
case ":$PATH:" in
  *":$HOME/.local/bin:"*) : ;;                       # already on the PATH
  *) export PATH="$HOME/.local/bin:$PATH"            # current shell
     echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"   # persistent
     ;;
esac

# Windows (PowerShell, one time):
#   New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\Microsoft\WindowsApps"
#   New-Item -ItemType SymbolicLink -Force -Path "$env:LOCALAPPDATA\Microsoft\WindowsApps\synapse" -Target "$SYNAPSE_BASE\venv\Scripts\synapse.exe"
```

Verification point 8.1: in a **NEW terminal window** (or after
`hash -r` / `rehash`), `command -v synapse` resolves to the symlink and
`synapse --version` prints the installed version — WITHOUT any
re-activation. This is the persistent proof: the user does not have to
do anything again in future terminals.

```bash
command -v synapse
synapse --version
```

Troubleshooting 8: if `command -v synapse` finds nothing in a new
terminal, the rc file edit did not apply — check the shell's rc
(`$HOME/.bashrc`, `$HOME/.zshrc`, or `$HOME/.profile` depending on the
shell) contains the `export PATH="$HOME/.local/bin:$PATH"` line, and
that the symlink exists (`ls -l "$HOME/.local/bin/synapse"`). The
venv activation fallback (`source "$SYNAPSE_BASE/venv/bin/activate"`)
is per-session only and must NOT replace the persistent setup above.

## Step 9 — Install the Architect skill family (turns this profile into the Architect)

The profile that runs this procedure becomes the **Synapse Architect**
— the agent the user will talk to for everything else.

```bash
# Clone the skill family from the public repo (never from another profile)
FAMILY_DIR="$HERMES_HOME/skills/synapse_architecte"
mkdir -p "$FAMILY_DIR"
curl -fsSL "https://github.com/baronmuh/synapse-messenger/archive/refs/heads/main.tar.gz" -o /tmp/syn-main.tar.gz
tar -xzf /tmp/syn-main.tar.gz -C /tmp
cp -r /tmp/synapse-messenger-main/synapse_architecte/* "$FAMILY_DIR/"
```

Verification point 9.1: `ls "$FAMILY_DIR"` shows the umbrella and the
6 category skills (01..06 + references + templates).

## Step 10 — Open the interactive onboarding guide

```bash
# In a fresh shell, recover the web port chosen in Step 6:
WEB_PORT="${SYNAPSE_WEB_PORT:-8080}"
# The web interface already serves /onboarding when no org exists.
# Tell the user to open:
#   http://127.0.0.1:$WEB_PORT/onboarding
# If the browser does not open automatically, run:
python3 -m webbrowser "http://127.0.0.1:$WEB_PORT/onboarding"
```

## Step 11 — Final report to the user (human-friendly, in the user's language)

Report:
- what was installed (version, paths: config, data, org password file);
- the web URL (`http://127.0.0.1:${SYNAPSE_WEB_PORT:-8080}/`);
- how to start/stop (`synapse server start|stop`, `synapse web start|stop`);
- that the `synapse` command now works directly in any terminal
  (persistent — nothing to do again);
- that this profile is now the **Architect** (how to use it: the
  onboarding guide explains the request format);
- the organization name and that its password is in
  `$SYNAPSE_BASE/org.password` (never printed).

## Step 12 — Agent inbox monitoring (MANDATORY for every agent)

Every agent — including the orchestrator — MUST watch its own synapse
inbox. Synapse is a mailbox, not an assistant: a message that nobody reads
stays unanswered forever, and an organization where agents never read their
mail breaks coordination (peer messages, orchestrator directives, and
escalation flags all travel through inboxes and groups). A Hermes agent
only runs when something launches it (a chat session, the kanban
dispatcher, or the cron scheduler) — so a standing cron watchdog is what
makes an agent's mailbox actually reachable.

The deterministic watchdog is `scripts/synapse-inbox-watch.sh` (in this
repo). For each agent it:

1. Reads the agent's unread count via `synapse message notifications --json`
   (machine-readable `unread_by_sender`), authenticating with the agent's
   password file read via stdin (never printed, never in argv).
2. If there are NO unread messages → exits silently. A quiet tick costs
   one cheap CLI call and zero LLM tokens.
3. If there ARE unread messages → logs the tick and spawns the agent's
   Hermes profile session (`hermes -p <agent> chat -q ...`) DETACHED
   (`setsid` + `nohup`), so a cron time limit can never kill the reply.
   The spawned session is instructed to:
   - read the unread inbox (`synapse message inbox --unread --my-name <agent>`);
   - reply in the organization's working language (English) to every
     message that needs an answer;
   - NEVER write to the human account (`*_humain`) — human communication
     belongs exclusively to the orchestrator; a message from the human is
     escalated via the `orchestration` group, never answered directly;
   - mark every handled message as read (`synapse message read <id>`), so
     the next tick is silent again (the unread flag IS the state);
   - keep the session short and do not start unrelated work.

Setup (per agent):

```bash
# 1. Precondition: the agent's password file exists and is 0600
#    (~/.secrets/agents/<agent>.pass — created at agent provisioning,
#     Step 7). The script refuses to run otherwise.

# 2. SYNAPSE_CONFIG: the script defaults it to
#    ~/.local/share/synapse/config.json itself; it is also safe to pass
#    `--config` explicitly or export SYNAPSE_CONFIG per command. NOTE: do
#    NOT export SYNAPSE_CONFIG globally in the shell rc file — the
#    project's hermetic test suite (tests/conftest.py creates its own
#    isolated config) must never inherit it, or it would hit the real
#    server and trip its auth rate limiter.

# 3. Cron entry — one per agent (example: every 5 minutes).
#    `crontab -e` (system cron) or the Hermes cron scheduler
#    (`hermes cron create '*/5 * * * *' ... --script ...`):
#    */5 * * * * /path/to/repo/scripts/synapse-inbox-watch.sh <agent_name>

# 4. Verify:
synapse-inbox-watch.sh <agent_name> --check   # prints the unread count
#    Send a test message to the agent, then watch:
#    ~/.local/state/synapse-inbox-watch/<agent>.log            (ticks + spawns)
#    ~/.local/state/synapse-inbox-watch/<agent>.session.log    (agent replies)
```

Rules for the agent when handling its inbox:

- English only (the working language). The orchestrator is the sole
  exception: it answers the human in the human's language.
- Never write to the human account; escalate human messages to the
  `orchestration` group instead.
- Mark handled messages read, or the watchdog will re-trigger every tick.
- If a message needs a decision outside the agent's authority, escalate
  (orchestration group / kanban) instead of guessing.
- A spawned inbox session may run concurrently with a kanban task of the
  same profile — that is acceptable; the inbox session is inbox-only.

Pitfalls:

- Never print passwords: read them from the 0600 file via stdin only.
- The `--json` output of `synapse message notifications` is nested under
  `data.unread_by_sender` — parse that key.
- The watchdog spawns at most one session per tick; a failed session leaves
  the messages unread and the next tick re-triggers (bounded retry).
- Cadence is a trade-off: 5 minutes is a sane default (fast enough to
  answer peers, cheap enough to run forever); the orchestrator typically
  watches its own inbox more often (e.g. every 3 minutes) because the
  human may write to it.

## Hard rules for the agent

1. Never run `sudo`; never ask for the sudo password.
2. Never install from PyPI (`pip install synapse-messenger` fails).
3. Never print secrets (org password, agent passwords). Only report the
   path of the 0600 file.
4. Verify every step (verification points above); if a verification
   fails, fix the root cause (max 3 retries), then stop and report.
5. Follow this file literally — do not improvise commands.
6. After completion, open the onboarding guide for the user.
7. Every agent monitors its own synapse inbox (Step 12) — set up the
   watchdog cron entry at install time, never skip it.
