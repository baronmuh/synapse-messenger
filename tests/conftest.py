"""Shared fixtures: temporary configuration, server, clients.

All tests run on temporary storage/socket: nothing is written outside the
test space. The Argon2id hasher is replaced by a fast instance for the
duration of the session (a dedicated test verifies the production
parameters).
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from synapse import security
from synapse.client import Client
from synapse.config import Config
from synapse.install import create_organization
from synapse.server import SynapseServer

pytest_plugins = ["tests.web_helpers"]

ORG_NAME = "root_org"
ORG_PASSWORD = "mot-de-passe-org-123"
ORG2_NAME = "second_org"
ORG2_PASSWORD = "mot-de-passe-org2-456"
ALICE = "alice"
ALICE_PASSWORD = "motdepasse-alice-1"
ALICE_DESCRIPTION = "Test agent alice: sends and receives messages"
BOB = "bob"
BOB_PASSWORD = "motdepasse-bob-1"
BOB_DESCRIPTION = "Test agent bob: sends and receives messages"


@pytest.fixture(scope="session", autouse=True)
def _fast_hasher():
    """Fast hasher for the whole session (security tests validate the
    production parameters separately)."""
    security.install_fast_hasher()
    yield
    security.install_production_hasher()


@pytest.fixture(scope="session", autouse=True)
def _no_orphaned_test_daemons():
    """Sweeps leftover test daemons around the session (auditor F1).

    A pytest worker killed mid-test can orphan the ``synapse server``/
    ``web``/``a2a`` daemon it started. The parent-watch
    (``SYNAPSE_WATCH_PARENT``, set by cli_helpers) prevents NEW orphans;
    this sweep removes leftovers from previously crashed runs at session
    start and end. Only daemons whose config lives under the system temp
    dir are touched — production daemons are never affected.
    """
    from tests.cli_helpers import sweep_orphan_test_daemons

    sweep_orphan_test_daemons()
    yield
    sweep_orphan_test_daemons()


@pytest.fixture()
def config(tmp_path) -> Config:
    """Isolated test configuration (storage, socket, logs in tmp_path)."""
    conf = {
        "storage_dir": str(tmp_path / "data"),
        "socket_path": str(tmp_path / "run" / "synapse.sock"),
        "log_dir": str(tmp_path / "logs"),
        "backup_dir": str(tmp_path / "backups"),
    }
    return Config.from_dict(conf)


# ---------------------------------------------------------------------------
# Unified CLI: disposable configuration + subprocess execution
# ---------------------------------------------------------------------------


@pytest.fixture()
def cli_env(tmp_path):
    """(config, config_file, env) for the CLI: isolated JSON configuration
    resolved via ``$Synapse_CONFIG`` (SPEC_CLI §2 search order)."""
    from tests.cli_helpers import cli_env_data

    return cli_env_data(tmp_path)


def make_server(config: Config, org: bool = True) -> "ServerFixture":
    """Starts a full server on the given configuration.

    If ``org`` is true, the test organization ``root_org`` is created.
    """
    from synapse.logging_setup import setup_logging

    setup_logging(config)  # real logging (files in log_dir)
    if org:
        create_organization(config, ORG_NAME, ORG_PASSWORD, ORG_PASSWORD)
    server = SynapseServer(config)
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()
    # wait until the socket actually accepts connections (not just that the
    # file exists: there is a bind -> listen window)
    deadline = time.time() + 10
    while True:
        probe = None
        try:
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(1)
            probe.connect(config.socket_path)
            probe.close()
            break
        except OSError:
            if probe is not None:
                probe.close()
            if time.time() > deadline:
                server.stop()
                raise RuntimeError("The server did not start in time")
            time.sleep(0.02)
    return ServerFixture(config, server)


class ServerFixture:
    """Test server + convenient clients."""

    def __init__(self, config: Config, server: SynapseServer) -> None:
        self.config = config
        self.server = server
        self.client = Client(config.socket_path)

    # -- helpers --------------------------------------------------------
    def create_agent(self, username: str, password: str, description: str = "Test agent",
                     org_name=ORG_NAME, org_password=ORG_PASSWORD,
                     can_see_org_agents: bool = False) -> dict:
        return self.client.create_agent(
            username, password, description, org_name, org_password,
            can_see_org_agents=can_see_org_agents,
        )

    def org(self, name=ORG_NAME, password=ORG_PASSWORD) -> Client:
        return Client(self.config.socket_path)

    def send(self, sender: str, password: str, recipient: str, content: str,
             client_message_id: str, business_reference: str | None = None) -> dict:
        return self.client.send_message(
            recipient, content, client_message_id, sender, password,
            business_reference=business_reference,
        )

    def stop(self) -> None:
        self.server.stop()


@pytest.fixture()
def fx(config) -> ServerFixture:
    """Ready test server (organization created, alice/bob agents created).

    The authentication cache is cleared after setup: the server behaves as
    freshly started (setup authentications must not pollute the auth-failure
    tests)."""
    server = make_server(config)
    try:
        server.create_agent(ALICE, ALICE_PASSWORD, ALICE_DESCRIPTION)
        server.create_agent(BOB, BOB_PASSWORD, BOB_DESCRIPTION)
        server.server.service._auth_cache.clear()
        yield server
    finally:
        server.stop()


@pytest.fixture()
def raw_socket_client(config):
    """Returns a function that sends a raw JSON request (line) and
    returns the parsed raw JSON response, without going through Client."""
    def _send(line: str) -> dict:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(config.socket_path)
            sock.sendall(line.encode("utf-8"))
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return json.loads(b"".join(chunks).decode("utf-8"))
        finally:
            sock.close()
    return _send
