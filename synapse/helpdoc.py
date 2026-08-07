"""Built-in service documentation — the ``help`` command (section 14 of SPEC.txt).

The documentation is built from ``COMMAND_SPECS``
(``synapse.validation``), the single source of truth for commands and their
parameters: command names, signatures, required/optional status,
types and defaults are **generated**, never hand-copied.
Only the semantic prose (role, access, response, errors, call example)
is written here, once per command. Any API v2 evolution must
add the corresponding prose; the rest follows automatically.

The returned text is designed to be consumed by an AI agent: structured,
unambiguous, without secrets or account data (the examples use
fictional identifiers), and capped at 64 KiB in full mode.
"""

from __future__ import annotations

import json
from functools import lru_cache

from .validation import API_VERSION, COMMAND_SPECS

MAX_DOCUMENTATION_BYTES = 64 * 1024  # specification: <= 64 KiB in full mode

# ---------------------------------------------------------------------------
# Expected formats by parameter name (documentation display only).
# Strict validation stays in validation.py. A test guarantees that every
# parameter declared in COMMAND_SPECS has an entry here: the
# documentation cannot silently forget a new parameter.
# ---------------------------------------------------------------------------
_PARAM_FORMATS: dict[str, str] = {
    "my_name_auth": "caller name (3-64 [a-z0-9_-])",
    "my_password_auth": "caller password (>= 12 printable characters)",
    "organization_name_auth": "calling org name (3-64 [a-z0-9_-])",
    "organization_password_auth": "org password (>= 12 printable characters)",
    "include_disabled": "boolean: include deactivated organizations "
                        "(human accounts only — SPEC_CLI org list --all)",
    "organization_name": "name of the new organization (3-64 [a-z0-9_-])",
    "organization_password": "password of the new organization (>= 12 characters)",
    "username": "username (3-64 [a-z0-9_-])",
    "password": "account password (>= 12 characters, never normalized)",
    "description": "description (account: 1-500; task: 1-5,000, NFC)",
    "can_see_org_agents": "boolean: list the org's usernames (default false)",
    "new_password": "new password (>= 12 characters)",
    "allow_incoming_external": "boolean: allow incoming external messages",
    "allow_outgoing_external": "boolean: allow outgoing external messages",
    "recipient_username": "recipient name (active account, != sender)",
    "message": "content (1-10,000 NFC code points, no control characters)",
    "client_message_id": "idempotency id (1-128 [A-Za-z0-9._:-])",
    "business_reference": "business reference (1-128 NFC, included in idempotency)",
    "status": "'read' or 'unread' (null = all)",
    "sender_username": "sender name (filter, null = all)",
    "conversation_id": "conversation UUIDv4 (filter, null = all)",
    "other_username": "other agent name (!= caller)",
    "limit": "integer 1-100",
    "days": "integer 1-3650 (event retention days)",
    "capabilities": "list of 1-50 capabilities (NFC 1-64, deduplicated)",
    "domain": "domain (NFC 1-128, null = none)",
    "model": "model (NFC 1-128, null = none)",
    "tools": "list of 0-50 tools (NFC 1-64, deduplicated)",
    "sla": "announced SLA (NFC 1-128, null = none)",
    "limits": "announced limits (NFC 1-256, null = none)",
    "estimated_cost": "estimated cost (NFC 1-128, null = none)",
    "capability": "capability filter (substring, case-insensitive)",
    "name_contains": "name substring filter",
    "title": "task title (1-200 NFC code points)",
    "task_id": "UUIDv4 of a task",
    "state": "task state (filter, null = all): submitted, in_progress, completed, "
             "failed, canceled, pending_approval",
    "due_before": "maximum due date (filter, null = all)",
    "assignee_username": "assignee name (active account)",
    "priority": "'low', 'normal' or 'high' (default 'normal')",
    "due_at": "UTC due date YYYY-MM-DDTHH:mm:ss.sssZ (null = none)",
    "depends_on": "list of 0-20 existing task identifiers",
    "client_task_id": "task idempotency id (1-128 [A-Za-z0-9._:-], optional)",
    "new_state": "new state: submitted, in_progress, completed, failed, canceled, "
                 "pending_approval (constrained transitions)",
    "result": "result (1-10,000 NFC, required for completed/failed)",
    "note": "transfer note (1-500 NFC, null = none)",
    "approver_username": "approver name (active account)",
    "reason": "rejection reason (1-500 NFC, null = none)",
    "types": "list of 1-20 event types (null = all)",
    "enabled": "boolean: enable automatic escalation",
    "due_after_seconds": "delay before escalating a late task (>= 1)",
    "failed_after_seconds": "delay before escalating a failed task (>= 1)",
    "escalate_to_username": "designated agent receiving escalations (org member)",
    "max_active_tasks": "maximum active tasks (integer >= 1, null = no limit)",
    "max_messages_per_hour": "maximum messages per hour (integer >= 1, null = no limit)",
    "department_name": "department name (3-64 [a-z0-9_-])",
    "role": "fixed role: manager, employee or rh",
    "actor_username": "audit actor filter (null = all)",
    "command": "audit command filter (null = all)",
    "since": "UTC date (filter: audit from this date, null = all)",
    "name": "group name (1-64 NFC code points)",
    "group_id": "UUIDv4 of a group",
    "delegatee_username": "delegatee name (active account)",
    "expires_at": "delegation expiration UTC date (in the future)",
    "principal_type": "principal type: 'agent' (default) or 'human'",
    "observer_name": "observer account name (3-64 [a-z0-9_-])",
    "cursor": "opaque cursor (null = first page)",
    "message_id": "UUIDv4 of the message",
    "command_name": "exact command name (null = full documentation)",
}

# ---------------------------------------------------------------------------
# Meaning of the error codes (documentation display). The full set of
# codes is checked by a test against synapse.errors: no forgotten code,
# no phantom code.
# ---------------------------------------------------------------------------
_ERROR_MEANINGS: dict[str, str] = {
    "AUTH_FAILED": "invalid credentials, deactivated account, or too many failed attempts",
    "ACCESS_DENIED": "the command requires a permission the caller does not have "
                     "(organization command called by an agent, or visibility"
                     "permission denied)",
    "INVALID_ARGUMENT": "unknown, missing, wrongly-typed field or invalid value",
    "UNKNOWN_COMMAND": "unknown command in the envelope, or unknown command requested from help",
    "USERNAME_ALREADY_EXISTS": "this username is already used",
    "USER_NOT_FOUND": "account not found or outside the organization (management commands)",
    "RECIPIENT_NOT_FOUND": "nonexistent or deactivated recipient",
    "MESSAGE_NOT_FOUND": "message not found or inaccessible",
    "CONVERSATION_NOT_FOUND": "conversation not found or inaccessible",
    "MESSAGE_ALREADY_EXISTS": "client_message_id already used with different "
                              "characteristics",
    "POLICY_DENIED": "external communication denied by the organization policy "
                     "(outgoing of the sender or incoming of the recipient)",
    "TASK_NOT_FOUND": "task not found or inaccessible (or approval whose caller "
                      "is not the designated approver)",
    "TASK_STATE_INVALID": "forbidden task state transition, or action on a task "
                          "completed / not pending approval",
    "TASK_DEPENDENCY_NOT_MET": "the task dependencies are not all completed",
    "QUOTA_EXCEEDED": "budget exceeded (active tasks or messages sent per hour)",
    "GROUP_NOT_FOUND": "group not found or the caller is not a member",
    "INTERNAL_ERROR": "internal service error",
}


class CommandDoc:
    """Documentary prose of a command: role, access, response, errors, example."""

    __slots__ = ("role", "access", "response", "errors", "example")

    def __init__(
        self,
        *,
        role: str,
        access: str,
        response: str,
        errors: str,
        example: dict,
    ) -> None:
        self.role = role
        self.access = access
        self.response = response
        self.errors = errors
        self.example = example


_ACCESS_ORG = (
    "an organization authentication is required "
    "(organization_name_auth + organization_password_auth)"
)
_ACCESS_AGENT = "any active agent account (my_name_auth + my_password_auth)"

COMMAND_DOCS: dict[str, CommandDoc] = {
    "create_agent": CommandDoc(
        role="Creates an agent account in the authenticated organization (the organization is "
             "never a parameter: it is always the authenticating one). The account is "
             "created with the 'active' state, its mandatory public description and its "
             "can_see_org_agents visibility permission (default false).",
        access=_ACCESS_ORG,
        response="data = {username, status: 'active', description, organization_name, "
                 "can_see_org_agents}",
        errors="USERNAME_ALREADY_EXISTS if the name is taken; AUTH_FAILED; INVALID_ARGUMENT "
               "(formats, description).",
        example={
            "username": "nouvel_agent",
            "password": "demo-mdp-robuste-123",
            "description": "Demo agent",
            "can_see_org_agents": False,
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "deactivate_agent": CommandDoc(
        role="Deactivates an agent of the authenticated organization ('disabled' state): it can "
             "no longer authenticate, send or read. Its data is kept. "
             "Idempotent.",
        access=_ACCESS_ORG,
        response="data = {username, status: 'disabled'}",
        errors="USER_NOT_FOUND if the account does not exist or belongs to another "
               "organization; AUTH_FAILED; INVALID_ARGUMENT (formats).",
        example={
            "username": "agent_a",
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "reactivate_agent": CommandDoc(
        role="Reactivates an agent of the authenticated organization ('active' state). Idempotent.",
        access=_ACCESS_ORG,
        response="data = {username, status: 'active'}",
        errors="USER_NOT_FOUND if the account does not exist or belongs to another "
               "organization; AUTH_FAILED; INVALID_ARGUMENT (formats).",
        example={
            "username": "agent_a",
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "change_agent_password": CommandDoc(
        role="Changes the password of an agent of the authenticated organization. The new "
             "password follows the same rules as the other passwords.",
        access=_ACCESS_ORG,
        response="data = {username, status: 'active|disabled'}",
        errors="USER_NOT_FOUND if the account does not exist or belongs to another "
               "organization; AUTH_FAILED; INVALID_ARGUMENT (formats).",
        example={
            "username": "agent_a",
            "new_password": "nouveau-demo-mdp-123",
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "set_agent_visibility": CommandDoc(
        role="Sets the can_see_org_agents visibility permission of an agent of the "
             "authenticated organization: controls whether the agent can list the usernames "
             "of the active agents of its own organization via list_org_agents.",
        access=_ACCESS_ORG,
        response="data = {username, can_see_org_agents}",
        errors="USER_NOT_FOUND if the account does not exist or belongs to another "
               "organization; AUTH_FAILED; INVALID_ARGUMENT (formats).",
        example={
            "username": "agent_a",
            "can_see_org_agents": True,
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "get_org_agents": CommandDoc(
        role="Paginated list of the authenticated organization's agents (username, "
             "description, status, can_see_org_agents, principal_type and detailed "
             "reputation — SPEC.txt F16), sorted by ascending username. An "
             "organization always has the right to list its own agents.",
        access=_ACCESS_ORG,
        response="data = {agents: [{username, description, status, can_see_org_agents, "
                 "principal_type, reputation}], next_cursor}",
        errors="AUTH_FAILED ; INVALID_ARGUMENT (curseur, limit).",
        example={
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
            "limit": 50,
            "cursor": None,
        },
    ),
    "set_organization_policy": CommandDoc(
        role="Sets the external communication policy of the organization: "
             "allow_incoming_external (can external agents send "
             "messages to the organization's agents?) and allow_outgoing_external (can the "
             "organization's agents send messages to the outside "
             "?). Internal communication is always allowed. Policies are "
             "evaluated at send time, for new messages only.",
        access=_ACCESS_ORG,
        response="data = {organization_name, allow_incoming_external, allow_outgoing_external}",
        errors="AUTH_FAILED ; INVALID_ARGUMENT (formats).",
        example={
            "allow_incoming_external": True,
            "allow_outgoing_external": False,
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "get_organization_policy": CommandDoc(
        role="Returns the current external communication policy of the "
             "authenticated organization.",
        access=_ACCESS_ORG,
        response="data = {organization_name, allow_incoming_external, allow_outgoing_external}",
        errors="AUTH_FAILED.",
        example={
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "change_organization_password": CommandDoc(
        role="Changes the password of the authenticated organization (authenticated by "
             "the old password).",
        access=_ACCESS_ORG,
        response="data = {organization_name}",
        errors="AUTH_FAILED ; INVALID_ARGUMENT (formats).",
        example={
            "new_password": "nouveau-demo-mdp-org-123",
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "change_agent_description": CommandDoc(
        role="Modifies the public description of an organization agent (SPEC-WEB §4). "
             "The organization's human account is not modifiable.",
        access=_ACCESS_ORG,
        response="data = {username, description}",
        errors="USER_NOT_FOUND (agent outside the organization); ACCESS_DENIED (human account); "
               "INVALID_ARGUMENT (description).",
        example={
            "username": "agent_a",
            "description": "New agent description",
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "create_org": CommandDoc(
        role="Creates an organization from a human account (SPEC-WEB §4/§7.1): name, "
             "password, auto-created human account in the same transaction. The first "
             "organization is still created locally (synapse-init-org); subsequent ones "
             "can be created by a human. Reserved for human accounts.",
        access="a human account authentication is required (my_name_auth + my_password_auth)",
        response="data = {organization_name, human_username}",
        errors="ACCESS_DENIED (non-human account); INVALID_ARGUMENT (name already used, "
               "invalid password); AUTH_FAILED.",
        example={
            "organization_name": "org_nouvelle",
            "organization_password": "demo-mdp-org-nouvelle-1",
            "my_name_auth": "org_demo_humain",
            "my_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "disable_org": CommandDoc(
        role="Deactivates the caller's organization (SPEC-WEB §4/§7.2): any "
             "authentication (accounts and organization) fails, sends refused, data "
             "intact. Reversible only by the local procedure "
             "(synapse-init-org --enable). Reserved for human accounts.",
        access="a human account authentication is required (my_name_auth + my_password_auth)",
        response="data = {organization_name, enabled: false}",
        errors="ACCESS_DENIED (non-human account, or another organization); "
               "INVALID_ARGUMENT (already deactivated); AUTH_FAILED.",
        example={
            "organization_name": "org_demo",
            "my_name_auth": "org_demo_humain",
            "my_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "list_orgs": CommandDoc(
        role="Lists the ACTIVE organizations (selection login screen, "
             "SPEC-WEB D5 amended): joinable organization names, sorted. Reserved "
             "for the local web identity (run dir trust token) and human "
             "accounts — never for agents.",
        access="a human account OR the local web identity authentication "
               "(my_name_auth + my_password_auth)",
        response="data = {organizations: [{organization_name}]}",
        errors="ACCESS_DENIED (agent account); AUTH_FAILED.",
        example={
            "my_name_auth": "org_demo_humain",
            "my_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "list_org_conversations": CommandDoc(
        role="Paginated list of the organization's conversations (SPEC-WEB §2/§7.3): "
             "participants, volume, last exchange, unread for the caller. Metadata "
             "only — content is served by get_org_conversation. Reserved for "
             "human accounts.",
        access="a human account authentication is required (my_name_auth + my_password_auth)",
        response="data = {conversations: [{conversation_id, participants, message_count, "
                 "unread_count, last_message_at}], next_cursor}",
        errors="ACCESS_DENIED (non-human account); AUTH_FAILED.",
        example={
            "my_name_auth": "org_demo_humain",
            "my_password_auth": "demo-mdp-org-demo-1",
            "limit": 50,
            "cursor": None,
        },
    ),
    "get_org_conversation": CommandDoc(
        role="Reads an organization conversation, content included (SPEC-WEB "
             "§2/§7.4): complete paginated messages, chronological order. Authorization: "
             "at least one participant belongs to the organization (otherwise "
             "CONVERSATION_NOT_FOUND). Content reads are traced (audit). Reserved "
             "for human accounts.",
        access="a human account authentication is required (my_name_auth + my_password_auth)",
        response="data = {conversation_id, messages: [{message_id, sender_username, "
                 "recipient_username, created_at, content, read_at}], next_cursor}",
        errors="ACCESS_DENIED (non-human account); CONVERSATION_NOT_FOUND (unknown or "
               "outside the organization); AUTH_FAILED.",
        example={
            "conversation_id": "uuid-v4-of-the-conversation",
            "my_name_auth": "org_demo_humain",
            "my_password_auth": "demo-mdp-org-demo-1",
            "limit": 100,
            "cursor": None,
        },
    ),
    "get_my_organization": CommandDoc(
        role="Returns the name of the authenticated agent's organization and its "
             "external communication policies.",
        access=_ACCESS_AGENT,
        response="data = {organization_name, allow_incoming_external, allow_outgoing_external}",
        errors="AUTH_FAILED.",
        example={
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "get_agent_description": CommandDoc(
        role="Returns the public description of an account from its "
             "username, as well as its organization. Public directory metadata: "
             "consultable by any active account, including for a deactivated account; it "
             "reveals neither password, nor hash, nor state, nor content.",
        access=_ACCESS_AGENT,
        response="data = {username, organization_name, description}",
        errors="USER_NOT_FOUND if the account does not exist; AUTH_FAILED; INVALID_ARGUMENT "
               "(formats).",
        example={
            "username": "agent_b",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "list_org_agents": CommandDoc(
        role="Paginated list of the usernames of the active agents of the authenticated "
             "agent's own organization, sorted by ascending username. Reserved for agents whose "
             "can_see_org_agents is true. Never reveals the usernames of another "
             "organization.",
        access=_ACCESS_AGENT,
        response="data = {usernames: [...], next_cursor}",
        errors="ACCESS_DENIED if can_see_org_agents is false; AUTH_FAILED; "
               "INVALID_ARGUMENT (curseur, limit).",
        example={
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
            "limit": 50,
            "cursor": None,
        },
    ),
    "send_message": CommandDoc(
        role="Sends a message to an active recipient, different from the sender. "
             "Reusing the same client_message_id with the same recipient and the same "
             "normalized content returns the already-created message. Internal "
             "organization communication is always allowed; an external send is subject to the "
             "policies of both organizations (outgoing of the sender, incoming of the "
             "recipient) and may cause POLICY_DENIED.",
        access=_ACCESS_AGENT,
        response="data = full message object (with sender_organization_name)",
        errors="RECIPIENT_NOT_FOUND (nonexistent or deactivated recipient) ; "
               "POLICY_DENIED (external communication denied); MESSAGE_ALREADY_EXISTS; "
               "INVALID_ARGUMENT (self-send, formats); AUTH_FAILED.",
        example={
            "recipient_username": "agent_b",
            "message": "Hello agent B",
            "client_message_id": "msg-001",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "get_messages": CommandDoc(
        role="Returns the messages received by the authenticated agent, sorted from newest to "
             "oldest, with optional filters (status, sender_username, "
             "conversation_id) and pagination. Modifies no status.",
        access=_ACCESS_AGENT,
        response="data = {messages: [message object], next_cursor}",
        errors="AUTH_FAILED; INVALID_ARGUMENT (filters, cursor, limit).",
        example={
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
            "status": None,
            "sender_username": None,
            "conversation_id": None,
            "limit": 50,
            "cursor": None,
        },
    ),
    "get_conversation": CommandDoc(
        role="Returns the full exchange between the authenticated agent and other_username, in "
             "ascending chronological order, with pagination. Consulting marks "
             "nothing as read. Replying to a message means sending a new message to the "
             "same recipient: it appears in the same conversation. No exchange -> "
             "CONVERSATION_NOT_FOUND.",
        access=_ACCESS_AGENT,
        response="data = {conversation_id, other_username, reply_status, messages, "
                 "next_cursor}",
        errors="CONVERSATION_NOT_FOUND if no exchange exists; INVALID_ARGUMENT "
               "(other_username identical to the caller, cursor, limit); AUTH_FAILED.",
        example={
            "other_username": "agent_b",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
            "limit": 50,
            "cursor": None,
        },
    ),
    "read_message": CommandDoc(
        role="Returns a message and marks it read if it was received by the caller: "
             "read_at = first-read date (written only if null), then the reply state is "
             "recomputed. A nonexistent or inaccessible message -> MESSAGE_NOT_FOUND.",
        access=_ACCESS_AGENT,
        response="data = full message object (with sender_organization_name)",
        errors="MESSAGE_NOT_FOUND (nonexistent or inaccessible message); INVALID_ARGUMENT; "
               "AUTH_FAILED.",
        example={
            "message_id": "11111111-1111-4111-8111-111111111111",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "get_notifications": CommandDoc(
        role="Returns, for the authenticated agent: unread_by_sender (number of "
             "unread received messages per sender) and the needs_reply conversations list "
             "(conversation_id, other agent and its organization, number of received "
             "unread messages, date of the last received message), sorted by descending date of the "
             "last received message. Modifies no status.",
        access=_ACCESS_AGENT,
        response="data = {unread_by_sender: {sender: count}, needs_reply: [...], "
                 "next_cursor}",
        errors="INVALID_ARGUMENT (invalid limit or cursor); AUTH_FAILED.",
        example={
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
            "limit": 50,
            "cursor": None,
        },
    ),
    "mark_conversation_no_reply": CommandDoc(
        role="Marks the conversation as not requiring a reply (state "
             "no_reply_needed for the caller, tied to the last received message). A new "
             "received message cancels the marking. Idempotent for the same last message; "
             "modifies no read status nor the other agent's state.",
        access=_ACCESS_AGENT,
        response="data = {conversation_id, reply_status: 'no_reply_needed', "
                 "no_reply_for_message_id}",
        errors="INVALID_ARGUMENT if the conversation does not exist or contains no "
               "received message; AUTH_FAILED.",
        example={
            "conversation_id": "11111111-1111-4111-8111-111111111111",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "set_agent_card": CommandDoc(
        role="Declares or replaces the caller's agent card (SPEC.txt F2): "
             "mandatory capabilities, other optional fields. Any submission returns "
             "to 'pending'; the organization must validate (approve_agent_card). No "
             "secret data in the card.",
        access=_ACCESS_AGENT,
        response="data = full agent card of the caller (validation_state: 'pending')",
        errors="INVALID_ARGUMENT (missing capabilities, formats, bounds); AUTH_FAILED.",
        example={
            "capabilities": ["comptabilite", "reporting"],
            "domain": "finance",
            "model": "demo-model-2",
            "tools": ["tableur", "api-facturation"],
            "sla": "reply within 1 hour",
            "limits": "10 operations per day",
            "estimated_cost": "0.01 EUR per operation",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "get_agent_card": CommandDoc(
        role="Returns the agent card of an account (SPEC.txt F2). Public directory "
             "metadata like the description: consultable by any active account, without "
             "revealing password or state. An account without a card returns an empty "
             "card (validation_state: null).",
        access=_ACCESS_AGENT,
        response="data = full agent card (capabilities, domain, model, tools, sla, "
                 "limits, estimated_cost, validation_state, approved_by, approved_at, "
                 "updated_at)",
        errors="USER_NOT_FOUND if the account does not exist; AUTH_FAILED; INVALID_ARGUMENT "
               "(formats).",
        example={
            "username": "agent_b",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "approve_agent_card": CommandDoc(
        role="Validates the card of an organization agent (SPEC.txt F2). Any "
             "later modification returns to 'pending' and requires a new "
             "validation. Idempotent.",
        access=_ACCESS_ORG,
        response="data = {username, validation_state: 'approved'}",
        errors="USER_NOT_FOUND (agent outside the organization or without a card); AUTH_FAILED; "
               "INVALID_ARGUMENT (formats).",
        example={
            "username": "agent_a",
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "find_agents": CommandDoc(
        role="Paginated search of agents by capability in one's own organization "
             "(SPEC.txt F3): filters by capability, domain and name substring. "
             "Reserved for agents with can_see_org_agents; never usernames of another "
             "organization; active agents with a card only.",
        access=_ACCESS_AGENT,
        response="data = {agents: [agent card...], next_cursor}",
        errors="ACCESS_DENIED if can_see_org_agents is false; AUTH_FAILED; "
               "INVALID_ARGUMENT (filtres, curseur, limit).",
        example={
            "capability": "comptabilite",
            "domain": None,
            "name_contains": None,
            "limit": 50,
            "cursor": None,
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "create_task": CommandDoc(
        role="Creates a coordination task (SPEC.txt F5): mandatory title, active assignee, "
             "priority, due date, dependencies, business reference and optional idempotency "
             "identifier. Moving to in_progress requires the dependencies completed. Emits "
             "task.created.",
        access=_ACCESS_AGENT,
        response="data = full task (task_id, state 'submitted', depends_on, history, ...)",
        errors="USER_NOT_FOUND / RECIPIENT_NOT_FOUND (assignee); INVALID_ARGUMENT (formats, "
               "nonexistent dependency, client_task_id already used for another task); "
               "QUOTA_EXCEEDED (active tasks budget); AUTH_FAILED.",
        example={
            "title": "Analyze customer issue 4711",
            "description": "Billing incident reported by the sales agent",
            "assignee_username": "support",
            "priority": "high",
            "due_at": "2026-08-10T12:00:00.000Z",
            "depends_on": [],
            "business_reference": "incident-4711",
            "client_task_id": "op-2026-0812-01",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "get_task": CommandDoc(
        role="Returns a task visible by the caller (creator or assignee), with its "
             "full history and dependencies (SPEC.txt F5).",
        access=_ACCESS_AGENT,
        response="data = full task",
        errors="TASK_NOT_FOUND (nonexistent or inaccessible); AUTH_FAILED; "
               "INVALID_ARGUMENT (format).",
        example={
            "task_id": "11111111-1111-4111-8111-111111111111",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "list_tasks": CommandDoc(
        role="Paginated list of the tasks visible by the caller (created by them or "
             "assigned to them), sorted by ascending created_at. Combinable filters: "
             "assignee, state, priority, due before a date.",
        access=_ACCESS_AGENT,
        response="data = {tasks: [...], next_cursor}",
        errors="AUTH_FAILED; INVALID_ARGUMENT (filters, cursor, limit).",
        example={
            "assignee_username": None,
            "state": "in_progress",
            "priority": None,
            "due_before": None,
            "limit": 50,
            "cursor": None,
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "update_task_state": CommandDoc(
        role="Evolves the state of a visible task (SPEC.txt F5): submitted -> "
             "in_progress | canceled; in_progress -> completed | failed | canceled. "
             "completed/failed require a result and are terminal. Emits "
             "task.state_changed.",
        access=_ACCESS_AGENT,
        response="data = full task in its new state",
        errors="TASK_NOT_FOUND; TASK_STATE_INVALID (forbidden transition); "
               "TASK_DEPENDENCY_NOT_MET; INVALID_ARGUMENT (formats); AUTH_FAILED.",
        example={
            "task_id": "11111111-1111-4111-8111-111111111111",
            "new_state": "completed",
            "result": "Analysis complete: incident resolved",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "transfer_task": CommandDoc(
        role="Transfers an unfinished task to an active agent (SPEC.txt F7), with an optional "
             "note. The creator or the current assignee can transfer; the chain "
             "of responsibility is traced. Emits task.transferred.",
        access=_ACCESS_AGENT,
        response="data = full task (new assignee)",
        errors="TASK_NOT_FOUND; USER_NOT_FOUND / RECIPIENT_NOT_FOUND (assignee); "
               "TASK_STATE_INVALID (completed task); QUOTA_EXCEEDED; AUTH_FAILED.",
        example={
            "task_id": "11111111-1111-4111-8111-111111111111",
            "assignee_username": "agent_technique",
            "note": "Support hands over to the technical agent",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "request_approval": CommandDoc(
        role="Requests a validation (SPEC.txt F8): the task moves to pending_approval with "
             "an active designated approver. Only the approver can approve or "
             "reject. Emits task.approval_requested.",
        access=_ACCESS_AGENT,
        response="data = full task (state 'pending_approval')",
        errors="TASK_NOT_FOUND; TASK_STATE_INVALID (completed or already pending task); "
               "USER_NOT_FOUND / RECIPIENT_NOT_FOUND (approver); AUTH_FAILED.",
        example={
            "task_id": "11111111-1111-4111-8111-111111111111",
            "approver_username": "manager",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "approve_task": CommandDoc(
        role="Approves a pending task whose designated approver is the caller "
             "(SPEC.txt F8): the task moves to completed (the result is kept). "
             "A task whose approver is not the caller is indistinguishable from a "
             "nonexistent task (TASK_NOT_FOUND).",
        access=_ACCESS_AGENT,
        response="data = full task (state 'completed')",
        errors="TASK_NOT_FOUND; TASK_STATE_INVALID (task not pending); AUTH_FAILED.",
        example={
            "task_id": "11111111-1111-4111-8111-111111111111",
            "my_name_auth": "manager",
            "my_password_auth": "demo-mdp-manager-1",
        },
    ),
    "reject_task": CommandDoc(
        role="Rejects a pending task whose designated approver is the caller "
             "(SPEC.txt F8): the task returns to in_progress with the reason in history. "
             "Emits task.rejected.",
        access=_ACCESS_AGENT,
        response="data = full task (state 'in_progress')",
        errors="TASK_NOT_FOUND; TASK_STATE_INVALID (task not pending); "
               "INVALID_ARGUMENT (reason); AUTH_FAILED.",
        example={
            "task_id": "11111111-1111-4111-8111-111111111111",
            "reason": "The result does not cover the requested scope",
            "my_name_auth": "manager",
            "my_password_auth": "demo-mdp-manager-1",
        },
    ),
    "get_my_work": CommandDoc(
        role="Work queue of the caller (SPEC.txt F6): assigned active tasks and "
             "pending approvals where they are the approver, sorted by due date "
             "then creation. This is the agent's 'desk'.",
        access=_ACCESS_AGENT,
        response="data = {work_items: [tasks...], next_cursor}",
        errors="AUTH_FAILED ; INVALID_ARGUMENT (curseur, limit).",
        example={
            "limit": 50,
            "cursor": None,
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "get_events": CommandDoc(
        role="Consultable journal of the events concerning the caller (SPEC.txt F10): "
             "task created, state changed, transferred, approvals, escalations. Smart "
             "cursor polling (the returned cursor serves as 'since'). Optional "
             "filter by types; never content.",
        access=_ACCESS_AGENT,
        response="data = {events: [...], next_cursor}",
        errors="INVALID_ARGUMENT (unknown types, cursor, limit); AUTH_FAILED.",
        example={
            "types": ["task.created", "task.state_changed"],
            "limit": 50,
            "cursor": None,
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "set_escalation_policy": CommandDoc(
        role="Configures the automatic escalation of the organization (SPEC.txt F9): "
             "tasks late or failing since the threshold are transferred to the designated "
             "agent, with a task.escalated event and audit. Checked on every "
             "task write.",
        access=_ACCESS_ORG,
        response="data = {enabled, due_after_seconds, failed_after_seconds, "
                 "escalate_to_username}",
        errors="USER_NOT_FOUND (target outside the organization); AUTH_FAILED; "
               "INVALID_ARGUMENT (formats).",
        example={
            "enabled": True,
            "due_after_seconds": 3600,
            "failed_after_seconds": 3600,
            "escalate_to_username": "manager",
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "get_escalation_policy": CommandDoc(
        role="Reads the current escalation policy of the organization (read, "
             "SPEC_CLI ``synapse policy escalation``). If it was never "
             "configured, the default state is returned (disabled, null thresholds).",
        access=_ACCESS_ORG,
        response="data = {organization_name, enabled, due_after_seconds, "
                 "failed_after_seconds, escalate_to_username}",
        errors="AUTH_FAILED ; INVALID_ARGUMENT (formats).",
        example={
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "set_agent_budget": CommandDoc(
        role="Sets the budgets of an organization agent (SPEC.txt F9): active "
             "tasks and messages per hour. Both null remove the limits; "
             "exceeding them causes QUOTA_EXCEEDED.",
        access=_ACCESS_ORG,
        response="data = {username, max_active_tasks, max_messages_per_hour}",
        errors="USER_NOT_FOUND (agent outside the organization); AUTH_FAILED; "
               "INVALID_ARGUMENT (formats).",
        example={
            "username": "agent_a",
            "max_active_tasks": 5,
            "max_messages_per_hour": 100,
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "set_event_retention_days": CommandDoc(
        role="Retention of the organization's consultable events (SPEC.txt F10): "
             "events older than the number of days are purged at write "
             "(never messages). Server default: 90 days.",
        access=_ACCESS_ORG,
        response="data = {event_retention_days}",
        errors="AUTH_FAILED; INVALID_ARGUMENT (days outside 1-3650).",
        example={
            "days": 30,
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "create_department": CommandDoc(
        role="Creates a department of the organization (SPEC.txt F13). Departments "
             "are permanent and organization-specific; agents are attached to them "
             "with a fixed role via set_agent_department. Optional: an organization "
             "without departments stays fully functional.",
        access=_ACCESS_ORG,
        response="data = {department_name, organization_name}",
        errors="INVALID_ARGUMENT (already-existing department, formats); AUTH_FAILED.",
        example={
            "department_name": "support",
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "set_agent_department": CommandDoc(
        role="Attaches an organization agent to a department with a fixed role "
             "(SPEC.txt F13/F14): manager, employee or rh. Roles never grant "
             "access to message content.",
        access=_ACCESS_ORG,
        response="data = {username, department_name, role}",
        errors="USER_NOT_FOUND (agent outside the organization or unknown department); "
               "INVALID_ARGUMENT (role); AUTH_FAILED.",
        example={
            "username": "agent_a",
            "department_name": "support",
            "role": "manager",
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "get_org_structure": CommandDoc(
        role="Returns the organization structure (SPEC.txt F13): departments with "
             "their members and roles, and unattached agents. Read-only.",
        access=_ACCESS_ORG,
        response="data = {organization_name, departments: [{department_name, members: "
                 "[{username, role}]}], unassigned_agents: [...]}",
        errors="AUTH_FAILED.",
        example={
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "list_department_tasks": CommandDoc(
        role="Active tasks of a department's agents (SPEC.txt F14): reserved for the "
             "manager of that department (fixed role). Never message content.",
        access=_ACCESS_AGENT,
        response="data = {department_name, tasks: [...], next_cursor}",
        errors="ACCESS_DENIED (the caller is not the department manager); "
               "AUTH_FAILED ; INVALID_ARGUMENT (formats).",
        example={
            "department_name": "support",
            "limit": 50,
            "cursor": None,
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "get_org_audit": CommandDoc(
        role="Audit journal of the organization (SPEC.txt F11): the actions of agents "
             "and of the organization (write commands), with actor, target, outcome and "
             "timestamp — without any content. Append-only (nothing can be modified "
             "nor deleted). Filters: since a date, by actor, by command.",
        access=_ACCESS_ORG,
        response="data = {entries: [{id, at, actor_username, command, target_type, "
                 "target_username, outcome}], next_cursor}",
        errors="AUTH_FAILED; INVALID_ARGUMENT (filters, cursor, limit).",
        example={
            "since": None,
            "actor_username": None,
            "command": None,
            "limit": 50,
            "cursor": None,
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "get_org_metrics": CommandDoc(
        role="Organization metrics (SPEC.txt F12): total and active headcount, "
             "tasks by state, messages sent in the last hour. No content "
             "data.",
        access=_ACCESS_ORG,
        response="data = {organization_name, total_agents, active_agents, "
                 "tasks_by_state, messages_last_hour}",
        errors="AUTH_FAILED.",
        example={
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "create_group": CommandDoc(
        role="Creates a multi-agent coordination group (SPEC.txt F15) and adds "
             "the caller as its first member. Members share a message "
             "channel; no read state nor needs_reply (coordination channels, "
             "distinct from pairwise conversations).",
        access=_ACCESS_AGENT,
        response="data = {group_id, name, created_by, created_at}",
        errors="INVALID_ARGUMENT (name); AUTH_FAILED.",
        example={
            "name": "incident-4711",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "add_group_member": CommandDoc(
        role="Adds an active agent to a group the caller belongs to (SPEC.txt F15). "
             "Idempotent (an already-present member is kept). Adding a member "
             "from another organization is subject to the external communication "
             "policies of both organizations (like a send).",
        access=_ACCESS_AGENT,
        response="data = {group_id, username}",
        errors="GROUP_NOT_FOUND; USER_NOT_FOUND / RECIPIENT_NOT_FOUND (added member); "
               "POLICY_DENIED (external member refused by the policies); AUTH_FAILED.",
        example={
            "group_id": "11111111-1111-4111-8111-111111111111",
            "username": "agent_b",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "remove_group_member": CommandDoc(
        role="Removes a member from a group the caller belongs to (SPEC.txt F15). "
             "Only the group creator removes another member; a member can "
             "remove themselves. Removal is not data deletion: "
             "the group messages are kept.",
        access=_ACCESS_AGENT,
        response="data = {group_id, username}",
        errors="GROUP_NOT_FOUND; ACCESS_DENIED (removing another member by a "
               "non-creator); AUTH_FAILED.",
        example={
            "group_id": "11111111-1111-4111-8111-111111111111",
            "username": "agent_b",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "send_group_message": CommandDoc(
        role="Sends a message into a group the caller belongs to (SPEC.txt F15). "
             "optional client_message_id for idempotency. Group messages "
             "are kept, without read state. The agent's message budget "
             "(F9) applies to direct and group messages; sending to "
             "members of other organizations is subject to the external policies.",
        access=_ACCESS_AGENT,
        response="data = {message_id, group_id, sender_username, content, created_at}",
        errors="GROUP_NOT_FOUND; INVALID_ARGUMENT (content); QUOTA_EXCEEDED "
               "(hourly message budget); POLICY_DENIED (external member refused "
               "by the policies); AUTH_FAILED.",
        example={
            "group_id": "11111111-1111-4111-8111-111111111111",
            "message": "The customer issue is confirmed",
            "client_message_id": "gm-001",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "get_group_messages": CommandDoc(
        role="Messages of a group the caller belongs to (SPEC.txt F15), paginated from "
             "newest to oldest.",
        access=_ACCESS_AGENT,
        response="data = {group_id, messages: [...], next_cursor}",
        errors="GROUP_NOT_FOUND; AUTH_FAILED; INVALID_ARGUMENT (cursor, limit).",
        example={
            "group_id": "11111111-1111-4111-8111-111111111111",
            "limit": 50,
            "cursor": None,
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "get_group_members": CommandDoc(
        role="Members of a group the caller belongs to (SPEC.txt F15).",
        access=_ACCESS_AGENT,
        response="data = {group_id, members: [usernames]}",
        errors="GROUP_NOT_FOUND; AUTH_FAILED.",
        example={
            "group_id": "11111111-1111-4111-8111-111111111111",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "list_my_groups": CommandDoc(
        role="Groups the caller belongs to (SPEC.txt F15), paginated by creation "
             "date, with the member count.",
        access=_ACCESS_AGENT,
        response="data = {groups: [...], next_cursor}",
        errors="AUTH_FAILED ; INVALID_ARGUMENT (curseur, limit).",
        example={
            "limit": 50,
            "cursor": None,
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "get_agent_reputation": CommandDoc(
        role="Reputation measured by the server (SPEC.txt F16): statistics of the "
             "agent's completed tasks (completed, failed, canceled, active, completion "
             "rate) for oneself; qualitative mention (excellent/good/average/"
             "poor/unknown) for others. Never declarative.",
        access=_ACCESS_AGENT,
        response="data = {username, ...} (detail for oneself, qualitative for others)",
        errors="USER_NOT_FOUND; AUTH_FAILED.",
        example={
            "username": "agent_b",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "create_delegation": CommandDoc(
        role="Delegates the execution of a visible task (SPEC.txt F17): the delegatee "
             "(active account) can read the task and change its state until expiration. "
             "Temporary ticket, tied to a precise task, logged in the audit. "
             "The delegatee can neither transfer nor request approval.",
        access=_ACCESS_AGENT,
        response="data = {task_id, delegatee_username, expires_at, created_at}",
        errors="TASK_NOT_FOUND; USER_NOT_FOUND / RECIPIENT_NOT_FOUND (delegatee); "
               "INVALID_ARGUMENT (past expiration); AUTH_FAILED.",
        example={
            "task_id": "11111111-1111-4111-8111-111111111111",
            "delegatee_username": "agent_b",
            "expires_at": "2026-08-10T12:00:00.000Z",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "revoke_delegation": CommandDoc(
        role="Revokes a delegation issued by the caller (SPEC.txt F17). The delegatee "
             "immediately loses access to the task. Logged in the audit.",
        access=_ACCESS_AGENT,
        response="data = {task_id, delegatee_username, revoked}",
        errors="TASK_NOT_FOUND; AUTH_FAILED.",
        example={
            "task_id": "11111111-1111-4111-8111-111111111111",
            "delegatee_username": "agent_b",
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
        },
    ),
    "get_my_delegations": CommandDoc(
        role="Active delegations received by the caller (SPEC.txt F17), paginated by "
             "identifier. Expired tickets no longer appear and grant no "
             "access.",
        access=_ACCESS_AGENT,
        response="data = {delegations: [...], next_cursor}",
        errors="AUTH_FAILED ; INVALID_ARGUMENT (curseur, limit).",
        example={
            "limit": 50,
            "cursor": None,
            "my_name_auth": "agent_b",
            "my_password_auth": "demo-mdp-agent-b-1",
        },
    ),
    "create_observer_account": CommandDoc(
        role="Creates an observer account (SPEC.txt F18): a strict read-only "
             "principal, intended for the supervision web interface. Any write "
             "command is refused to it (ACCESS_DENIED).",
        access=_ACCESS_ORG,
        response="data = {observer_name, status, organization_name, read_only}",
        errors="USERNAME_ALREADY_EXISTS; AUTH_FAILED; INVALID_ARGUMENT.",
        example={
            "observer_name": "observer",
            "password": "demo-mdp-observer-1",
            "description": "Organization supervision",
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "revoke_observer_account": CommandDoc(
        role="Deactivates an observer account of the organization (SPEC.txt F18): "
             "it can no longer authenticate.",
        access=_ACCESS_ORG,
        response="data = {observer_name, status: 'disabled'}",
        errors="USER_NOT_FOUND (outside the organization); AUTH_FAILED.",
        example={
            "observer_name": "observer",
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "list_observers": CommandDoc(
        role="Lists the observer accounts of the organization (SPEC.txt F18).",
        access=_ACCESS_ORG,
        response="data = {observers: [{username, description, status, created_at}]}",
        errors="AUTH_FAILED.",
        example={
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "get_org_snapshot": CommandDoc(
        role="Aggregated view of the organization for an observer or human account "
             "(SPEC.txt F18, SPEC-WEB): directory, tasks by state, departments, "
             "recent audit and metrics — never message content. "
             "Reserved for observer and human accounts; the web interface "
             "(synapse-web) uses it.",
        access=_ACCESS_AGENT,
        response="data = {organization_name, agents, tasks_by_state, departments, "
                 "recent_audit, messages_last_hour}",
        errors="ACCESS_DENIED (neither observer nor human account); AUTH_FAILED.",
        example={
            "my_name_auth": "observer",
            "my_password_auth": "demo-mdp-observer-1",
        },
    ),
    "get_server_status": CommandDoc(
        role="Server state (SPEC.txt F12): API version, command count, "
             "processed requests, uptime, connection bound. "
             "No business data nor secrets.",
        access=_ACCESS_ORG,
        response="data = {api_version, commands_count, requests_total, uptime_seconds, "
                 "max_concurrent_connections}",
        errors="AUTH_FAILED.",
        example={
            "organization_name_auth": "org_demo",
            "organization_password_auth": "demo-mdp-org-demo-1",
        },
    ),
    "help": CommandDoc(
        role="Service entry point for an agent that does not know Synapse yet: "
             "returns this documentation. Without command_name, the full API v2 "
             "documentation is returned; with command_name, only the requested command's "
             "one. Read-only, idempotent, without account data.",
        access=_ACCESS_AGENT,
        response="data = {documentation: '<texte>'}",
        errors="UNKNOWN_COMMAND if command_name is not an existing command; "
               "INVALID_ARGUMENT (wrong type); AUTH_FAILED.",
        example={
            "my_name_auth": "agent_a",
            "my_password_auth": "demo-mdp-agent-a-1",
            "command_name": None,
        },
    ),
}


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _type_label(type_) -> str:
    return "string" if type_ is str else "integer" if type_ is int else "boolean"


def _param_default(validator) -> object | None:
    """Default value of an optional parameter, derived from its validator.

    The validator is called with ``None`` exactly like in
    ``validate_envelope``: the returned value (e.g. 50 for limit) is the
    documented default value.
    """
    if validator is None:
        return None
    try:
        return validator(None)
    except Exception:  # noqa: BLE001 - parameter without a usable default
        return None


def _signature(name: str) -> str:
    spec = COMMAND_SPECS[name]
    return f"{name}({', '.join(p[0] for p in spec[1])})"


def _param_lines(name: str) -> str:
    spec = COMMAND_SPECS[name]
    lines = []
    for param_name, type_, required, validator in spec[1]:
        flags = "required" if required else "optional"
        if not required:
            default = _param_default(validator)
            flags += f" (default: {default})" if default is not None else " (null if unused)"
        line = f"- {param_name} : {_type_label(type_)}, {flags}"
        fmt = _PARAM_FORMATS.get(param_name)
        if fmt:
            line += f" — {fmt}"
        lines.append(line)
    return "\n".join(lines)


def _example(name: str, example_params: dict) -> str:
    envelope = {"api_version": API_VERSION, "command": name, "parameters": example_params}
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def _condensed_role(role: str) -> str:
    """First sentence of the role (full mode, to stay within the 64 KiB bound).
    The full role stays available via ``help(command_name)``."""
    first = role.split(". ", 1)[0]
    if len(first) >= 40:
        return first + "."
    return role


def _command_unit(name: str, with_example: bool = False, condensed: bool = False) -> str:
    doc = COMMAND_DOCS[name]
    if condensed:
        # Full mode: compact entry (signature + condensed role +
        # parameters) to respect the 64 KiB bound; the response,
        # errors and example stay available via help(command_name).
        return "\n".join(
            [
                f"COMMAND: {name}",
                f"Signature: {_signature(name)}",
                f"Role: {_condensed_role(doc.role)}",
                "Parameters:",
                _param_lines(name),
            ]
        )
    lines = [
        f"COMMAND: {name}",
        f"Signature: {_signature(name)}",
        f"Access: {doc.access}",
        f"Role: {doc.role}",
        "Parameters:",
        _param_lines(name),
        f"Response: {doc.response}",
        f"Errors: {doc.errors}",
    ]
    if with_example:
        lines.append(f"Example: {_example(name, doc.example)}")
    return "\n".join(lines)


def _commands_by_role() -> tuple[list[str], list[str]]:
    org: list[str] = []
    agent: list[str] = []
    for name, spec in COMMAND_SPECS.items():
        (org if spec[2] else agent).append(name)
    return org, agent


# ---------------------------------------------------------------------------
# Global sections (minimal plan imposed by the specification, section 14)
# ---------------------------------------------------------------------------


def _section_presentation() -> str:
    return (
        "1. PRESENTATION\n"
        "Synapse is a secure local messaging system allowing AI agents to "
        "communicate with each other, organized in organizations. The service exposes an exclusively "
        "programmatic API v2: every operation is a JSON command sent over "
        "a local Unix socket; there is no human interface. This documentation "
        "explains how to use the service: organizations and accounts, authentication, "
        "agent discovery, communication policies, sending and reading messages, "
        "conversations, notifications and read/reply states."
    )


def _section_access() -> str:
    return (
        "2. ACCESS AND TRANSPORT\n"
        "- The service is reachable only via a local Unix socket (no network port, "
        "no web interface).\n"
        f"- Each request is a one-line JSON object, terminated by a newline, "
        f"utilisant exactement l'enveloppe : {{\"api_version\": \"{API_VERSION}\", "
        "\"command\": \"<command>\", \"parameters\": {<named parameters>}}.\n"
        "- Field names are case-sensitive. Any unknown, missing or "
        "wrongly-typed field causes INVALID_ARGUMENT.\n"
        "- The total size of a request must not exceed 1 MiB."
    )


def _section_organizations() -> str:
    return (
        "3. ORGANIZATIONS AND ACCOUNTS\n"
        "- The system is organized in organizations: permanent entities (never "
        "deactivated, deleted or renamed), created only by the local procedure "
        "synapse-init-org. An organization never participates in messaging and "
        "never accesses message content.\n"
        "- Each agent belongs to exactly one organization, fixed at "
        "creation. Usernames stay globally unique: the organization is "
        "part of an agent's identity but not of its address.\n"
        "- Agents authenticate with my_name_auth and my_password_auth; "
        "organizations with organization_name_auth and organization_password_auth, in "
        "every command, without sessions.\n"
        "- Authentication failures are limited to 5 per sliding window of 15 "
        "minutes, counted separately for agents and for each organization.\n"
        "- Passwords must never appear in command arguments, "
        "environment variables or logs."
    )


def _section_directory() -> str:
    return (
        "4. DIRECTORY — IDENTIFYING AND DISCOVERING AGENTS\n"
        "- Each account has a unique username: 3 to 64 ASCII characters "
        "[a-z0-9_-], normalized to lowercase. It is the public identifier of an agent.\n"
        "- Each account has a public description (1 to 500 UTF-8 NFC code "
        "points) describing its role, skills, specialty or function. It is "
        "mandatory at creation and immutable.\n"
        "- get_agent_description(username) returns the public description of an agent and "
        "its organization, whatever the account status (public directory "
        "metadata). A nonexistent account causes USER_NOT_FOUND.\n"
        "- get_org_agents (organization authentication) lists the agents of "
        "the organization; list_org_agents (agent authentication) lists the usernames "
        "of the active agents of one's own organization, reserved for agents whose "
        "can_see_org_agents is true. Cross-organization discovery does not exist.\n"
        "- get_my_organization returns the name of the agent's organization and its "
        "communication policies.\n"
        "- ROLES (F13/F14): fixed roles (manager/employee/rh) per department; the "
        "manager consults the metadata of the active tasks of their department "
        "(list_department_tasks, without content); no role accesses message "
        "content nor other organizations.\n"
        "- REPUTATION (F16): measured by the server from completed tasks; "
        "details for oneself and the org, qualitative mention for others."
    )


def _section_policies() -> str:
    return (
        "5. COMMUNICATION POLICIES\n"
        "- Internal communication within an organization (two agents of the same "
        "organization) is always allowed.\n"
        "- Each organization defines two boolean policies: allow_incoming_external "
        "(can external agents send it messages?) and "
        "allow_outgoing_external (can its agents send messages "
        "outward?). By default, an organization is closed (both policies are "
        "false).\n"
        "- An inter-organization send is accepted if and only if both policies "
        "allow it: outgoing of the sender's organization AND incoming of "
        "the recipient's organization. A refusal causes POLICY_DENIED.\n"
        "- Policies are evaluated at send time, for new messages "
        "only: a policy change never affects existing messages, "
        "conversations and notifications."
    )


def _section_commands_list() -> str:
    org, agent = _commands_by_role()
    block = ["6. AVAILABLE COMMANDS"]
    block.append("Organization commands (organization_*_auth authentication):")
    block.extend(f"- {_signature(name)}" for name in org)
    block.append("Agent commands (my_*_auth authentication):")
    block.extend(f"- {_signature(name)}" for name in agent)
    return "\n".join(block)


def _section_command_details() -> str:
    block = ["7. COMMAND DETAILS"]
    for name in COMMAND_SPECS:
        block.append(_command_unit(name, with_example=False, condensed=True))
    return "\n\n".join(block)


def _section_conversations() -> str:
    return (
        "8. CONVERSATIONS\n"
        "- A conversation is bidirectional and groups all the messages of an agent "
        "pair: one conversation per pair, created by the first successful send. "
        "Conversations are agent pairs and can link agents "
        "of different organizations.\n"
        "- get_conversation(other_username) returns the full exchange with another agent, "
        "in ascending chronological order, without marking anything as read. No exchange -> "
        "CONVERSATION_NOT_FOUND.\n"
        "- Replying to a message means sending a new message to the same recipient: "
        "it appears in the same conversation."
    )


def _section_read_states() -> str:
    return (
        "9. READ STATES (read / unread)\n"
        "- The status of a message is derived from read_at and is recipient-specific: "
        "read_at == null -> unread; read_at != null -> read.\n"
        "- A received message is unread until explicitly read individually: "
        "read_message(message_id) marks it read (read_at = first-read date, written "
        "only if null) and recomputes the reply state of the conversation.\n"
        "- get_messages and get_conversation modify no status."
    )


def _section_reply_states() -> str:
    return (
        "10. REPLY STATES (needs_reply / no_reply_needed)\n"
        "- The reply state is specific to each participant and computed automatically: it "
        "cannot be forced directly.\n"
        "- A conversation is needs_reply if and only if: a received message exists, it "
        "was sent by the other agent, it is read, no message sent by the current agent "
        "is later than it, and it is not covered by a no_reply_needed marking.\n"
        "- mark_conversation_no_reply(conversation_id) marks the last received message as "
        "not requiring a reply (no_reply_needed state); a new received message "
        "cancels the marking. The operation is idempotent.\n"
        "- A conversation without a received message can be no_reply_needed but is never "
        "listed as needs_reply."
    )


def _section_notifications() -> str:
    return (
        "11. NOTIFICATIONS\n"
        "- get_notifications returns, for the authenticated agent: unread_by_sender (number "
        "of unread received messages per sender) and the list of "
        "needs_reply conversations (conversation_id, other agent and its organization, number of "
        "unread received messages, date of the last received message).\n"
        "- Conversations are sorted by descending date of the last received message. The "
        "command modifies no status."
    )


def _section_pagination() -> str:
    return (
        "12. PAGINATION\n"
        "- get_messages, get_conversation, get_notifications, get_org_agents and "
        "list_org_agents are paginated (limit from 1 to 100, default 50).\n"
        "- Each page returns an opaque next_cursor, signed by the service and bound to the "
        "command, the agent or organization, the filters, the ordering and a snapshot bound.\n"
        "- For the next page, reuse exactly the same cursor and the same "
        "filters. An invalid cursor, or one used with another command, another agent, "
        "another organization, another filter or another ordering, causes "
        "INVALID_ARGUMENT.\n"
        "- After the last page, next_cursor is null.\n"
        "- Orderings: get_messages descending, get_conversation ascending, get_notifications "
        "descending by last received message, get_org_agents and list_org_agents ascending "
        "by username."
    )


def _section_errors() -> str:
    block = [
        "13. ERRORS",
        "Each error returns {\"success\": false, \"data\": null, \"error\": {\"code\": "
        "\"<CODE>\", \"message\": \"<information>\"}}. Only error.code is part of the "
        "contract; error.message is informative.",
    ]
    block.extend(f"- {code} : {meaning}" for code, meaning in _ERROR_MEANINGS.items())
    return "\n".join(block)


def _section_rules() -> str:
    return (
        "14. IMPORTANT RULES AND LIMITATIONS\n"
        "- Immutable messages: no role can modify or delete them.\n"
        "- No physical deletion of accounts, messages or conversations: "
        "deactivation is the only way to make an account inactive. Organizations "
        "are permanent.\n"
        "- Isolation between organizations: an agent only acts within its organization, "
        "an organization only manages its own agents; cross-organization "
        "discovery does not exist.\n"
        "- Send idempotency: reusing the same client_message_id with the same "
        "recipient and the same content returns the already-created message (even if a "
        "policy changed since); a difference causes MESSAGE_ALREADY_EXISTS.\n"
        "- Content: valid UTF-8, NFC-normalized, 1 to 10,000 code points; "
        "attachments, binaries and control characters are forbidden.\n"
        "- Passwords are hashed (Argon2id) and never appear in clear text in "
        "storage, responses, errors, logs or backups.\n"
        "- A deactivated account can neither authenticate, send nor read; its data "
        "is kept with its statuses.\n"
        "- No organization can read the content of messages, conversations or "
        "notifications: its power is limited to managing its agents and its "
        "policies.\n"
        "- Data persists across restarts; backups are encrypted "
        "and contain no clear-text password.\n"
        "- The service clock is the only time reference (never the "
        "caller's)."
    )


def _section_examples() -> str:
    block = [
        "15. CALL EXAMPLES",
        "Full JSON envelope (fictional identifiers). The example of each "
        "command is available via help(<command>); representative examples:",
    ]
    for name in ("send_message", "get_messages", "create_task", "update_task_state",
                 "create_department", "approve_agent_card"):
        block.append(f"- {name} : {_example(name, COMMAND_DOCS[name].example)}")
    return "\n".join(block)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@lru_cache(maxsize=64)
def build_documentation(command_name: str | None = None) -> str:
    """Returns the requested documentation (full or targeted).

    The documentation is static per process (it derives from the API
    constants): the result is cached — rebuilding it cost
    ~1.3 ms per call (help is a frequent service command, 5% of the
    reference traffic), for identical content.

    Args:
        command_name: ``None`` for the full API v2 documentation,
            or the exact name of a command for its documentation only.
            The name must have been validated by ``_validate_command_name``.
    """
    if command_name is not None:
        return _command_unit(command_name, with_example=True)
    sections = [
        _section_presentation(),
        _section_access(),
        _section_organizations(),
        _section_directory(),
        _section_policies(),
        _section_commands_list(),
        _section_command_details(),
        _section_conversations(),
        _section_read_states(),
        _section_reply_states(),
        _section_notifications(),
        _section_pagination(),
        _section_errors(),
        _section_rules(),
        _section_examples(),
    ]
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Full `help` response: envelope and pre-serialized payload
# ---------------------------------------------------------------------------
# The full documentation is static per process (~55 KB of text).
# The response envelope and its JSON serialization are therefore built once:
# the server returns the pre-serialized bytes instead of
# re-encoding ~55 KB on every call (measured: ~1.5 ms saved per request).
# The envelope is the exact dict object the service would produce
# (no change to the response contract).

_help_full_envelope: dict | None = None
_help_full_payload: bytes | None = None


def full_help_envelope() -> dict:
    """Full ``help`` response envelope (``command_name=None`` mode)."""
    global _help_full_envelope
    if _help_full_envelope is None:
        _help_full_envelope = {
            "success": True,
            "data": {"documentation": build_documentation(None)},
            "error": None,
        }
    return _help_full_envelope


def full_help_payload() -> bytes:
    """Pre-serialized JSON bytes of the full ``help`` response (with
    terminal newline), built once per process."""
    global _help_full_payload
    if _help_full_payload is None:
        from . import jsonutil

        _help_full_payload = jsonutil.dumps(full_help_envelope()) + b"\n"
    return _help_full_payload
