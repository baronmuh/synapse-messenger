"""Auditor F1: a killed pytest worker must never orphan a daemon.

The parent-watch (``SYNAPSE_WATCH_PARENT=1``, set by cli_helpers for every
CLI test) makes the ``synapse server``/``web``/``a2a`` daemon exit when
the process that started it disappears — even on SIGKILL, where no
atexit/finally cleanup can run. The integration test below kills a real
worker mid-run and proves the daemon count for its config returns to
zero; the regression test proves production semantics are unchanged
(without the watch, a detached daemon survives its starter).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from synapse.cli.daemon import (
    _install_parent_watch,
    _parent_gone,
    _process_starttime,
)

from tests.cli_helpers import daemon_pids_for, run_cli

# A "worker": starts the server through the CLI (as a real pytest worker
# would), then stays alive until it is killed.
_WORKER = """
import os, subprocess, sys, time
subprocess.run([sys.executable, "-m", "synapse.cli", "server", "start"],
               env=dict(os.environ), timeout=60)
time.sleep(300)
"""


def _wait_server(env, timeout: float = 20.0) -> None:
    """Waits until ``synapse server status --json`` reports running."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        proc = run_cli(env, "server", "status", "--json")
        if proc.returncode == 0:
            data = json.loads(proc.stdout.decode()).get("data") or {}
            if data.get("state") == "running":
                return
        time.sleep(0.2)
    pytest.fail("server did not reach running state")


def test_killed_worker_leaves_no_orphan_daemon(cli_env):
    """SIGKILL the worker after ``server start``: the daemon exits with
    it (parent-watch), so the daemon count for the config is zero
    afterwards — the audit scenario (killed worker, no atexit/finally
    ever runs)."""
    _, config_file, env = cli_env
    worker = subprocess.Popen([sys.executable, "-c", _WORKER], env=env)
    try:
        _wait_server(env)
        assert daemon_pids_for(config_file) != [], "daemon must be running"
        assert len(daemon_pids_for(config_file)) == 1

        worker.kill()  # SIGKILL: the worker cannot run any cleanup
        worker.wait()

        deadline = time.time() + 15
        while time.time() < deadline and daemon_pids_for(config_file):
            time.sleep(0.2)
        assert daemon_pids_for(config_file) == [], (
            "killed worker left an orphaned daemon")
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()
        run_cli(env, "server", "stop")  # belt and braces


def test_daemon_survives_starter_death_without_watch(cli_env):
    """Without SYNAPSE_WATCH_PARENT the daemon keeps production
    semantics: a detached service survives the process that started it.
    The parent-watch must stay strictly opt-in (test harness only)."""
    _, config_file, env = cli_env
    env = {k: v for k, v in env.items() if k != "SYNAPSE_WATCH_PARENT"}
    worker = subprocess.Popen([sys.executable, "-c", _WORKER], env=env)
    try:
        _wait_server(env)
        worker.kill()
        worker.wait()
        # longer than the watch poll interval: the daemon must still live
        time.sleep(2.5)
        assert daemon_pids_for(config_file) != [], (
            "daemon must survive without the parent-watch")
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()
        proc = run_cli(env, "server", "stop")
        assert proc.returncode == 0, proc.stderr.decode()
        deadline = time.time() + 15
        while time.time() < deadline and daemon_pids_for(config_file):
            time.sleep(0.2)
        assert daemon_pids_for(config_file) == [], "cleanup failed"


def test_parent_gone_identity_and_starttime():
    """The watch helper: a live parent with a matching start time is not
    gone; a dead parent (or a mismatched start time, i.e. PID reuse) is."""
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        start = _process_starttime(sleeper.pid)
        assert start is not None
        assert _parent_gone(sleeper.pid, start) is False
        # a different start time means "not the watched parent"
        assert _parent_gone(sleeper.pid, "0") is True
    finally:
        sleeper.kill()
        sleeper.wait()
    assert _parent_gone(sleeper.pid, start) is True
    assert _parent_gone(999_999_999, None) is True
    assert _process_starttime(999_999_999) is None


def test_watch_parent_env_opt_in(monkeypatch):
    """SYNAPSE_WATCH_PARENT=1 carries the invoker's identity; without it
    nothing is injected (production daemons stay unwatched)."""
    from synapse.cli.daemon import watch_parent_env

    monkeypatch.delenv("SYNAPSE_WATCH_PARENT", raising=False)
    assert watch_parent_env() is None

    monkeypatch.setenv("SYNAPSE_WATCH_PARENT", "1")
    watch = watch_parent_env()
    assert watch is not None
    assert watch["SYNAPSE_DAEMON_PARENT_PID"] == str(os.getppid())
    assert watch["SYNAPSE_DAEMON_PARENT_START"]


def test_install_parent_watch_no_env_is_noop(monkeypatch):
    """Without the daemon parent env vars no watcher thread is started
    (production daemons never watch anything)."""
    monkeypatch.delenv("SYNAPSE_DAEMON_PARENT_PID", raising=False)
    fired = []
    _install_parent_watch(lambda: fired.append(True))
    time.sleep(0.2)  # a thread (if any) would have fired by now
    assert fired == []


def test_install_parent_watch_fires_when_parent_already_gone(monkeypatch):
    """A watcher pointed at an already-dead parent fires immediately —
    the daemon exits instead of outliving a killed worker."""
    monkeypatch.setenv("SYNAPSE_DAEMON_PARENT_PID", "999999999")
    monkeypatch.setenv("SYNAPSE_DAEMON_PARENT_START", "0")
    fired = []
    _install_parent_watch(lambda: fired.append(True))
    deadline = time.time() + 5
    while not fired and time.time() < deadline:
        time.sleep(0.05)
    assert fired == [True]
