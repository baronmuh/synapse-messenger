"""Demo scenario for the web interface (development tool).

Creates a realistic example organization — an "AI agents company":
departments, agents with capability cards, messages (some unread),
tasks in every state (including one overdue and one pending approval),
observer account — then prints the commands to launch the interface.

Usage:
    python scripts/seed_demo.py                # writes to ./demo (default)
    python scripts/seed_demo.py --dir /tmp/synapse-demo
    synapse-web --config <dir>/config.json   # login: org + password
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from synapse.client import Client  # noqa: E402
from synapse.config import Config  # noqa: E402
from synapse.install import create_organization  # noqa: E402
from synapse.server import SynapseServer  # noqa: E402
from synapse.validation import now_utc_offset  # noqa: E402

ORG = "acme_ia"
ORG_PASSWORD = "motdepasse-acme-1"
OBSERVER = "observateur"
OBS_PASSWORD = "motdepasse-observateur-1"

AGENTS = [
    # (username, password, description, department, role, capabilities, domain)
    ("director", "mdp-directeur-1", "Leads the organization: arbitrates, validates, plans.",
     "direction", "manager", ["planification", "arbitrage", "validation"], "direction"),
    ("resources", "mdp-resources-1", "Manages resources: recruits, trains, evaluates agents.",
     "direction", "rh", ["recrutement", "evaluation", "formation"], "ressources-humaines"),
    ("accountant", "mdp-comptable-1", "Keeps the books: invoices, budgets, reporting.",
     "finance", "employee", ["comptabilite", "budget", "reporting-financier"], "finance"),
    ("analyst", "mdp-analyste-1", "Analyzes business data and produces reports.",
     "finance", "employee", ["analyse-donnees", "statistiques", "rapports"], "analyse"),
    ("support", "mdp-support-1", "Answers incoming requests and escalates incidents.",
     "support", "employee", ["support-client", "diagnostic", "escalade"], "support"),
    ("sales", "mdp-commercial-1", "Grows revenue and tracks prospects.",
     "marketing", "employee", ["prospection", "suivi-clients", "negociation"], "commercial"),
    ("devops", "mdp-devops-1", "Operates the infrastructure: deployments, monitoring, SRE.",
     "operations", "employee", ["deploiement", "surveillance", "incidents"], "infrastructure"),
    ("data", "mdp-donnees-1", "Prepares the data and maintains the pipelines.",
     "operations", "employee", ["pipelines", "etl", "qualite-donnees"], "donnees"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="demo", help="demo directory")
    args = parser.parse_args()

    base = Path(args.dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    config = Config.from_dict({
        "storage_dir": str(base / "data"),
        "socket_path": str(base / "run" / "synapse.sock"),
        "log_dir": str(base / "logs"),
        "backup_dir": str(base / "backups"),
    })
    (base / "run").mkdir(parents=True, exist_ok=True)

    config_path = base / "config.json"
    config_path.write_text(json.dumps({
        "storage_dir": config.storage_dir,
        "socket_path": config.socket_path,
        "log_dir": config.log_dir,
        "backup_dir": config.backup_dir,
    }, indent=2), encoding="utf-8")

    create_organization(config, ORG, ORG_PASSWORD, ORG_PASSWORD)
    server = SynapseServer(config)
    import threading
    threading.Thread(target=server.start, daemon=True).start()

    import time
    import socket as socklib
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
                print("The server did not start", file=sys.stderr)
                return 1
            time.sleep(0.05)

    c = Client(config.socket_path)

    # --- Organization -----------------------------------------------------
    for dept in {a[3] for a in AGENTS}:
        c.create_department(dept, ORG, ORG_PASSWORD)
    for username, password, description, dept, role, caps, domain in AGENTS:
        c.create_agent(username, password, description, ORG, ORG_PASSWORD,
                       can_see_org_agents=True)
        c.set_agent_card(caps, username, password, domain=domain,
                         model="synapse-agent-1", sla="response < 1 h",
                         limits="max 10 active tasks",
                         estimated_cost="0.01 € / task")
        c.set_agent_department(username, dept, role, ORG, ORG_PASSWORD)

    # --- Messages (some stay unread) --------------------------------------
    convs = [
        ("sales", "accountant", ["Invoice client 4711 sent", "Please take it into account"]),
        ("accountant", "sales", ["Received, I will process it this morning"]),
        ("support", "devops", ["Latency incident reported by a client", "I suspect the cache"]),
        ("devops", "support", ["Cache invalidated, latency back to normal"]),
        ("director", "resources", ["Where is the quarterly evaluation?"]),
        ("resources", "director", ["Report ready, awaiting your validation"]),
        ("analyst", "accountant", ["Monthly report available"]),
    ]
    mid = 0
    for sender, recipient, contents in convs:
        for content in contents:
            mid += 1
            c.send_message(recipient, content, f"demo-{mid}", sender,
                           AGENTS[[a[0] for a in AGENTS].index(sender)][1])
    # the observer reads some messages? no: the observer is never a
    # participant. We mark the handled exchanges as read to get a realistic
    # read / unread mix (some conversations stay "pending").
    def mark_read(recipient: str, sender: str) -> None:
        pwd = AGENTS[[a[0] for a in AGENTS].index(recipient)][1]
        for m in c.get_messages(recipient, pwd, sender_username=sender)["messages"]:
            c.read_message(m["message_id"], recipient, pwd)

    mark_read("accountant", "sales")
    mark_read("accountant", "analyst")
    mark_read("devops", "support")
    mark_read("director", "resources")

    # --- Human ↔ agent exchange (web "Human ↔ Agent" view) ---------------
    human = f"{ORG}_humain"
    c.send_message("support", "Hello, a client reports a regression after the deployment.",
                   "demo-human-1", human, ORG_PASSWORD)
    c.send_message(human, "Thank you, I will check and get back to you.",
                   "demo-human-2", "support",
                   AGENTS[[a[0] for a in AGENTS].index("support")][1])

    # --- Tasks (every state, one overdue, one pending approval) -----------
    def task(title, assignee, priority="normal", due_hours=None, state=None,
             creator="director", result=None, approver=None):
        pwd = AGENTS[[a[0] for a in AGENTS].index(assignee)][1]
        cpwd = AGENTS[[a[0] for a in AGENTS].index(creator)][1]
        t = c.create_task(title, assignee, creator, cpwd,
                          description=f"Description of \"{title}\"",
                          priority=priority,
                          due_at=now_utc_offset(3600 * due_hours) if due_hours is not None else None)
        tid = t["task_id"]
        if state in ("in_progress", "completed", "failed", "canceled"):
            c.update_task_state(tid, "in_progress", assignee, pwd)
        if state == "pending_approval":
            c.update_task_state(tid, "in_progress", assignee, pwd)
            assert approver is not None  # required for this state
            c.request_approval(tid, approver, assignee, pwd)
        elif state == "completed":
            c.update_task_state(tid, "completed", assignee, pwd, result=result)
        elif state == "failed":
            c.update_task_state(tid, "failed", assignee, pwd, result=result)
        elif state == "canceled":
            c.update_task_state(tid, "canceled", assignee, pwd)
        return tid

    task("Prepare the first-semester balance sheet", "accountant", "high", 48, "in_progress")
    task("Respond to client incident 4711", "support", "high", -3, "in_progress")  # overdue
    task("Q3 prospecting campaign", "sales", "normal", 168, "submitted")
    task("Security audit of the pipelines", "data", "high", 72, "in_progress")
    task("Update the incident runbook", "devops", "low", None, "submitted")
    task("Validate the quarterly evaluation report", "resources", "normal", 24,
         "pending_approval", approver="director")
    task("Deploy the new dashboard", "devops", "high", -24, "completed",
         result="deployed and verified")
    task("Migration of the historical database", "data", "normal", -48, "failed",
         result="failed on the integrity check")
    task("Old request without follow-up", "support", "low", None, "canceled")

    # --- Observer ----------------------------------------------------------
    c.create_observer_account(OBSERVER, OBS_PASSWORD, "Supervision of the organization",
                              ORG, ORG_PASSWORD)

    server.stop()
    print(f"Organization \"{ORG}\" ready: {len(AGENTS)} agents, 9 tasks, "
          f"{mid} messages.")
    print(f"Config: {config_path}")
    print("Launching the interface (two terminals, SPEC-WEB — no secret "
          "at startup):\n"
          f"  1) .venv/bin/synapse-server --config {config_path}\n"
          f"  2) .venv/bin/synapse-web --config {config_path} --port 8080\n"
          "  → http://127.0.0.1:8080 — login: organization "
          f"\"{ORG}\", password \"{ORG_PASSWORD}\" (human account)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
