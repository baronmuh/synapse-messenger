"""API v1 client: programmatic access for AI agents.

Le client parle au service via le socket Unix local. Chaque commande
sends the exact JSON envelope of the specification (all parameters
are named fields; optional parameters are always present,
``null`` when unused) and raises ``ApiClientError`` with the stable
``error.code`` on failure.
"""

from __future__ import annotations

import json
import socket

from .validation import API_VERSION
from . import jsonutil

# Maximum request size (specification) and margin for the response.
_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class ApiClientError(Exception):
    """Error returned by the service (``code`` is the API contract)."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class ClientTransportError(Exception):
    """Transport problem (socket unreachable, invalid response...)."""


class Client:
    """Client de l'API v1 sur socket Unix.

    Args:
        socket_path: chemin du socket Unix du service.
    """

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    # ------------------------------------------------------------------
    # Bas niveau
    # ------------------------------------------------------------------
    def request(self, command: str, parameters: dict) -> dict:
        """Sends a command and returns ``data`` (or raises an error)."""
        envelope = {"api_version": API_VERSION, "command": command, "parameters": parameters}
        payload = jsonutil.dumps(envelope)
        if len(payload) > _MAX_REQUEST_BYTES:
            raise ClientTransportError("Request too large (1 MiB max)")
        response = self._transact(payload + b"\n")
        if not response.get("success"):
            error = response.get("error") or {}
            code = error.get("code", "INTERNAL_ERROR")
            message = error.get("message")
            raise ApiClientError(code, message)
        return response.get("data") or {}

    def _transact(self, payload: bytes) -> dict:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.socket_path)
            sock.sendall(payload)
            sock.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise ClientTransportError("Response too large")
            raw = b"".join(chunks)
        except (OSError, ConnectionError) as exc:
            raise ClientTransportError(f"Cannot reach the service: {exc}") from exc
        finally:
            sock.close()
        if not raw:
            raise ClientTransportError("The service closed the connection without a response")
        try:
            response = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ClientTransportError("Invalid response from the service") from exc
        if not isinstance(response, dict):
            raise ClientTransportError("Invalid response from the service")
        return response

    # ------------------------------------------------------------------
    # Commandes d'organisation
    # ------------------------------------------------------------------
    def create_agent(
        self,
        username: str,
        password: str,
        description: str,
        organization_name_auth: str,
        organization_password_auth: str,
        can_see_org_agents: bool = False,
        principal_type: str | None = None,
    ) -> dict:
        return self.request(
            "create_agent",
            {
                "username": username,
                "password": password,
                "description": description,
                "can_see_org_agents": can_see_org_agents,
                "principal_type": principal_type,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def deactivate_agent(
        self, username: str, organization_name_auth: str, organization_password_auth: str
    ) -> dict:
        return self.request(
            "deactivate_agent",
            {
                "username": username,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def reactivate_agent(
        self, username: str, organization_name_auth: str, organization_password_auth: str
    ) -> dict:
        return self.request(
            "reactivate_agent",
            {
                "username": username,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def change_agent_password(
        self,
        username: str,
        new_password: str,
        organization_name_auth: str,
        organization_password_auth: str,
    ) -> dict:
        return self.request(
            "change_agent_password",
            {
                "username": username,
                "new_password": new_password,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def set_agent_visibility(
        self,
        username: str,
        can_see_org_agents: bool,
        organization_name_auth: str,
        organization_password_auth: str,
    ) -> dict:
        return self.request(
            "set_agent_visibility",
            {
                "username": username,
                "can_see_org_agents": can_see_org_agents,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def get_org_agents(
        self,
        organization_name_auth: str,
        organization_password_auth: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "get_org_agents",
            {
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
                "limit": limit,
                "cursor": cursor,
            },
        )

    def set_organization_policy(
        self,
        allow_incoming_external: bool,
        allow_outgoing_external: bool,
        organization_name_auth: str,
        organization_password_auth: str,
    ) -> dict:
        return self.request(
            "set_organization_policy",
            {
                "allow_incoming_external": allow_incoming_external,
                "allow_outgoing_external": allow_outgoing_external,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def get_organization_policy(
        self, organization_name_auth: str, organization_password_auth: str
    ) -> dict:
        return self.request(
            "get_organization_policy",
            {
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def change_organization_password(
        self,
        new_password: str,
        organization_name_auth: str,
        organization_password_auth: str,
    ) -> dict:
        return self.request(
            "change_organization_password",
            {
                "new_password": new_password,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def change_agent_description(
        self,
        username: str,
        description: str,
        organization_name_auth: str,
        organization_password_auth: str,
    ) -> dict:
        return self.request(
            "change_agent_description",
            {
                "username": username,
                "description": description,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    # ------------------------------------------------------------------
    # Commandes des comptes humains (SPEC-WEB) : gestion des organisations
    # and content reading. Authentication: human account (my_*_auth,
    # password = the organization's).
    # ------------------------------------------------------------------
    def create_org(
        self,
        organization_name: str,
        organization_password: str,
        my_name_auth: str,
        my_password_auth: str,
    ) -> dict:
        return self.request(
            "create_org",
            {
                "organization_name": organization_name,
                "organization_password": organization_password,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def disable_org(
        self,
        organization_name: str,
        my_name_auth: str,
        my_password_auth: str,
    ) -> dict:
        return self.request(
            "disable_org",
            {
                "organization_name": organization_name,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def list_orgs(self, my_name_auth: str, my_password_auth: str,
                  include_disabled: bool = False) -> dict:
        """Lists the ACTIVE organizations (selection login screen,
        SPEC-WEB D5 amended). Reserved for the local web identity (the local
        trust token instead of a password) and human accounts.

        ``include_disabled`` (comptes humains uniquement) ajoute le champ
        ``disabled``: deactivated organizations (local administration,
        SPEC_CLI ``synapse org list --all``).
        """
        return self.request(
            "list_orgs",
            {
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
                "include_disabled": include_disabled,
            },
        )

    def list_org_conversations(
        self,
        my_name_auth: str,
        my_password_auth: str,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "list_org_conversations",
            {
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
                "limit": limit,
                "cursor": cursor,
            },
        )

    def get_org_conversation(
        self,
        conversation_id: str,
        my_name_auth: str,
        my_password_auth: str,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "get_org_conversation",
            {
                "conversation_id": conversation_id,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
                "limit": limit,
                "cursor": cursor,
            },
        )

    # ------------------------------------------------------------------
    # Commandes des agents
    # ------------------------------------------------------------------
    def get_my_organization(self, my_name_auth: str, my_password_auth: str) -> dict:
        return self.request(
            "get_my_organization",
            {
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def get_agent_description(self, username: str, my_name_auth: str, my_password_auth: str) -> dict:
        return self.request(
            "get_agent_description",
            {
                "username": username,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def list_org_agents(
        self,
        my_name_auth: str,
        my_password_auth: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "list_org_agents",
            {
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
                "limit": limit,
                "cursor": cursor,
            },
        )

    def help(self, my_name_auth: str, my_password_auth: str, command_name: str | None = None) -> dict:
        """Returns the service's built-in documentation (section 14 of SPEC.txt)."""
        return self.request(
            "help",
            {
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
                "command_name": command_name,
            },
        )

    def send_message(
        self,
        recipient_username: str,
        message: str,
        client_message_id: str,
        my_name_auth: str,
        my_password_auth: str,
        business_reference: str | None = None,
    ) -> dict:
        return self.request(
            "send_message",
            {
                "recipient_username": recipient_username,
                "message": message,
                "client_message_id": client_message_id,
                "business_reference": business_reference,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def get_messages(
        self,
        my_name_auth: str,
        my_password_auth: str,
        status: str | None = None,
        sender_username: str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "get_messages",
            {
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
                "status": status,
                "sender_username": sender_username,
                "conversation_id": conversation_id,
                "limit": limit,
                "cursor": cursor,
            },
        )

    def get_conversation(
        self,
        other_username: str,
        my_name_auth: str,
        my_password_auth: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "get_conversation",
            {
                "other_username": other_username,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
                "limit": limit,
                "cursor": cursor,
            },
        )

    def read_message(self, message_id: str, my_name_auth: str, my_password_auth: str) -> dict:
        return self.request(
            "read_message",
            {
                "message_id": message_id,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def get_notifications(
        self,
        my_name_auth: str,
        my_password_auth: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "get_notifications",
            {
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
                "limit": limit,
                "cursor": cursor,
            },
        )

    def mark_conversation_no_reply(
        self, conversation_id: str, my_name_auth: str, my_password_auth: str
    ) -> dict:
        return self.request(
            "mark_conversation_no_reply",
            {
                "conversation_id": conversation_id,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def set_agent_card(
        self,
        capabilities: list[str],
        my_name_auth: str,
        my_password_auth: str,
        domain: str | None = None,
        model: str | None = None,
        tools: list[str] | None = None,
        sla: str | None = None,
        limits: str | None = None,
        estimated_cost: str | None = None,
    ) -> dict:
        return self.request(
            "set_agent_card",
            {
                "capabilities": capabilities,
                "domain": domain,
                "model": model,
                "tools": tools if tools is not None else [],
                "sla": sla,
                "limits": limits,
                "estimated_cost": estimated_cost,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def get_agent_card(self, username: str, my_name_auth: str, my_password_auth: str) -> dict:
        return self.request(
            "get_agent_card",
            {
                "username": username,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def approve_agent_card(
        self, username: str, organization_name_auth: str, organization_password_auth: str
    ) -> dict:
        return self.request(
            "approve_agent_card",
            {
                "username": username,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def find_agents(
        self,
        my_name_auth: str,
        my_password_auth: str,
        capability: str | None = None,
        domain: str | None = None,
        name_contains: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "find_agents",
            {
                "capability": capability,
                "domain": domain,
                "name_contains": name_contains,
                "limit": limit,
                "cursor": cursor,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    # -- Tasks and coordination (SPEC.txt F5-F10) ---------------------------
    def create_task(
        self,
        title: str,
        assignee_username: str,
        my_name_auth: str,
        my_password_auth: str,
        description: str | None = None,
        priority: str | None = None,
        due_at: str | None = None,
        depends_on: list[str] | None = None,
        business_reference: str | None = None,
        client_task_id: str | None = None,
    ) -> dict:
        return self.request(
            "create_task",
            {
                "title": title,
                "description": description,
                "assignee_username": assignee_username,
                "priority": priority,
                "due_at": due_at,
                "depends_on": depends_on if depends_on is not None else [],
                "business_reference": business_reference,
                "client_task_id": client_task_id,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def get_task(self, task_id: str, my_name_auth: str, my_password_auth: str) -> dict:
        return self.request(
            "get_task",
            {
                "task_id": task_id,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def list_tasks(
        self,
        my_name_auth: str,
        my_password_auth: str,
        assignee_username: str | None = None,
        state: str | None = None,
        priority: str | None = None,
        due_before: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "list_tasks",
            {
                "assignee_username": assignee_username,
                "state": state,
                "priority": priority,
                "due_before": due_before,
                "limit": limit,
                "cursor": cursor,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def update_task_state(
        self,
        task_id: str,
        new_state: str,
        my_name_auth: str,
        my_password_auth: str,
        result: str | None = None,
    ) -> dict:
        return self.request(
            "update_task_state",
            {
                "task_id": task_id,
                "new_state": new_state,
                "result": result,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def transfer_task(
        self,
        task_id: str,
        assignee_username: str,
        my_name_auth: str,
        my_password_auth: str,
        note: str | None = None,
    ) -> dict:
        return self.request(
            "transfer_task",
            {
                "task_id": task_id,
                "assignee_username": assignee_username,
                "note": note,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def request_approval(
        self,
        task_id: str,
        approver_username: str,
        my_name_auth: str,
        my_password_auth: str,
    ) -> dict:
        return self.request(
            "request_approval",
            {
                "task_id": task_id,
                "approver_username": approver_username,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def approve_task(self, task_id: str, my_name_auth: str, my_password_auth: str) -> dict:
        return self.request(
            "approve_task",
            {
                "task_id": task_id,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def reject_task(
        self,
        task_id: str,
        my_name_auth: str,
        my_password_auth: str,
        reason: str | None = None,
    ) -> dict:
        return self.request(
            "reject_task",
            {
                "task_id": task_id,
                "reason": reason,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def get_my_work(
        self,
        my_name_auth: str,
        my_password_auth: str,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "get_my_work",
            {
                "limit": limit,
                "cursor": cursor,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def get_events(
        self,
        my_name_auth: str,
        my_password_auth: str,
        types: list[str] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "get_events",
            {
                "types": types,
                "limit": limit,
                "cursor": cursor,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def set_escalation_policy(
        self,
        enabled: bool,
        due_after_seconds: int,
        failed_after_seconds: int,
        escalate_to_username: str,
        organization_name_auth: str,
        organization_password_auth: str,
    ) -> dict:
        return self.request(
            "set_escalation_policy",
            {
                "enabled": enabled,
                "due_after_seconds": due_after_seconds,
                "failed_after_seconds": failed_after_seconds,
                "escalate_to_username": escalate_to_username,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def get_escalation_policy(
        self, organization_name_auth: str, organization_password_auth: str
    ) -> dict:
        """Current escalation policy of the organization (read)."""
        return self.request(
            "get_escalation_policy",
            {
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def set_agent_budget(
        self,
        username: str,
        organization_name_auth: str,
        organization_password_auth: str,
        max_active_tasks: int | None = None,
        max_messages_per_hour: int | None = None,
    ) -> dict:
        return self.request(
            "set_agent_budget",
            {
                "username": username,
                "max_active_tasks": max_active_tasks,
                "max_messages_per_hour": max_messages_per_hour,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def set_event_retention_days(
        self,
        days: int,
        organization_name_auth: str,
        organization_password_auth: str,
    ) -> dict:
        """Retention of the organization's consultable events (F10)."""
        return self.request(
            "set_event_retention_days",
            {
                "days": days,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    # -- Organizational structure and observability (F11-F14) ----------
    def create_department(
        self, department_name: str, organization_name_auth: str, organization_password_auth: str
    ) -> dict:
        return self.request(
            "create_department",
            {
                "department_name": department_name,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def set_agent_department(
        self,
        username: str,
        department_name: str,
        role: str,
        organization_name_auth: str,
        organization_password_auth: str,
    ) -> dict:
        return self.request(
            "set_agent_department",
            {
                "username": username,
                "department_name": department_name,
                "role": role,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def get_org_structure(
        self, organization_name_auth: str, organization_password_auth: str
    ) -> dict:
        return self.request(
            "get_org_structure",
            {
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def list_department_tasks(
        self,
        department_name: str,
        my_name_auth: str,
        my_password_auth: str,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "list_department_tasks",
            {
                "department_name": department_name,
                "limit": limit,
                "cursor": cursor,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def get_org_audit(
        self,
        organization_name_auth: str,
        organization_password_auth: str,
        since: str | None = None,
        actor_username: str | None = None,
        command: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "get_org_audit",
            {
                "since": since,
                "actor_username": actor_username,
                "command": command,
                "limit": limit,
                "cursor": cursor,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def get_org_metrics(
        self, organization_name_auth: str, organization_password_auth: str
    ) -> dict:
        return self.request(
            "get_org_metrics",
            {
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def get_server_status(
        self, organization_name_auth: str, organization_password_auth: str
    ) -> dict:
        return self.request(
            "get_server_status",
            {
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    # -- Groups, reputation, delegation (F15-F17) ------------------------
    def create_group(self, name: str, my_name_auth: str, my_password_auth: str) -> dict:
        return self.request(
            "create_group",
            {
                "name": name,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def add_group_member(
        self, group_id: str, username: str, my_name_auth: str, my_password_auth: str
    ) -> dict:
        return self.request(
            "add_group_member",
            {
                "group_id": group_id,
                "username": username,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def remove_group_member(
        self, group_id: str, username: str, my_name_auth: str, my_password_auth: str
    ) -> dict:
        return self.request(
            "remove_group_member",
            {
                "group_id": group_id,
                "username": username,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def send_group_message(
        self,
        group_id: str,
        message: str,
        my_name_auth: str,
        my_password_auth: str,
        client_message_id: str | None = None,
    ) -> dict:
        return self.request(
            "send_group_message",
            {
                "group_id": group_id,
                "message": message,
                "client_message_id": client_message_id,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def get_group_messages(
        self,
        group_id: str,
        my_name_auth: str,
        my_password_auth: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "get_group_messages",
            {
                "group_id": group_id,
                "limit": limit,
                "cursor": cursor,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def get_group_members(
        self,
        group_id: str,
        my_name_auth: str,
        my_password_auth: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "get_group_members",
            {
                "group_id": group_id,
                "limit": limit,
                "cursor": cursor,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def list_my_groups(
        self,
        my_name_auth: str,
        my_password_auth: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "list_my_groups",
            {
                "limit": limit,
                "cursor": cursor,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def get_agent_reputation(
        self, username: str, my_name_auth: str, my_password_auth: str
    ) -> dict:
        return self.request(
            "get_agent_reputation",
            {
                "username": username,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def create_delegation(
        self,
        task_id: str,
        delegatee_username: str,
        expires_at: str,
        my_name_auth: str,
        my_password_auth: str,
    ) -> dict:
        return self.request(
            "create_delegation",
            {
                "task_id": task_id,
                "delegatee_username": delegatee_username,
                "expires_at": expires_at,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def revoke_delegation(
        self,
        task_id: str,
        delegatee_username: str,
        my_name_auth: str,
        my_password_auth: str,
    ) -> dict:
        return self.request(
            "revoke_delegation",
            {
                "task_id": task_id,
                "delegatee_username": delegatee_username,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    def get_my_delegations(
        self,
        my_name_auth: str,
        my_password_auth: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict:
        return self.request(
            "get_my_delegations",
            {
                "limit": limit,
                "cursor": cursor,
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )

    # -- Observateurs et passerelle (F18/F20) -----------------------------
    def create_observer_account(
        self,
        observer_name: str,
        password: str,
        description: str,
        organization_name_auth: str,
        organization_password_auth: str,
    ) -> dict:
        return self.request(
            "create_observer_account",
            {
                "observer_name": observer_name,
                "password": password,
                "description": description,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def revoke_observer_account(
        self, observer_name: str, organization_name_auth: str, organization_password_auth: str
    ) -> dict:
        return self.request(
            "revoke_observer_account",
            {
                "observer_name": observer_name,
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def list_observers(
        self, organization_name_auth: str, organization_password_auth: str
    ) -> dict:
        return self.request(
            "list_observers",
            {
                "organization_name_auth": organization_name_auth,
                "organization_password_auth": organization_password_auth,
            },
        )

    def get_org_snapshot(self, my_name_auth: str, my_password_auth: str) -> dict:
        return self.request(
            "get_org_snapshot",
            {
                "my_name_auth": my_name_auth,
                "my_password_auth": my_password_auth,
            },
        )
