"""Minimal seed for the web interface (testing empty states).
Creates a nearly empty organization (1 agent, 0 messages, 0 tasks) and an
observer account, then prints the launch commands."""
from __future__ import annotations

import json
import shutil
import socket as socklib
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from synapse.client import Client
from synapse.config import Config
from synapse.install import create_organization
from synapse.server import SynapseServer

BASE = Path("/tmp/synapse-empty")

ORG = "org_vide"
ORG_PASSWORD = "motdepasse-vide-1"
OBSERVER = "observateur"
OBS_PASSWORD = "motdepasse-observateur-1"


def wait_socket(path: str) -> None:
    deadline = time.time() + 10
    while True:
        try:
            probe = socklib.socket(socklib.AF_UNIX, socklib.SOCK_STREAM)
            probe.settimeout(1)
            probe.connect(path)
            probe.close()
            return
        except OSError:
            if time.time() > deadline:
                raise RuntimeError("the server did not open in time")
            time.sleep(0.2)


def main() -> int:
    if BASE.exists():
        shutil.rmtree(BASE)
    (BASE / "run").mkdir(parents=True)
    config = Config.from_dict({
        "storage_dir": str(BASE / "data"),
        "socket_path": str(BASE / "run" / "synapse.sock"),
        "log_dir": str(BASE / "logs"),
        "backup_dir": str(BASE / "backups"),
    })
    create_organization(config, ORG, ORG_PASSWORD, ORG_PASSWORD)
    server = SynapseServer(config)
    threading.Thread(target=server.start, daemon=True).start()
    wait_socket(config.socket_path)
    try:
        client = Client(config.socket_path)
        client.create_observer_account(OBSERVER, OBS_PASSWORD, "Supervision",
                                       ORG, ORG_PASSWORD)
        # 1 single agent
        client.create_agent("seul_agent", "motdepasse-agent-1",
                            "Seul agent de test", ORG, ORG_PASSWORD)
    finally:
        server.stop()

    (BASE / "config.json").write_text(json.dumps({
        "storage_dir": config.storage_dir,
        "socket_path": config.socket_path,
        "log_dir": config.log_dir,
        "backup_dir": config.backup_dir,
    }, indent=2), encoding="utf-8")
    print("org_vide ready (1 agent, 0 messages, 0 tasks)")
    print(f"1) .venv/bin/synapse-server --config {BASE / 'config.json'}")
    print(f"2) echo '{OBS_PASSWORD}' | .venv/bin/synapse-web --config {BASE / 'config.json'} "
          f"--observer-name {OBSERVER} --password-stdin --port 8082")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
