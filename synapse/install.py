"""Organization creation (local installation procedure).

The first organization is created by this procedure; subsequent ones
can be created by a human account via ``create_org`` (SPEC-WEB §4).
Each organization gets its human account (web access) in the same
transaction. The procedure also allows the local reactivation of a
deactivated organization (``--enable``).

The procedure must be run locally, under the system account of the
service :

    sudo -u synapse synapse-init-org [--config /etc/synapse/config.json]
    sudo -u synapse synapse-init-org --enable acme

The password is prompted on standard input (never as a command
argument, in shell history, in a file or an environment
variable).
"""

from __future__ import annotations

import getpass
import sqlite3
import sys

from . import db
from .config import Config
from .security import hash_password, human_password_sentinel, verify_password
from .store import accounts, organizations
from .validation import (
    human_username_for,
    normalize_organization_name,
    validate_password,
)


def create_organization(
    config: Config,
    organization_name: str,
    password: str,
    confirm: str | None = None,
) -> str:
    """Creates an organization and its human account. Returns the normalized name.

    Args:
        config: service configuration.
        organization_name: proposed organization name.
        password: password (also the human account's, delegated).
        confirm: password confirmation (if provided, must match).
    """
    organization_name = normalize_organization_name(organization_name)
    validate_password(password)
    if confirm is not None and password != confirm:
        raise ValueError("The two password entries differ")
    human_name = human_username_for(organization_name)
    human_hash = human_password_sentinel()  # never verified (delegated to the org)
    with db.connect(config) as conn:
        with db.begin_immediate(conn):
            if organizations.get(conn, organization_name) is not None:
                raise ValueError(f"The organization '{organization_name}' already exists")
            if accounts.get(conn, human_name) is not None:
                raise ValueError(
                    f"The human account name '{human_name}' is already used"
                )
            organizations.insert(conn, organization_name, hash_password(password))
            accounts.insert(
                conn,
                human_name,
                human_hash,
                "active",
                f"Human account of the organization {organization_name} (web access)",
                organization_name,
                can_see_org_agents=True,  # supervisor: directory and search
                principal_type="human",
            )
    return organization_name


def enable_organization(config: Config, organization_name: str, password: str) -> str:
    """Locally reactivates a deactivated organization (SPEC-WEB §4/I15).

    Reactivation is a system administrator decision: it requires
    local access (CLI) and knowledge of the organization's
    password (proof of control). The data is intact.
    """
    organization_name = normalize_organization_name(organization_name)
    with db.connect(config) as conn:
        with db.begin_immediate(conn):
            row = organizations.get(conn, organization_name)
            if row is None:
                raise ValueError(f"The organization '{organization_name}' does not exist")
            if not verify_password(row["password_hash"], password):
                raise ValueError("Incorrect organization password")
            if bool(row["enabled"]):
                raise ValueError(f"The organization '{organization_name}' is not deactivated")
            conn.execute(
                "UPDATE organizations SET enabled = 1 WHERE organization_name = ?",
                (organization_name,),
            )
    return organization_name


def org_init_main() -> None:
    """Console entry point: ``synapse-init-org``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="synapse-init-org", description="Creates an organization of the Synapse service"
    )
    parser.add_argument("--config", default=None, help="JSON configuration file path")
    parser.add_argument(
        "--enable",
        metavar="ORGANIZATION",
        default=None,
        help="Locally reactivates a deactivated organization (local procedure, SPEC-WEB §4)",
    )
    args = parser.parse_args()

    try:
        config = Config.load(args.config)
    except ValueError as exc:
        print(f"synapse-init-org: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.enable is not None:
            organization_name = args.enable
            password = getpass.getpass(
                f"Password of the organization '{organization_name}' : "
            )
            enabled = enable_organization(config, organization_name, password)
        else:
            organization_name = input("Organization name: ")
            password = getpass.getpass("Password (>= 12 printable characters): ")
            confirm = getpass.getpass("Password confirmation: ")
            enabled = create_organization(config, organization_name, password, confirm)
    except (EOFError, KeyboardInterrupt):
        print("\nsynapse-init-org: operation canceled", file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError, sqlite3.Error, db.StorageError) as exc:
        print(f"synapse-init-org: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.enable is not None:
        print(f"Organization '{enabled}' reactivated successfully.")
    else:
        print(f"Organization '{enabled}' created successfully.")
        print("The password is never stored in clear text (Argon2id).")
        print(f"Human account created: {human_username_for(enabled)} (web access, SPEC-WEB).")
