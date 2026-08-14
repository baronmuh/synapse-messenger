"""Tests for the unified ``synapse`` CLI (SPEC_CLI.md) — raw ``api`` access.

Passwords are never passed as command-line arguments: they are read
from standard input (--password-stdin or getpass).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

from .conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB_PASSWORD

CLI = [sys.executable, "-m", "synapse.cli"]


def _run_cli(config, *args, stdin: str = ""):
    """Run the CLI with the test configuration (written as a JSON file,
    resolved via $Synapse_CONFIG — SPEC_CLI §2 search order)."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config.to_dict(), fh)
    env = dict(os.environ)
    env["Synapse_CONFIG"] = path
    try:
        proc = subprocess.run(
            CLI + [*args],
            input=stdin.encode(),
            capture_output=True,
            env=env,
        )
    finally:
        os.unlink(path)
    return proc


def test_cli_org_flow(fx, config):
    """create_agent via ``api`` (agent password + org on stdin)."""
    proc = _run_cli(
        config,
        "api", "create_agent",
        "--username", "carol",
        "--description", "Agent carol de test",
        "--organization-name", ORG_NAME,
        "--password-stdin",
        stdin=f"motdepasse-carol-1\n{ORG_PASSWORD}\n",
    )
    assert proc.returncode == 0, proc.stderr.decode()
    envelope = json.loads(proc.stdout.decode())
    assert envelope["success"] is True
    assert envelope["data"]["username"] == "carol"


def test_cli_agent_flow(fx, config):
    proc = _run_cli(
        config,
        "api", "send_message",
        "--recipient-username", "bob",
        "--message", "hello via CLI",
        "--client-message-id", "cmid-cli-1",
        "--my-name", ALICE,
        "--password-stdin",
        stdin=f"{ALICE_PASSWORD}\n",
    )
    assert proc.returncode == 0, proc.stderr.decode()
    envelope = json.loads(proc.stdout.decode())
    assert envelope["success"] is True
    assert envelope["data"]["recipient_username"] == "bob"

    proc = _run_cli(
        config,
        "api", "get_messages",
        "--my-name", "bob",
        "--password-stdin",
        stdin=f"{BOB_PASSWORD}\n",
    )
    assert proc.returncode == 0, proc.stderr.decode()
    envelope = json.loads(proc.stdout.decode())
    assert envelope["data"]["messages"][0]["content"] == "hello via CLI"


def test_cli_help(fx, config):
    """``api help``: built-in service documentation."""
    proc = _run_cli(
        config,
        "api", "help",
        "--my-name", ALICE,
        "--password-stdin",
        stdin=f"{ALICE_PASSWORD}\n",
    )
    assert proc.returncode == 0, proc.stderr.decode()
    envelope = json.loads(proc.stdout.decode())
    assert envelope["success"] is True
    assert "COMMAND: send_message" in envelope["data"]["documentation"]
    assert "COMMAND: help" in envelope["data"]["documentation"]


def test_cli_help_targeted(fx, config):
    proc = _run_cli(
        config,
        "api", "help",
        "--command-name", "send_message",
        "--my-name", ALICE,
        "--password-stdin",
        stdin=f"{ALICE_PASSWORD}\n",
    )
    assert proc.returncode == 0, proc.stderr.decode()
    envelope = json.loads(proc.stdout.decode())
    doc = envelope["data"]["documentation"]
    assert "COMMAND: send_message" in doc
    assert "COMMAND: help" not in doc
    assert "Example: {" in doc


def test_cli_error_envelope(fx, config):
    proc = _run_cli(
        config,
        "api", "get_messages",
        "--my-name", ALICE,
        "--password-stdin",
        stdin="mauvais-motdepasse\n",
    )
    envelope = json.loads(proc.stdout.decode())
    assert envelope["success"] is False
    assert envelope["error"]["code"] == "AUTH_FAILED"


def test_cli_passwords_not_in_argv(fx, config):
    """The password never shows up in the command line."""
    proc = _run_cli(
        config,
        "api", "get_messages",
        "--my-name", ALICE,
        "--password-stdin",
        stdin=f"{ALICE_PASSWORD}\n",
    )
    assert proc.returncode == 0
    # the password does not appear in the process arguments
    cmdline = " ".join(CLI)
    assert ALICE_PASSWORD not in cmdline


def test_cli_unknown_command(fx, config):
    proc = _run_cli(config, "api", "frobnicate")
    assert proc.returncode != 0
    envelope = json.loads(proc.stdout.decode())
    assert envelope["error"]["code"] == "UNKNOWN_COMMAND"


def test_cli_structured_command(fx, config):
    """A structured command (org group) with a local token: no password
    requested (rule 7 of SPEC_CLI §5)."""
    proc = _run_cli(config, "org", "list", "--json")
    assert proc.returncode == 0, proc.stderr.decode()
    envelope = json.loads(proc.stdout.decode())
    assert {o["organization_name"] for o in envelope["data"]["organizations"]} \
        == {ORG_NAME}
