"""Tests of the operations functions introduced by SPEC_PRODUCTION §5
(``version`` command) and §4 (A2A gateway state in ``status``)."""

from __future__ import annotations

import json
import re
from importlib.metadata import version as _pkg_version

from tests.cli_helpers import run_cli


def test_version_flag(cli_env):
    """synapse --version: installed version, no server needed."""
    _, _, env = cli_env
    proc = run_cli(env, "--version")
    assert proc.returncode == 0
    assert proc.stdout.decode().strip() == _pkg_version("synapse-messenger")


def test_version_subcommand(cli_env):
    """synapse version: same version, simple output."""
    _, _, env = cli_env
    proc = run_cli(env, "version")
    assert proc.returncode == 0
    assert proc.stdout.decode().strip() == _pkg_version("synapse-messenger")


def test_version_no_server_required(cli_env):
    """The version depends neither on the server nor on the configuration."""

    _, _, env = cli_env
    env = dict(env)
    env.pop("Synapse_CONFIG", None)
    proc = run_cli(env, "--version")
    assert proc.returncode == 0
    assert proc.stdout.decode().strip() == _pkg_version("synapse-messenger")


def test_status_includes_a2a_stopped(cli_env):
    """synapse status --json exposes the state of the A2A gateway (stopped
    when not provisioned — a legitimate, optional state)."""
    _, _, env = cli_env
    proc = run_cli(env, "status", "--json")
    assert proc.returncode == 0, proc.stderr.decode()
    data = json.loads(proc.stdout.decode())["data"]
    assert "a2a" in data
    assert data["a2a"]["state"] == "stopped"
    assert data["server"]["state"] == "stopped"


def test_fallback_version_matches_pyproject():
    """The version read in a source checkout (package not installed)
    must match the version declared in pyproject.toml — the single
    source of truth is pyproject.toml (synapse/version.py reads it)."""
    import tomllib
    from pathlib import Path

    from synapse.version import project_version

    pyproject = tomllib.loads(
        Path(__file__).resolve().parents[1].joinpath("pyproject.toml").read_text()
    )
    assert project_version() == pyproject["project"]["version"]


def test_readme_install_url_matches_release():
    """The README install command must pin the wheel of the CURRENT
    release — a stale URL installs an older wheel."""
    from pathlib import Path

    readme = Path(__file__).resolve().parents[1].joinpath("README.md").read_text()
    match = re.search(r"releases/download/v([0-9.]+)/synapse_messenger", readme)
    assert match is not None
    assert match.group(1) == "3.1.6"


def test_cli_user_facing_text_is_english():
    """m12: the unified CLI (3.1.3 '100% English') must not leak French
    user-facing labels/pagination strings."""
    from pathlib import Path

    cli_dir = Path(__file__).resolve().parents[1] / "synapse" / "cli"
    french_leaks = []
    for py in sorted(cli_dir.glob("*.py")):
        text = py.read_text()
        for token in ("horodatage", "page suivante", "service indisponible",
                      "archive manquante", "mot de passe", "non lu"):
            if token in text:
                french_leaks.append(f"{py.name}:{token}")
    assert not french_leaks, f"French leaks in CLI output: {french_leaks}"
