"""Large-volume seed for the web interface (scale testing).
Creates an organization with many agents/tasks/messages, an observer
account, then prints the launch commands."""
from __future__ import annotations

import json
import random
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

ORG = "grande_org"
ORG_PASSWORD = "motdepasse-grande-1"
OBSERVER = "observateur"
OBS_PASSWORD = "motdepasse-observateur-1"

N_AGENTS = 150
N_TASKS = 220
N_MSGS = 300


def main() -> int:
    out_dir = Path("/tmp/synapse-big")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run").mkdir(parents=True, exist_ok=True)
    config = Config.from_dict({
        "storage_dir": str(out_dir / "data"),
        "socket_path": str(out_dir / "run" / "synapse.sock"),
        "log_dir": str(out_dir / "logs"),
        "backup_dir": str(out_dir / "backups"),
    })
    create_organization(config, ORG, ORG_PASSWORD, ORG_PASSWORD)
    server = SynapseServer(config)
    threading.Thread(target=server.start, daemon=True).start()
    deadline = time.time() + 10
    while True:
        try:
            probe = socklib.socket(socklib.AF_UNIX, socklib.SOCK_STREAM)
            probe.settimeout(1)
            probe.connect(config.socket_path)
            probe.close()
            break
        except OSError:
            if time.time() > deadline:
                raise RuntimeError("the server did not open in time")
            time.sleep(0.2)
    try:
        client = Client(config.socket_path)
        client.create_observer_account(OBSERVER, OBS_PASSWORD, "Supervision",
                                       ORG, ORG_PASSWORD)
        # Agents
        agents = []
        for i in range(N_AGENTS):
            name = f"agent_{i:04d}"
            pwd = f"mdp-{name}"
            client.create_agent(name, pwd,
                                f"Test agent {i} — domain {i % 7}",
                                ORG, ORG_PASSWORD)
            agents.append((name, pwd))
        # Departments
        for d in range(5):
            client.create_department(f"dept-{d}", ORG, ORG_PASSWORD)
        for i, (name, _) in enumerate(agents[:120]):
            client.set_agent_department(name, f"dept-{i % 5}", "employee", ORG, ORG_PASSWORD)
        # Messages
        random.seed(42)
        sent = 0
        while sent < N_MSGS:
            a, pa = random.choice(agents)
            b, _ = random.choice(agents)
            if a == b:
                continue
            client.send_message(b, f"Message {sent} from {a} to {b}", f"big-{sent}", a, pa)
            sent += 1
        # Tasks
        for i in range(N_TASKS):
            creator, cpwd = agents[i % len(agents)]
            assignee, _ = agents[(i * 7) % len(agents)]
            client.create_task(f"Task {i}", assignee, creator, cpwd,
                               priority=random.choice(["low", "normal", "high"]))
        print(f"Organization \"{ORG}\": {N_AGENTS} agents, {N_TASKS} tasks, {N_MSGS} messages.")
    finally:
        server.stop()

    cfg_path = out_dir / "config.json"
    cfg_path.write_text(json.dumps({
        "storage_dir": config.storage_dir,
        "socket_path": config.socket_path,
        "log_dir": config.log_dir,
        "backup_dir": config.backup_dir,
    }, indent=2), encoding="utf-8")
    print(f"Config: {cfg_path}")
    print(f"1) .venv/bin/synapse-server --config {cfg_path}")
    print(f"2) echo '{OBS_PASSWORD}' | .venv/bin/synapse-web --config {cfg_path} --observer-name {OBSERVER} --password-stdin --port 8081")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
