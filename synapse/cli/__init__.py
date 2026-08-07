"""Unified ``synapse`` CLI (SPEC_CLI.md).

Single entry point: server and web administration, management of
organizations/agents/messages/tasks/groups/policies/events, raw
``api`` access, backups, A2A bridge, logs, diagnostics, updates
and global state. Replaces the old flat API client (``synapse <command>``
→ ``synapse api <commande>``, SPEC_CLI §6).
"""

from .main import main

__all__ = ["main"]
