# Synapse CLI — verified commands (reference)

Verified against the unified CLI (SPEC_CLI). Secrets always via
`--password-stdin` + pipe, never as arguments.

```bash
synapse server status --json            # instance state, version
synapse org init <name> --password-stdin
synapse agent create <name> --description "<role>" --password-stdin
#   ^ reads TWO passwords via stdin: line 1 = agent, line 2 = org
synapse agent list --json               # org directory
synapse message send <dest> "<text>" --client-message-id <id> \
    --my-name <sender> --password-stdin
synapse message inbox --unread --my-name <agent> --password-stdin
synapse status --json                   # full state (org, agents, web token)
synapse --version                       # installed version
```

Config: `SYNAPSE_CONFIG` env or `--config <path>`. Test isolation:
`SYNAPSE_NO_SYSTEMD=1`, `SYNAPSE_WEB_PORT` (free ports). Transport: Unix
socket on POSIX (default), loopback TCP + per-run token on Windows —
the JSON protocol is identical.

Pitfalls:

- `agent create` needs TWO passwords (agent then org) — one line is not
  enough.
- Installation: NOT on PyPI — use the GitHub release wheel or
  git+https (see the umbrella).
- Reserved/org/admin commands exist (backup, server, web, org policy…):
  agent-facing skills only list their names under "Limits".

Full project context: `synapse-project` skill.
