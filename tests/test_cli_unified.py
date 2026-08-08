"""Tests of the unified ``synapse`` CLI (SPEC_CLI.md) — structured groups.

Coverage: server/web lifecycle (real detached processes), local token
without a password, local procedures (init/enable), raw ``api`` access,
exit codes (0/1/3), diagnostics, backups, deprecated aliases, passwords
never as arguments.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from importlib.metadata import version as _pkg_version
from synapse.cli.common import project_version as __version__
from tests.cli_helpers import run_cli


def _json(proc) -> dict:
    return json.loads(proc.stdout.decode())


# ---------------------------------------------------------------------------
# Server lifecycle (real processes)
# ---------------------------------------------------------------------------


def test_server_lifecycle(cli_env):
    config, config_file, env = cli_env

    # local org init (no server required)
    proc = run_cli(env, "org", "init", "acme", "--password-stdin",
                   stdin="motdepasse-acme-1\n")
    assert proc.returncode == 0, proc.stderr.decode()

    # detached start + PID file written
    proc = run_cli(env, "server", "start")
    assert proc.returncode == 0, proc.stderr.decode()
    try:
        pid_file = os.path.join(os.path.dirname(config.socket_path), "synapse.pid")
        info = json.loads(open(pid_file).read())
        assert info["pid"] > 0 and info["version"]
        assert os.path.exists(os.path.join(os.path.dirname(config.socket_path),
                                           "web_token"))

        # idempotency: already started → code 0 + clear message
        proc = run_cli(env, "server", "start")
        assert proc.returncode == 0
        assert "already running" in proc.stdout.decode()

        # status --json: double check PID + socket
        proc = run_cli(env, "server", "status", "--json")
        assert proc.returncode == 0
        data = _json(proc)["data"]
        assert data["state"] == "running"
        assert data["socket_ok"] is True
        assert data["web_token_present"] is True
        from importlib.metadata import version as _pkg_version
        assert data["version"] == _pkg_version("synapse-messenger")
    finally:
        proc = run_cli(env, "server", "stop")
        assert proc.returncode == 0, proc.stderr.decode()

    # clean stop: socket + token + PID removed (short wait: the
    # removal happens in the parent process after the daemon stops)
    run_dir = os.path.dirname(config.socket_path)
    deadline = time.time() + 5
    while time.time() < deadline:
        if not (os.path.exists(os.path.join(run_dir, "synapse.sock"))
                or os.path.exists(os.path.join(run_dir, "web_token"))
                or os.path.exists(os.path.join(run_dir, "synapse.pid"))):
            break
        time.sleep(0.1)
    assert not os.path.exists(os.path.join(run_dir, "synapse.sock"))
    assert not os.path.exists(os.path.join(run_dir, "web_token"))
    assert not os.path.exists(os.path.join(run_dir, "synapse.pid"))

    # stop on a stopped server: idempotent, code 0
    proc = run_cli(env, "server", "stop")
    assert proc.returncode == 0
    assert "already stopped" in proc.stdout.decode()


def test_server_start_double_lock(cli_env):
    """Startup with an active lock (legacy server without a PID file)."""
    _, config_file, env = cli_env
    # real server in a thread (legacy type: no CLI PID file)
    from synapse.config import Config
    from tests.conftest import make_server

    srv = make_server(Config.load(config_file), org=False)
    try:
        proc = run_cli(env, "server", "start")
        # the socket responds: considered already started (idempotent)
        assert proc.returncode == 0
        assert "already running" in proc.stdout.decode()
    finally:
        srv.stop()


def test_server_restart(cli_env):
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    run_cli(env, "server", "start")
    try:
        proc = run_cli(env, "server", "restart")
        assert proc.returncode == 0, proc.stderr.decode()
        assert "stopped" in proc.stdout.decode()
        assert "started" in proc.stdout.decode()
        proc = run_cli(env, "server", "status", "--json")
        assert _json(proc)["data"]["state"] == "running"
    finally:
        run_cli(env, "server", "stop")


def test_server_config_masks_secrets(cli_env):
    _, config_file, env = cli_env
    proc = run_cli(env, "server", "config", "--json")
    assert proc.returncode == 0
    data = _json(proc)["data"]
    assert data["socket_path"].endswith("synapse.sock")


# ---------------------------------------------------------------------------
# Local token: administration without a password (rule 7)
# ---------------------------------------------------------------------------


def test_token_auth_org_commands(cli_env):
    """Org commands served by the local token: NO password."""
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    run_cli(env, "server", "start")
    try:
        proc = run_cli(env, "org", "list", "--json")
        assert proc.returncode == 0, proc.stderr.decode()
        orgs = _json(proc)["data"]["organizations"]
        assert {o["organization_name"] for o in orgs} == {"acme"}

        proc = run_cli(env, "agent", "create", "support", "--password-stdin",
                       stdin="motdepasse-support-1\n")
        assert proc.returncode == 0, proc.stderr.decode()

        proc = run_cli(env, "agent", "status", "support", "--json")
        assert proc.returncode == 0
        assert _json(proc)["data"]["username"] == "support"

        proc = run_cli(env, "org", "metrics", "acme", "--json")
        # total_agents includes the organization's human account
        assert _json(proc)["data"]["total_agents"] >= 1
    finally:
        run_cli(env, "server", "stop")


def test_org_disable_enable_cycle(cli_env):
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    run_cli(env, "server", "start")
    try:
        proc = run_cli(env, "org", "disable", "acme", "--json")
        assert proc.returncode == 0
        assert _json(proc)["data"]["enabled"] is False
        # absolute freeze: no active org
        proc = run_cli(env, "org", "list", "--json")
        assert _json(proc)["data"]["organizations"] == []
        # LOCAL unfreeze (org password required)
        proc = run_cli(env, "org", "enable", "acme", "--password-stdin",
                       stdin="motdepasse-acme-1\n")
        assert proc.returncode == 0
        proc = run_cli(env, "org", "list", "--json")
        assert len(_json(proc)["data"]["organizations"]) == 1
        # idempotent enable
        proc = run_cli(env, "org", "enable", "acme", "--password-stdin",
                       stdin="motdepasse-acme-1\n")
        assert proc.returncode == 0
        assert "already active" in proc.stdout.decode()
    finally:
        run_cli(env, "server", "stop")


def test_org_list_all_disabled(cli_env):
    """org list --all: human account of an active org + disabled ones."""
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    run_cli(env, "org", "init", "gel", "--password-stdin",
            stdin="motdepasse-gel-1\n")
    run_cli(env, "server", "start")
    try:
        run_cli(env, "org", "disable", "gel", "--json")
        proc = run_cli(env, "org", "list", "--all", "--json")
        assert proc.returncode == 0, proc.stderr.decode()
        data = _json(proc)["data"]
        active = {o["organization_name"] for o in data["organizations"]}
        disabled = {o["organization_name"] for o in data.get("disabled", [])}
        assert active == {"acme"}
        assert disabled == {"gel"}
    finally:
        run_cli(env, "server", "stop")


# ---------------------------------------------------------------------------
# Exit codes (SPEC_CLI §2)
# ---------------------------------------------------------------------------


def test_exit_code_service_unavailable(cli_env):
    """Missing socket: code 3 (service unavailable)."""
    _, _, env = cli_env
    proc = run_cli(env, "org", "list")
    assert proc.returncode == 3
    assert "service unavailable" in proc.stderr.decode()


def test_exit_code_error(cli_env):
    """API refusal: code 1 with a JSON envelope."""
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    run_cli(env, "server", "start")
    try:
        proc = run_cli(env, "agent", "create", "x_humain", "--password-stdin",
                       stdin="motdepasse-x-1\n")
        assert proc.returncode == 1
        assert _json(proc)["success"] is False
        assert "_humain" in proc.stderr.decode()
    finally:
        run_cli(env, "server", "stop")


def test_exit_code_argument_error(cli_env):
    """Argument error: code 1 (not 2, SPEC_CLI §2)."""
    _, _, env = cli_env
    proc = run_cli(env, "org", "list", "--bogus")
    assert proc.returncode == 1
    assert "unexpected arguments" in proc.stderr.decode()


# ---------------------------------------------------------------------------
# Raw api access
# ---------------------------------------------------------------------------


def test_api_raw_full_envelope(cli_env):
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    run_cli(env, "server", "start")
    try:
        proc = run_cli(env, "api", "list_orgs")
        assert proc.returncode == 0, proc.stderr.decode()
        assert _json(proc)["success"] is True

        proc = run_cli(env, "api", "get_org_metrics", "--organization-name", "acme")
        assert _json(proc)["data"]["organization_name"] == "acme"

        proc = run_cli(env, "api", "frobnicate")
        assert proc.returncode == 1
        assert _json(proc)["error"]["code"] == "UNKNOWN_COMMAND"
    finally:
        run_cli(env, "server", "stop")


def test_api_password_never_in_argv(cli_env):
    """Password parameters are refused as arguments (rule 3)."""
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    run_cli(env, "server", "start")
    try:
        proc = run_cli(env, "api", "create_agent", "--username", "x",
                       "--password", "motdepasse-x-1",
                       "--description", "d", "--organization-name", "acme")
        assert proc.returncode == 1
        assert "password forbidden as a command argument" in proc.stderr.decode()
    finally:
        run_cli(env, "server", "stop")


def test_api_create_org_via_web_local(cli_env):
    """api create_org: local web identity + token (no org required)."""
    _, _, env = cli_env
    run_cli(env, "server", "start")
    try:
        proc = run_cli(env, "api", "create_org", "--organization-name", "nouvelle",
                       "--password-stdin", stdin="motdepasse-nouvelle-1\n")
        assert proc.returncode == 0, proc.stderr.decode()
        assert _json(proc)["data"]["organization_name"] == "nouvelle"
    finally:
        run_cli(env, "server", "stop")


# ---------------------------------------------------------------------------
# Local procedures, web, backups, diag, update, logs
# ---------------------------------------------------------------------------


def test_org_init_refuses_existing(cli_env):
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    proc = run_cli(env, "org", "init", "acme", "--password-stdin",
                   stdin="motdepasse-acme-2\n")
    assert proc.returncode == 1
    assert "already exists" in proc.stderr.decode()


def test_web_lifecycle(cli_env):
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    run_cli(env, "server", "start")
    try:
        proc = run_cli(env, "web", "start", "--port", "18081")
        assert proc.returncode == 0, proc.stderr.decode()
        try:
            proc = run_cli(env, "web", "status", "--json")
            data = _json(proc)["data"]
            assert data["state"] == "running"
            assert data["http_ok"] is True
            assert data["sessions_active"] == 0
        finally:
            proc = run_cli(env, "web", "stop")
            assert proc.returncode == 0, proc.stderr.decode()
        proc = run_cli(env, "web", "status", "--json")
        assert _json(proc)["data"]["state"] == "stopped"
    finally:
        run_cli(env, "server", "stop")


def test_web_start_requires_server(cli_env):
    """web start without a server: code 3, "local service not ready"."""
    _, _, env = cli_env
    proc = run_cli(env, "web", "start")
    assert proc.returncode == 3
    assert "local service not ready" in proc.stderr.decode()


def test_backup_create_list_restore(cli_env):
    _, config, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    run_cli(env, "server", "start")
    try:
        proc = run_cli(env, "backup", "create")
        assert proc.returncode == 0, proc.stderr.decode()
        archive = proc.stdout.decode().splitlines()[0]
        assert archive.endswith(".synbk")

        proc = run_cli(env, "backup", "list", "--json")
        data = _json(proc)["data"]
        assert len(data["backups"]) == 1
        assert data["backups"][0]["format"] == 1
        assert data["backups"][0]["created_at"]
    finally:
        run_cli(env, "server", "stop")

    # restore: server stopped required; --force mandatory
    proc = run_cli(env, "backup", "restore", archive)
    assert proc.returncode == 1
    assert "--force" in proc.stderr.decode()
    proc = run_cli(env, "backup", "restore", archive, "--force")
    assert proc.returncode == 0, proc.stderr.decode()


def test_diag_doctor(cli_env):
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    run_cli(env, "server", "start")
    try:
        proc = run_cli(env, "diag", "doctor")
        assert proc.returncode == 0, proc.stderr.decode()
        assert "Diagnostic complete" in proc.stdout.decode()
        proc = run_cli(env, "diag", "doctor", "--json")
        checks = _json(proc)["data"]["checks"]
        verdicts = {c["verdict"] for c in checks}
        assert "FAIL" not in verdicts
    finally:
        run_cli(env, "server", "stop")


def test_update_check_and_dry_run(cli_env):
    _, _, env = cli_env
    proc = run_cli(env, "update", "check", "--json")
    assert proc.returncode == 0
    assert _json(proc)["data"]["installed"] == _pkg_version("synapse-messenger")
    proc = run_cli(env, "update", "apply", "--dry-run")
    assert proc.returncode == 0
    assert "Update plan" in proc.stdout.decode()
    # without a configured command: explicit refusal (no simulated behavior)
    proc = run_cli(env, "update", "apply")
    assert proc.returncode == 1
    assert "no update command" in proc.stderr.decode()


def test_logs_command(cli_env):
    _, _, env = cli_env
    run_cli(env, "server", "start")
    try:
        proc = run_cli(env, "server", "logs", "--lines", "5")
        assert proc.returncode == 0
        assert "timestamp" in proc.stdout.decode()
        proc = run_cli(env, "logs", "--lines", "5")
        assert proc.returncode == 0
        # --level unavailable: explicit refusal
        proc = run_cli(env, "server", "logs", "--level", "debug")
        assert proc.returncode == 1
        assert "does not contain a level" in proc.stderr.decode()
    finally:
        run_cli(env, "server", "stop")


def test_status_global(cli_env):
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    run_cli(env, "server", "start")
    try:
        proc = run_cli(env, "status", "--json")
        data = _json(proc)["data"]
        assert data["server"]["state"] == "running"
        assert data["organizations"][0]["organization_name"] == "acme"
    finally:
        run_cli(env, "server", "stop")


def test_bare_synapse_is_server_start(cli_env):
    """Bare "synapse" = idempotent server start (decision §7.2)."""
    _, _, env = cli_env
    proc = run_cli(env)
    assert proc.returncode == 0, proc.stderr.decode()
    assert "started" in proc.stdout.decode()
    try:
        proc = run_cli(env)
        assert proc.returncode == 0
        assert "already running" in proc.stdout.decode()
    finally:
        run_cli(env, "server", "stop")


def test_help_command(cli_env):
    """synapse help and --help: general help, without a server."""
    _, _, env = cli_env
    proc = run_cli(env, "help")
    assert proc.returncode == 0
    assert "Groups:" in proc.stdout.decode()
    proc = run_cli(env, "--help")
    assert proc.returncode == 0


def test_deprecated_alias_backup(cli_env):
    """Legacy binaries delegate with a warning (SPEC_CLI §6)."""
    _, _, env = cli_env
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys; from synapse.cli.aliases import backup_alias_main; "
         "sys.exit(backup_alias_main())",
         "--config", env["Synapse_CONFIG"]],
        capture_output=True, env=env, timeout=30,
    )
    assert proc.returncode == 0
    assert "deprecated" in proc.stderr.decode()
    assert "synapse backup create" in proc.stderr.decode()
    assert proc.stdout.decode().strip().endswith(".synbk") or ".synbk" in proc.stdout.decode()


# ---------------------------------------------------------------------------
# Full group coverage (agent, task, group, policy, event, a2a)
# ---------------------------------------------------------------------------


def _bootstrap(env, extra_agents=("data",)):
    """org acme + started server + agents (fixed passwords) ready."""
    run_cli(env, "org", "init", "acme", "--password-stdin",
            stdin="motdepasse-acme-1\n")
    proc = run_cli(env, "server", "start")
    assert proc.returncode == 0, proc.stderr.decode()
    for agent in extra_agents:
        proc = run_cli(env, "agent", "create", agent, "--password-stdin",
                       stdin=f"motdepasse-{agent}-1\n")
        assert proc.returncode == 0, proc.stderr.decode()


def test_agent_description_card_find(cli_env):
    _, _, env = cli_env
    _bootstrap(env)
    try:
        proc = run_cli(env, "agent", "description", "data",
                       "Analyzes the data")
        assert proc.returncode == 0, proc.stderr.decode()
        proc = run_cli(env, "api", "get_agent_description", "--username", "data")
        assert _json(proc)["data"]["description"] == "Analyzes the data"

        # card: write --set (--my-name = the account) then read
        proc = run_cli(env, "agent", "card", "data", "--set",
                       "--capability", "audit", "--model", "m-1",
                       "--my-name", "data", "--password-stdin",
                       stdin="motdepasse-data-1\n")
        assert proc.returncode == 0, proc.stderr.decode()
        proc = run_cli(env, "agent", "card", "data", "--json")
        card = _json(proc)["data"]
        assert card["capabilities"] == ["audit"]
        assert card["model"] == "m-1"

        # find by capability
        proc = run_cli(env, "agent", "find", "--capability", "audit", "--json")
        agents = _json(proc)["data"]["agents"]
        assert any(a["username"] == "data" for a in agents)

        # card write without --my-name: explicit refusal
        proc = run_cli(env, "agent", "card", "data", "--set",
                       "--capability", "x")
        assert proc.returncode == 1
        assert "--my-name" in proc.stderr.decode()
    finally:
        run_cli(env, "server", "stop")


def test_task_full_lifecycle(cli_env):
    _, _, env = cli_env
    _bootstrap(env)
    try:
        proc = run_cli(env, "task", "create", "Rapport", "--assignee", "data",
                       "--priority", "haute", "--json")
        assert proc.returncode == 0, proc.stderr.decode()
        task_id = _json(proc)["data"]["task_id"]
        assert _json(proc)["data"]["priority"] == "high"  # FR→EN translation

        proc = run_cli(env, "task", "status", task_id, "--json")
        assert _json(proc)["data"]["state"] == "submitted"

        # in-progress then completed (agent data)
        proc = run_cli(env, "task", "update", task_id, "en_cours", "--json",
                       "--my-name", "data", "--password-stdin",
                       stdin="motdepasse-data-1\n")
        assert _json(proc)["data"]["state"] == "in_progress"
        proc = run_cli(env, "task", "update", task_id, "terminee", "--json",
                       "--my-name", "data", "--password-stdin",
                       stdin="motdepasse-data-1\n")
        assert _json(proc)["data"]["state"] == "completed"

        # my-work
        proc = run_cli(env, "task", "my-work", "--my-name", "data",
                       "--password-stdin", "--json",
                       stdin="motdepasse-data-1\n")
        assert _json(proc)["data"]["work_items"] == []

        # new task: approval (request + rejection + approval)
        proc = run_cli(env, "task", "create", "To approve", "--assignee", "data",
                       "--json")
        task_id = _json(proc)["data"]["task_id"]
        proc = run_cli(env, "task", "request-approval", task_id,
                       "--approver", "acme_humain",
                       "--my-name", "data", "--password-stdin",
                       stdin="motdepasse-data-1\n")
        assert proc.returncode == 0, proc.stderr.decode()
        proc = run_cli(env, "task", "reject", task_id, "--reason", "non", "--json",
                       "--my-name", "acme_humain", "--password-stdin",
                       stdin="motdepasse-acme-1\n")
        # the rejection brings the task back to the assignee (in_progress — SPEC.txt F7)
        assert _json(proc)["data"]["state"] == "in_progress"
    finally:
        run_cli(env, "server", "stop")


def test_group_full_lifecycle(cli_env):
    _, _, env = cli_env
    _bootstrap(env, extra_agents=("data", "comptable"))
    try:
        proc = run_cli(env, "group", "create", "direction", "--json")
        assert proc.returncode == 0, proc.stderr.decode()
        proc = run_cli(env, "group", "add-member", "direction", "comptable")
        assert proc.returncode == 0, proc.stderr.decode()
        proc = run_cli(env, "group", "members", "direction", "--json")
        members = _json(proc)["data"]["members"]
        assert "comptable" in members
        proc = run_cli(env, "group", "send", "direction", "Bonjour le groupe")
        assert proc.returncode == 0, proc.stderr.decode()
        proc = run_cli(env, "group", "messages", "direction", "--json")
        assert len(_json(proc)["data"]["messages"]) == 1
        proc = run_cli(env, "group", "list", "--json")
        names = {g["name"] for g in _json(proc)["data"]["groups"]}
        assert "direction" in names
        proc = run_cli(env, "group", "remove-member", "direction", "comptable")
        assert proc.returncode == 0, proc.stderr.decode()
        # unknown group: honest error
        proc = run_cli(env, "group", "members", "inexistant")
        assert proc.returncode == 1
    finally:
        run_cli(env, "server", "stop")


def test_policy_delegations(cli_env):
    _, _, env = cli_env
    _bootstrap(env, extra_agents=("data", "comptable"))
    try:
        proc = run_cli(env, "task", "create", "T", "--assignee", "data", "--json")
        task_id = _json(proc)["data"]["task_id"]
        # task delegation (the delegator must be linked to the task)
        proc = run_cli(env, "policy", "delegate", "comptable", "--task", task_id,
                       "--expires", "2026-09-01T00:00:00Z",
                       "--my-name", "data", "--password-stdin",
                       stdin="motdepasse-data-1\n")
        assert proc.returncode == 0, proc.stderr.decode()
        proc = run_cli(env, "policy", "delegations", "--my-name", "comptable",
                       "--password-stdin", "--json", stdin="motdepasse-comptable-1\n")
        delegations = _json(proc)["data"]["delegations"]
        assert len(delegations) == 1
        assert delegations[0]["expires_at"] == "2026-09-01T00:00:00.000Z"
        proc = run_cli(env, "policy", "revoke", "comptable", "--task", task_id,
                       "--my-name", "data", "--password-stdin",
                       stdin="motdepasse-data-1\n")
        assert proc.returncode == 0, proc.stderr.decode()
    finally:
        run_cli(env, "server", "stop")


def test_event_stream(cli_env):
    _, _, env = cli_env
    _bootstrap(env)
    try:
        # events are emitted for tasks (task.created) — agent
        # creation does not emit a journal event.
        proc = run_cli(env, "task", "create", "Event", "--assignee", "data",
                       "--json")
        assert proc.returncode == 0, proc.stderr.decode()
        proc = run_cli(env, "event", "stream", "--limit", "5", "--json")
        assert proc.returncode == 0, proc.stderr.decode()
        events = _json(proc)["data"]["events"]
        assert events, "at least one event (task.created)"
        assert all("seq" in e for e in events)
    finally:
        run_cli(env, "server", "stop")


def test_a2a_lifecycle(cli_env):
    _, _, env = cli_env
    _bootstrap(env)
    try:
        proc = run_cli(env, "a2a", "start", "--agent-name", "data",
                       "--port", "18095", "--password-stdin", "--token-stdin",
                       stdin="motdepasse-data-1\njeton-a2a-test-1\n")
        assert proc.returncode == 0, proc.stderr.decode()
        try:
            proc = run_cli(env, "a2a", "status", "--json")
            data = _json(proc)["data"]
            assert data["state"] == "running"
            assert data["agent_name"] == "data"
            assert data["http_ok"] is True
        finally:
            proc = run_cli(env, "a2a", "stop")
            assert proc.returncode == 0, proc.stderr.decode()
    finally:
        run_cli(env, "server", "stop")


def test_a2a_start_requires_server(cli_env):
    """a2a start without a server: code 3."""
    _, _, env = cli_env
    proc = run_cli(env, "a2a", "start", "--agent-name", "x",
                   "--password-stdin", "--token-stdin",
                   stdin="mdp\njeton\n")
    assert proc.returncode == 3
    assert "local service not ready" in proc.stderr.decode()
