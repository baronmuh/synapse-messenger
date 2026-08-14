"""Regression guard: real DOM verification harness (SPEC-WEB D5).

The scripts/webui-dom-check/verify.mjs harness loads the full application
in jsdom wired to a REAL backend (seed_demo.py demo server + synapse-server
+ synapse-web), logs in as a human (organization login + password, session
cookie), then verifies the rendering of the 8 views, the live org chart,
the Conversations view (list/detail/composer) and the absence of console
errors.

This is the real rendering proof (JS runs, the DOM is real, the data comes
from the real server) — the level between the HTTP tests of test_webui.py
and a visual browser review. Skipped if node/jsdom are not available
(environments without Node).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from synapse.config import Config

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "scripts" / "webui-dom-check"
VENV_BIN = REPO / ".venv" / "bin"

ORG = "acme_ia"
ORG_PASSWORD = "motdepasse-acme-1"


def _node_jsdom_available() -> bool:
    if shutil.which("node") is None:
        return False
    return (HARNESS / "node_modules" / "jsdom" / "package.json").exists()


pytestmark = pytest.mark.skipif(
    not _node_jsdom_available(),
    reason="node or jsdom unavailable (real DOM harness)",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.3)
    raise AssertionError(f"port {port} jamais ouvert")


def _wait_socket(path, timeout: float = 20.0) -> None:
    """Waits until the server's Unix socket responds (the web refuses to
    start until the server is ready — SPEC_CLI §4.3)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(path)
                return
        except OSError:
            time.sleep(0.3)
    raise AssertionError(f"socket {path} never ready")


def test_webui_dom_harness_sessions():
    """The real DOM harness passes with the selection-based session model:
    login screen (dropdown of active organizations), then selection login
    and rendering of the 8 views (live org chart, conversations
    list/detail/composer) without console errors."""
    with tempfile.TemporaryDirectory(prefix="synapse-dom-") as tmp:
        seed = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "seed_demo.py"), "--dir", tmp],
            cwd=REPO, capture_output=True, text=True, timeout=180,
            env={**os.environ, "SYNAPSE_FAST_HASH": "1"})
        assert seed.returncode == 0, seed.stderr

        port = _free_port()
        server = subprocess.Popen(
            [str(VENV_BIN / "synapse-server"), "--config", f"{tmp}/config.json"],
            cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            _wait_socket(f"{tmp}/run/synapse.sock")
            web = subprocess.Popen(
                [str(VENV_BIN / "synapse-web"), "--config", f"{tmp}/config.json",
                 "--port", str(port)],
                cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except AssertionError:
            # The server did not start: keep the original error visible.
            out, err = server.communicate(timeout=5)
            raise AssertionError(
                f"test server not ready\n{out.decode(errors='replace')}\n"
                f"{err.decode(errors='replace')}"
            ) from None
        try:
            _wait_port(port)
            base_env = {**os.environ, "SYNAPSE_WEB_ORIGIN": f"http://127.0.0.1:{port}"}

            # 1) Selection-based login screen (no session).
            login_run = subprocess.run(
                ["node", "verify_login.mjs"], cwd=HARNESS, env=base_env,
                capture_output=True, text=True, timeout=120)
            assert login_run.returncode == 0, login_run.stdout + login_run.stderr
            login_report = json.loads(login_run.stdout)
            assert login_report["selectPresent"] is True
            assert ORG in login_report["options"], login_report
            assert login_report["buttonLabel"] == "Sign in"
            # Organization creation from the login page (D5 amended).
            assert login_report["createModeBtn"] is True, login_report
            assert login_report["createFormPresent"] is True, login_report
            assert login_report["backToLoginAfterToggle"] is True, login_report
            assert login_report["cssRulesPresent"] is True, \
                "CSS rule .login-root[hidden] missing — the login window would not hide after login"
            assert login_report["consoleErrors"] == []

            # 2) Selection login + views rendering.
            env = {**base_env, "SYNAPSE_WEB_ORG": ORG}
            run = subprocess.run(
                ["node", "verify.mjs"], cwd=HARNESS, env=env,
                capture_output=True, text=True, timeout=180)
            assert run.returncode == 0, f"harness failed (code {run.returncode})\n{run.stdout}\n{run.stderr}"

            report = json.loads(run.stdout)
            assert "acme_ia_humain" in report["sessionIdentity"], report
            assert report["appMounted"] is True
            assert report["appVisible"] is True
            assert report["noNavShortcuts"] is True, \
                "shortcut hints (g d / g a / g c) still shown in the sidebar"
            assert report["loginHiddenAfterAuth"] is True, \
                "login window still visible after login (hidden missing)"
            assert report["orgChartExists"] is True
            assert report["orgChartNodes"] > 0
            # Conversations: switch [Agent ↔ Agent | Human ↔ Agent]
            assert report["convSwitch"] == ["Agent ↔ Agent", "Human ↔ Agent"], report
            assert report["convDefaultMode"] == "Agent ↔ Agent", report
            assert report["conversationsList"] > 0
            # Agent ↔ Agent view: read-only, both sides alternate.
            assert report["aaThread"] is True
            assert report["aaComposerAbsent"] is True
            assert report["aaBothSides"] is True, \
                "Agent ↔ Agent view: no left/right alternation of the two interlocutors"
            assert report["aaHeadShowsBoth"] is True
            # Smooth refresh: the thread is not rebuilt and
            # "Loading content…" does not reappear.
            assert report["threadStableAfterRefresh"] is True
            assert report["noReloadSpinner"] is True
            # Human ↔ Agent view: dedicated list + composer + unread.
            assert report["haActive"] == "Human ↔ Agent", report
            assert report["haList"] > 0
            assert report["haUnreadBadgeBefore"] is True, \
                "the received message (agent -> human) is not marked unread"
            assert report["conversationThread"] is True
            assert report["conversationInput"] is True
            assert report["haUnreadBadgeAfter"] is True, \
                "the unread badge does not disappear after viewing the conversation"
            # Back to Agent ↔ Agent: no composer anymore.
            assert report["backToAA"] == "Agent ↔ Agent", report
            assert report["aaComposerAbsentAfterSwitch"] is True
            assert report["consoleErrors"] == [], report["consoleErrors"]
            for route in ("dashboard", "agents", "communications", "tasks",
                          "activity", "server", "conversations"):
                assert report[f"route_{route}"]["errors"] == 0, route
        finally:
            for proc in (web, server):
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()


def test_webui_onboarding_redirect_and_dom():
    """The onboarding gate: without any organization, `/` redirects to
    /onboarding (interactive guide); with the DOM harness, the page
    renders the 6 sections, the 5-step workflow, the request example and
    the explicit validation mention (I APPROVE THIS PLAN)."""
    if shutil.which("node") is None:
        pytest.skip("node absent")
    with tempfile.TemporaryDirectory(prefix="synapse-ob-") as tmp:
        for d in ("data", "run", "logs", "backups"):
            (Path(tmp) / d).mkdir(parents=True)
        import json as _json
        (Path(tmp) / "config.json").write_text(_json.dumps({
            "storage_dir": f"{tmp}/data",
            "socket_path": f"{tmp}/run/synapse.sock",
            "run_dir": f"{tmp}/run",
            "log_dir": f"{tmp}/logs",
            "backup_dir": f"{tmp}/backups",
        }))
        port = _free_port()
        server = subprocess.Popen(
            [str(VENV_BIN / "synapse-server"), "--config", f"{tmp}/config.json"],
            cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            _wait_socket(f"{tmp}/run/synapse.sock")
            web = subprocess.Popen(
                [str(VENV_BIN / "synapse-web"), "--config", f"{tmp}/config.json",
                 "--port", str(port)],
                cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except AssertionError:
            out, err = server.communicate(timeout=5)
            raise AssertionError(
                f"test server not ready\n{out.decode(errors='replace')}\n"
                f"{err.decode(errors='replace')}"
            ) from None
        try:
            _wait_port(port)
            # 1) Redirect: / -> /onboarding when no org exists.
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                assert resp.status == 200
                assert resp.geturl().endswith("/onboarding"), resp.geturl()
            # 2) DOM harness on the onboarding page.
            env = {**os.environ, "SYNAPSE_WEB_ORIGIN": f"http://127.0.0.1:{port}"}
            run = subprocess.run(
                ["node", "verify_onboarding.mjs"], cwd=HARNESS, env=env,
                capture_output=True, text=True, timeout=120)
            assert run.returncode == 0, f"{run.stdout}\n{run.stderr}"
            assert "ONBOARDING DOM OK" in run.stdout
        finally:
            for proc in (web, server):
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()


def test_webui_onboarding_login_route_and_lockout_exemption():
    """Regression guards for the onboarding flow fixes (2026-08-09):

    1. /login must be served WITHOUT the onboarding gate (the onboarding
       buttons point there — a direct link to "/" loops back to
       /onboarding while no org exists).
    2. Creating an organization via POST /api/orgs must still work after
       failed login attempts: the local web identity (_WEB_LOCAL) is
       exempt from the failure lockout, otherwise a few mistyped
       passwords make the first-organization creation impossible.
    """
    with tempfile.TemporaryDirectory(prefix="synapse-obfix-") as tmp:
        for d in ("data", "run", "logs", "backups"):
            (Path(tmp) / d).mkdir(parents=True)
        import json as _json
        (Path(tmp) / "config.json").write_text(_json.dumps({
            "storage_dir": f"{tmp}/data",
            "socket_path": f"{tmp}/run/synapse.sock",
            "run_dir": f"{tmp}/run",
            "log_dir": f"{tmp}/logs",
            "backup_dir": f"{tmp}/backups",
        }))
        port = _free_port()
        server = subprocess.Popen(
            [str(VENV_BIN / "synapse-server"), "--config", f"{tmp}/config.json"],
            cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            _wait_socket(f"{tmp}/run/synapse.sock")
            web = subprocess.Popen(
                [str(VENV_BIN / "synapse-web"), "--config", f"{tmp}/config.json",
                 "--port", str(port)],
                cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except AssertionError:
            out, err = server.communicate(timeout=5)
            raise AssertionError(
                f"test server not ready\n{out.decode(errors='replace')}\n"
                f"{err.decode(errors='replace')}"
            ) from None
        import urllib.request
        import urllib.error
        try:
            _wait_port(port)
            base = f"http://127.0.0.1:{port}"

            # 1) /login served without the gate (no org exists).
            req = urllib.request.Request(f"{base}/login")
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 200
                assert resp.geturl().endswith("/login"), resp.geturl()
                body = resp.read().decode()
            assert "index.html" in body or "login" in body.lower()

            # 2) lockout exemption: the web creates the first org even
            # after failed HUMAN authentications on the socket API
            # (the local web identity must never be blocked by the
            # failure lockout — otherwise mistyped passwords make the
            # first-organization creation impossible).
            from synapse.client import Client
            cli = Client.from_config(Config.load(f"{tmp}/config.json"))

            def post(path, payload):
                data = _json.dumps(payload).encode()
                r = urllib.request.Request(f"{base}{path}", data=data,
                                           headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(r, timeout=5) as resp:
                        return resp.status, _json.loads(resp.read().decode())
                except urllib.error.HTTPError as e:
                    return e.code, _json.loads(e.read().decode())

            # accumulate failed human logins via the socket API
            for _ in range(4):
                try:
                    cli.get_my_organization("nobody", "wrong-password")
                except Exception:
                    pass
            # creating the first org via the web must still succeed
            s, _ = post("/api/orgs", {
                "organization_name": "lock_test",
                "organization_password": "mdp-lock-test-123",
            })
            assert s == 200, f"org creation blocked after lockout: {s}"
        finally:
            for proc in (web, server):
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
