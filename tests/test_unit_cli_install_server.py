"""Atomic tests of the unified CLI (direct main() invocation), installation,
server internals and logging.

The CLI is tested in-process (main() called directly) with simulated
stdin/getpass — unified SPEC_CLI syntax (structured groups + raw ``api``
access).
"""

from __future__ import annotations

import json
import logging
import os
import sys

import pytest

from synapse.config import Config
from synapse.errors import ApiError
from synapse.install import create_organization
from synapse.server import lock_is_stale

from .conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD, ALICE_DESCRIPTION


class _CliResult:
    def __init__(self, out: str, err: str, code):
        self.out = out
        self.err = err
        self.code = code

    def json(self):
        return json.loads(self.out)


@pytest.fixture()
def run_cli(monkeypatch, capsys, tmp_path, config):
    """Runs cli.main() with simulated argv/stdin; getpass fed by a queue of
    passwords; SystemExit captured in ``result.code``.

    The test configuration is written to a JSON file and resolved via
    ``$Synapse_CONFIG`` (SPEC_CLI §2 lookup order).
    """
    cfg_file = tmp_path / "cli-config.json"
    cfg_file.write_text(json.dumps(config.to_dict()))

    class _FakeStdin:
        def __init__(self, data: str):
            self._lines = data.split("\n")
            self._i = 0

        def readline(self):
            if self._i < len(self._lines):
                value = self._lines[self._i]
                self._i += 1
                return value + "\n"
            return "\n"

        def read(self):
            return "\n".join(self._lines)

    from synapse.cli import common as cli_common

    def _run(args, stdin="", passwords=(), config_file=None):
        target = str(config_file) if config_file is not None else str(cfg_file)
        monkeypatch.setenv("Synapse_CONFIG", target)
        monkeypatch.setattr(sys, "argv", ["synapse", *args])
        monkeypatch.setattr(sys, "stdin", _FakeStdin(stdin))
        queue = list(passwords)

        def fake_getpass(prompt=""):  # noqa: ANN001
            return queue.pop(0) if queue else "\n"

        monkeypatch.setattr(cli_common.getpass, "getpass", fake_getpass)
        code = None
        try:
            from synapse.cli.main import main

            code = main()
        except SystemExit as exc:
            code = exc.code if exc.code is not None else 0
        out, err = capsys.readouterr()
        return _CliResult(out, err, code)

    return _run


def test_cli_each_agent_command(fx, run_cli):
    # send_message (structured message group)
    out = run_cli(["message", "send", BOB, "via cli directe",
                   "--client-message-id", "cmid-direct-1",
                   "--my-name", ALICE, "--password-stdin", "--json"],
                  stdin=f"{ALICE_PASSWORD}\n")
    env = out.json()
    assert env["success"] is True, out.err
    msg_id = env["data"]["message_id"]

    # inbox with unread filter
    out = run_cli(["message", "inbox", "--my-name", BOB, "--password-stdin", "--json"],
                  stdin=f"{BOB_PASSWORD}\n")
    env = out.json()
    assert len(env["data"]["messages"]) == 1

    # read_message
    out = run_cli(["message", "read", msg_id, "--my-name", BOB, "--password-stdin", "--json"],
                  stdin=f"{BOB_PASSWORD}\n")
    assert out.json()["data"]["status"] == "read"

    # get_conversation
    out = run_cli(["message", "conversation", ALICE, "--my-name", BOB,
                   "--password-stdin", "--json"], stdin=f"{BOB_PASSWORD}\n")
    assert out.json()["data"]["reply_status"] == "needs_reply"

    # notifications
    out = run_cli(["message", "notifications", "--my-name", BOB,
                   "--password-stdin", "--json"], stdin=f"{BOB_PASSWORD}\n")
    assert out.json()["data"]["needs_reply"][0]["other_username"] == ALICE

    # mark_conversation_no_reply (resolution by counterpart)
    out.json()["data"]["needs_reply"][0]["conversation_id"]
    out = run_cli(["message", "mark-no-reply", ALICE, "--my-name", BOB,
                   "--password-stdin", "--json"], stdin=f"{BOB_PASSWORD}\n")
    assert out.json()["data"]["reply_status"] == "no_reply_needed"


def test_cli_each_org_command(fx, run_cli):
    """Organization commands via the structured CLI (local token present:
    no org password on stdin — rule 7)."""

    # agent create (new agent's password on stdin)
    out = run_cli(["agent", "create", "carol",
                   "--description", "Agent carol de test", "--password-stdin", "--json"],
                  stdin="motdepasse-carol-1\n")
    assert out.json()["data"] == {"username": "carol", "status": "active",
                                  "description": "Agent carol de test",
                                  "organization_name": ORG_NAME,
                                  "can_see_org_agents": False,
                                  "principal_type": "agent"}

    # deactivate_agent / reactivate_agent
    out = run_cli(["agent", "deactivate", "carol", "--json"])
    assert out.json()["data"]["status"] == "disabled"
    out = run_cli(["agent", "reactivate", "carol", "--json"])
    assert out.json()["data"]["status"] == "active"

    # change_agent_password: new password via getpass, org via token
    out = run_cli(["agent", "password", "carol", "--json"],
                  passwords=("nouveau-motdepasse-carol",))
    assert out.json()["success"] is True


def test_cli_error_envelope_direct(fx, run_cli):
    out = run_cli(["api", "get_messages", "--my-name", ALICE, "--password-stdin"],
                  stdin="mauvais\n")
    env = out.json()
    assert env["success"] is False
    assert env["error"]["code"] == "AUTH_FAILED"
    assert env["data"] is None


def test_cli_unknown_subcommand(fx, run_cli):
    result = run_cli(["frobnicate"])
    assert result.code != 0


def test_cli_transport_error(fx, run_cli, tmp_path):
    """Missing socket: code 3 (service unavailable, SPEC_CLI §2)."""
    cfg = Config.from_dict({
        "storage_dir": str(tmp_path / "data"),
        "socket_path": str(tmp_path / "absent" / "synapse.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "bk"),
    })
    alt_cfg = tmp_path / "absent-config.json"
    alt_cfg.write_text(json.dumps(cfg.to_dict()))
    out = run_cli(["api", "get_messages", "--my-name", ALICE, "--password-stdin"],
                  stdin=f"{ALICE_PASSWORD}\n", config_file=alt_cfg)
    assert out.code == 3
    assert "service unavailable" in out.err


def test_cli_empty_password_on_stdin(fx, run_cli):
    result = run_cli(["api", "get_messages", "--my-name", ALICE,
                      "--password-stdin"], stdin="\n")
    assert result.code == 1
    assert "empty password" in result.err


def test_cli_create_agent_via_getpass(fx, run_cli):
    """agent create without --password-stdin: password via getpass."""
    result = run_cli(["agent", "create", "carol",
                      "--description", "Agent carol de test", "--json"],
                     passwords=("motdepasse-carol-1",))
    assert result.json()["data"]["username"] == "carol"
    assert result.json()["data"]["description"] == "Agent carol de test"


def test_cli_password_via_getpass(fx, run_cli):
    """Without --password-stdin, the password goes through getpass."""
    result = run_cli(["api", "get_messages", "--my-name", ALICE],
                     passwords=(ALICE_PASSWORD,))
    assert result.json()["success"] is True


def test_cli_get_agent_description(fx, run_cli):
    out = run_cli(["api", "get_agent_description", "--username", ALICE,
                   "--my-name", BOB, "--password-stdin"],
                  stdin=f"{BOB_PASSWORD}\n")
    assert out.json()["data"]["username"] == ALICE
    assert out.json()["data"]["description"] == ALICE_DESCRIPTION


def test_cli_create_agent_with_description(fx, run_cli):
    out = run_cli(["agent", "create", "carol",
                   "--description", "Agent carol : revue de code",
                   "--password-stdin", "--json"],
                  stdin="motdepasse-carol-1\n")
    assert out.json()["data"]["description"] == "Agent carol : revue de code"


def test_cli_org_management_subcommands(fx, run_cli):
    """Organization subcommands: visibility, list, policies,
    password rotation, agent's organization."""
    fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol",
                           ORG_NAME, ORG_PASSWORD)
    # agent visibility (directory)
    out = run_cli(["agent", "visibility", "carol", "visible", "--json"])
    assert out.json()["data"] == {"username": "carol", "can_see_org_agents": True}
    # org agents
    out = run_cli(["org", "agents", ORG_NAME, "--json"])
    usernames = set(out.json()["data"]["usernames"])
    assert {"alice", "bob", "carol"} <= usernames
    # policy set + show
    out = run_cli(["policy", "set", ORG_NAME, "--allow-incoming-external",
                   "--allow-outgoing-external", "--json"])
    assert out.json()["data"]["allow_incoming_external"] is True
    out = run_cli(["policy", "show", ORG_NAME, "--json"])
    assert out.json()["data"]["allow_outgoing_external"] is True
    # change_organization_password (new via getpass, org via token)
    out = run_cli(["org", "password", ORG_NAME, "--json"],
                  passwords=("nouveau-mot-de-passe-org-1",))
    assert out.json()["data"]["organization_name"] == ORG_NAME
    # get_my_organization (agent)
    out = run_cli(["api", "get_my_organization", "--my-name", ALICE,
                   "--password-stdin"], stdin=f"{ALICE_PASSWORD}\n")
    assert out.json()["data"]["organization_name"] == ORG_NAME
    # list_org_agents (authorized agent)
    run_cli(["agent", "visibility", ALICE, "visible"])
    out = run_cli(["api", "list_org_agents", "--my-name", ALICE,
                   "--password-stdin"], stdin=f"{ALICE_PASSWORD}\n")
    assert "bob" in out.json()["data"]["usernames"]


def test_cli_help_full_and_targeted(fx, run_cli):
    """help via raw access: full and targeted documentation."""
    out = run_cli(["api", "help", "--my-name", ALICE, "--password-stdin"],
                  stdin=f"{ALICE_PASSWORD}\n")
    doc = out.json()["data"]["documentation"]
    assert "send_message" in doc and "create_agent" in doc
    # targeted mode
    out = run_cli(["api", "help", "--command-name", "get_agent_description",
                   "--my-name", ALICE, "--password-stdin"],
                  stdin=f"{ALICE_PASSWORD}\n")
    doc = out.json()["data"]["documentation"]
    assert "get_agent_description" in doc
    assert "send_message" not in doc
    # unknown command: UNKNOWN_COMMAND
    out = run_cli(["api", "help", "--command-name", "frobnicate",
                   "--my-name", ALICE, "--password-stdin"],
                  stdin=f"{ALICE_PASSWORD}\n")
    assert out.code == 1
    assert out.json()["error"]["code"] == "UNKNOWN_COMMAND"


def test_cli_v3_org_commands(fx, run_cli):
    """SPEC.txt v3 organization commands via the structured CLI
    (authentication by local token)."""

    # approve_agent_card (raw access, org-auth via token)
    fx.client.set_agent_card(["comptabilite"], ALICE, ALICE_PASSWORD)
    out = run_cli(["api", "approve_agent_card", "--username", ALICE])
    assert out.json()["data"]["validation_state"] == "approved"

    # create_department + set_agent_department + structure
    out = run_cli(["api", "create_department", "--department-name", "support"])
    assert out.json()["data"]["department_name"] == "support"
    out = run_cli(["agent", "department", ALICE, "support", "--role", "manager", "--json"])
    assert out.json()["data"]["role"] == "manager"
    out = run_cli(["org", "structure", ORG_NAME, "--json"])
    assert out.json()["data"]["departments"][0]["department_name"] == "support"

    # escalation + budget + retention
    out = run_cli(["policy", "escalation", ORG_NAME, "--set", "--max-hours", "1",
                   "--targets", ALICE, "--json"])
    assert out.json()["data"]["enabled"] is True
    out = run_cli(["agent", "budget", BOB, "--max-active-tasks", "5", "--json"])
    assert out.json()["data"]["max_active_tasks"] == 5
    out = run_cli(["event", "retention", "30", "--json"])
    assert out.json()["data"]["event_retention_days"] == 30
    # --clear removes every budget of the agent (previously impossible
    # through the CLI: 0 is refused by the API, no flag was refused by
    # the CLI).
    out = run_cli(["agent", "budget", BOB, "--clear", "--json"])
    assert out.json()["data"]["max_active_tasks"] is None
    assert out.json()["data"]["max_messages_per_hour"] is None
    out = run_cli(["agent", "budget", BOB, "--max-active-tasks", "5", "--json"])
    assert out.json()["data"]["max_active_tasks"] == 5
    out = run_cli(["agent", "budget", BOB, "--max-messages-per-hour", "3", "--json"])
    assert out.json()["data"]["max_messages_per_hour"] == 3
    assert out.json()["data"]["max_active_tasks"] == 5  # preserved by COALESCE

    # observers
    out = run_cli(["agent", "create-observer", "superviseur",
                   "--description", "Supervision", "--password-stdin", "--json"],
                  stdin="motdepasse-superviseur-1\n")
    assert out.json()["data"]["read_only"] is True
    out = run_cli(["agent", "observers", "--json"])
    assert "superviseur" in str(out.json()["data"])
    out = run_cli(["agent", "revoke-observer", "superviseur", "--json"])
    assert out.json()["data"]["status"] == "disabled"

    # audit + metrics + server status
    fx.send(ALICE, ALICE_PASSWORD, BOB, "cli", "cmid-cli-v3-1")
    out = run_cli(["org", "audit", ORG_NAME, "--limit", "50", "--actor", ALICE, "--json"])
    assert out.json()["data"]["entries"]
    out = run_cli(["org", "metrics", ORG_NAME, "--json"])
    assert out.json()["data"]["total_agents"] >= 2
    out = run_cli(["api", "get_server_status"])
    assert out.json()["data"]["api_version"] == "v2"


def test_cli_get_escalation_policy(fx, run_cli):
    """Reading the escalation policy (65th command, SPEC_CLI)."""
    out = run_cli(["policy", "escalation", ORG_NAME, "--json"])
    assert out.json()["data"]["organization_name"] == ORG_NAME
    assert out.json()["data"]["enabled"] is False


# ---------------------------------------------------------------------------
# Installation (organization creation)
# ---------------------------------------------------------------------------


def test_create_organization_bad_username(config):
    with pytest.raises(ApiError):
        create_organization(config, "bad name!", "motdepasse-123", "motdepasse-123")


def test_create_organization_bad_password(config):
    with pytest.raises(ApiError):
        create_organization(config, "admin1", "court", "court")


def test_create_organization_confirm_mismatch(config):
    with pytest.raises(ValueError):
        create_organization(config, "admin1", "motdepasse-123", "different")


def test_create_organization_refuses_when_exists(fx, config):
    with pytest.raises(ValueError):
        create_organization(config, ORG_NAME, "motdepasse-123", "motdepasse-123")


def test_create_organization_success_and_persists(config):
    created = create_organization(
        config, "BossAdmin", "motdepasse-admin-1", "motdepasse-admin-1"
    )
    assert created == "bossadmin"  # normalized
    # a second call with the same name is refused
    with pytest.raises(ValueError):
        create_organization(config, "BossAdmin", "motdepasse-123", "motdepasse-123")
    # another organization can be created (several per server)
    created = create_organization(config, "org_bis", "motdepasse-org-bis-1",
                                  "motdepasse-org-bis-1")
    assert created == "org_bis"


def test_org_init_main_bad_config(tmp_path, capsys, monkeypatch):
    from synapse.install import org_init_main
    bad = tmp_path / "bad.json"
    bad.write_text("{pas du json")
    monkeypatch.setattr(sys, "argv", ["synapse-init-org", "--config", str(bad)])
    with pytest.raises(SystemExit) as exc:
        org_init_main()
    assert exc.value.code == 1


def test_org_init_main_eof_aborts(tmp_path, capsys, monkeypatch):
    """EOF on stdin (no terminal): operation canceled, code 1."""
    from synapse.install import org_init_main
    config_path = tmp_path / "conf.json"
    config_path.write_text(json.dumps({"storage_dir": str(tmp_path / "data"),
                                       "socket_path": str(tmp_path / "synapse.sock"),
                                       "log_dir": str(tmp_path / "logs"),
                                       "backup_dir": str(tmp_path / "bk")}))
    monkeypatch.setattr(sys, "argv", ["synapse-init-org", "--config", str(config_path)])
    monkeypatch.setattr(sys, "stdin", type("S", (), {"readline": lambda self: ""})())
    with pytest.raises(SystemExit) as exc:
        org_init_main()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Server: internals
# ---------------------------------------------------------------------------


def test_lock_is_stale_branches(tmp_path):
    dead = tmp_path / "dead.lock"
    dead.write_text("99999999")
    assert lock_is_stale(dead) is True

    alive = tmp_path / "alive.lock"
    alive.write_text(str(os.getpid()))
    assert lock_is_stale(alive) is False

    restore_marker = tmp_path / "restore.lock"
    restore_marker.write_text("restore")
    assert lock_is_stale(restore_marker) is False  # unknown content: active

    bad_pid = tmp_path / "bad.lock"
    bad_pid.write_text("0")
    assert lock_is_stale(bad_pid) is False

    junk = tmp_path / "junk.lock"
    junk.write_text("pas-un-pid")
    assert lock_is_stale(junk) is False


def test_server_prepare_socket_path_removes_stale(config, tmp_path, monkeypatch):
    """An orphan socket (not active) is removed at startup."""
    from synapse.server import SynapseServer
    # pristine configuration (no active server)
    fresh = Config.from_dict({
        "storage_dir": str(tmp_path / "data"),
        "socket_path": str(tmp_path / "run" / "synapse.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "bk"),
    })
    import socket as sockmod
    os.makedirs(os.path.dirname(fresh.socket_path), exist_ok=True)
    stale = sockmod.socket(sockmod.AF_UNIX, sockmod.SOCK_STREAM)
    stale.bind(fresh.socket_path)
    stale.close()  # orphan socket file (nobody is listening anymore)
    server = SynapseServer(fresh)
    server._prepare_socket_path()
    assert not os.path.exists(fresh.socket_path)
    # the real server can then start
    from .conftest import make_server
    srv = make_server(fresh, org=True)
    try:
        assert srv.client.get_org_agents(ORG_NAME, ORG_PASSWORD)["next_cursor"] is None
    finally:
        srv.stop()


def test_server_main_bad_config(tmp_path, monkeypatch):
    from synapse.server import main as server_main
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]")  # JSON valide mais non-objet -> ValueError
    monkeypatch.setattr(sys, "argv", ["synapse-server", "--config", str(bad)])
    with pytest.raises(SystemExit) as exc:
        server_main()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_json_formatter_with_exception():
    from synapse.logging_setup import JsonFormatter
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="boom", args=(), exc_info=(ValueError, ValueError("message-secret-xyz"), None),
    )
    line = formatter.format(record)
    entry = json.loads(line)
    assert entry["exception_type"] == "ValueError"
    # never the exception message nor the trace
    assert "message-secret-xyz" not in json.dumps(entry)
    assert "process_id" in entry and "timestamp" in entry


def test_json_formatter_allowed_fields_only():
    from synapse.logging_setup import JsonFormatter
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="request", args=(), exc_info=None,
    )
    record.username = "alice"
    record.command = "get_messages"
    record.target_id = "abc"
    record.result = "ok"
    record.secret_password = "ne-jamais-loguer"
    entry = json.loads(formatter.format(record))
    assert entry["username"] == "alice"
    assert entry["command"] == "get_messages"
    assert entry["result"] == "ok"
    assert "secret_password" not in entry
    assert "ne-jamais-loguer" not in json.dumps(entry)


def test_json_formatter_timestamp_milliseconds():
    """The log timestamp uses the exact spec format
    (YYYY-MM-DDTHH:MM:SS.sssZ, milliseconds — no literal %f)."""
    import re

    from synapse.logging_setup import JsonFormatter
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="x", args=(), exc_info=None,
    )
    ts = json.loads(formatter.format(record))["timestamp"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", ts)
    assert "%f" not in ts


def test_setup_logging_verbose(config, capsys):
    from synapse.logging_setup import setup_logging
    setup_logging(config, verbose=True)
    root = logging.getLogger()
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    assert os.path.exists(os.path.join(config.log_dir, "synapse.log"))


def test_cli_agent_status_reputation(fx, run_cli):
    """``agent status`` displays the server's real reputation (F16): the
    flat shape returned by get_agent_reputation (detail for oneself,
    qualitative mention for others) — never the old phantom contract
    score/total_reviews (AUDIT-002)."""
    # Reputation for oneself: counters + completion_rate.
    out = run_cli(["agent", "status", ALICE, "--my-name", ALICE,
                   "--password-stdin", "--json"], stdin=f"{ALICE_PASSWORD}\n")
    data = out.json()["data"]
    assert "reputation" in data
    rep = data["reputation"]
    assert "completion_rate" in rep  # real (flat) server contract
    assert "score" not in rep        # the old phantom contract is gone
    # Text display: the reputation line mentions completion.
    out2 = run_cli(["agent", "status", ALICE, "--my-name", ALICE,
                    "--password-stdin"], stdin=f"{ALICE_PASSWORD}\n")
    assert "reputation" in out2.out
    assert "completion" in out2.out
