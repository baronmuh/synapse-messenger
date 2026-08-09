"""Normalized errors of the API v2.

Each error has a stable code (used by clients) and a message
informative and localizable. Only ``error.code`` is part of the API
contract.

Single source of truth for every user-visible message: the named
constants below and ``_MESSAGES``. Changing a message = editing THIS
file only — callers never embed the text.
"""

from __future__ import annotations

# --- stable error codes (API contract) ---
AUTH_FAILED = "AUTH_FAILED"
ACCESS_DENIED = "ACCESS_DENIED"
INVALID_ARGUMENT = "INVALID_ARGUMENT"
UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
USERNAME_ALREADY_EXISTS = "USERNAME_ALREADY_EXISTS"
USER_NOT_FOUND = "USER_NOT_FOUND"
RECIPIENT_NOT_FOUND = "RECIPIENT_NOT_FOUND"
MESSAGE_NOT_FOUND = "MESSAGE_NOT_FOUND"
CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
MESSAGE_ALREADY_EXISTS = "MESSAGE_ALREADY_EXISTS"
POLICY_DENIED = "POLICY_DENIED"
TASK_NOT_FOUND = "TASK_NOT_FOUND"
TASK_STATE_INVALID = "TASK_STATE_INVALID"
TASK_DEPENDENCY_NOT_MET = "TASK_DEPENDENCY_NOT_MET"
QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
GROUP_NOT_FOUND = "GROUP_NOT_FOUND"
INTERNAL_ERROR = "INTERNAL_ERROR"

# --- default messages per code ---
_MESSAGES: dict[str, str] = {
    AUTH_FAILED: "Invalid credentials",
    ACCESS_DENIED: "Access denied",
    INVALID_ARGUMENT: "Invalid argument",
    UNKNOWN_COMMAND: "Unknown command",
    USERNAME_ALREADY_EXISTS: "This username already exists",
    USER_NOT_FOUND: "User not found",
    RECIPIENT_NOT_FOUND: "Recipient not found",
    MESSAGE_NOT_FOUND: "Message not found",
    POLICY_DENIED: "External communication denied by the organization policy",
    CONVERSATION_NOT_FOUND: "Conversation not found",
    MESSAGE_ALREADY_EXISTS: "This message already exists with different characteristics",
    TASK_NOT_FOUND: "Task not found",
    TASK_STATE_INVALID: "Invalid task state transition",
    TASK_DEPENDENCY_NOT_MET: "The task dependencies are not all completed",
    QUOTA_EXCEEDED: "Quota exceeded",
    GROUP_NOT_FOUND: "Group not found",
    INTERNAL_ERROR: "Internal service error",
}

# --- contextual message constants (variants of a code) ---
ACCESS_DENIED_HUMAN_ONLY_ORG = "A human only manages their own organization"
ACCESS_DENIED_HUMAN_COMMANDS = "Command reserved for human accounts"
ACCESS_DENIED_ORG_COMMANDS = "Command reserved for organizations"
ACCESS_DENIED_WEB_AND_HUMAN = "Command reserved for the web interface and human accounts"
ACCESS_DENIED_WEB_LOCAL_SCOPE = "Local web identity reserved for list_orgs and create_org"
ACCESS_DENIED_MANAGER_ROLE = "Manager role required for this department"
ACCESS_DENIED_OBSERVER_READONLY = "Observer account is read-only"
ACCESS_DENIED_OBSERVER_OR_HUMAN = "Observer or human account required"
ACCESS_DENIED_GROUP_CREATOR = "Only the group creator removes a member"
ACCESS_DENIED_CAN_SEE_ORG_AGENTS = "Permission can_see_org_agents required"
ACCESS_DENIED_HUMAN_NO_DEACTIVATE = "The human account cannot be deactivated"
ACCESS_DENIED_HUMAN_NO_MODIFY = "The human account cannot be modified"
ACCESS_DENIED_HUMAN_NO_PASSWORD = "The human account has no password of its own"

AUTH_FAILED_TOO_MANY = "Too many failed attempts, try again later"

INVALID_ARGUMENT_CONV_NO_RECEIVED = "Conversation not found or without a received message"
INVALID_ARGUMENT_HUMAN_NAME_USED = "Human account name already used"
INVALID_ARGUMENT_HUMAN_AUTO = "Human accounts are created automatically"
INVALID_ARGUMENT_CURSOR = "Invalid cursor"
INVALID_ARGUMENT_CURSOR_FILTERS = "Invalid cursor for these filters"
INVALID_ARGUMENT_CURSOR_AGENT = "Invalid cursor for this agent"
INVALID_ARGUMENT_CURSOR_COMMAND = "Invalid cursor for this command"
INVALID_ARGUMENT_CURSOR_SORT = "Invalid cursor for this sort"
INVALID_ARGUMENT_ORG_NAME_USED = "Organization name already used"
INVALID_ARGUMENT_APPROVER_SAME = "The approver must be different from the requester"
INVALID_ARGUMENT_ORG_DEACTIVATED = "The organization is already deactivated"
INVALID_ARGUMENT_RECIPIENT_SAME = "The recipient must be different from the sender"
INVALID_ARGUMENT_DEPT_EXISTS = "This department already exists"
INVALID_ARGUMENT_HUMAN_NAME_RESERVED = "This name is reserved for the organization's human account"
INVALID_ARGUMENT_UNKNOWN_ORG = "Unknown organization"
INVALID_ARGUMENT_CLIENT_TASK_ID_USED = "client_task_id already used for another task"
INVALID_ARGUMENT_EXPIRES_FUTURE = "expires_at must be in the future"
INVALID_ARGUMENT_OTHER_USERNAME_SAME = "other_username must be different from the authenticated agent"

MESSAGE_ALREADY_EXISTS_CLIENT_ID = "client_message_id already used with a different recipient or content"

QUOTA_EXCEEDED_TASK_BUDGET = "Active task budget exceeded"
QUOTA_EXCEEDED_MESSAGE_BUDGET = "Hourly message budget exceeded"
QUOTA_EXCEEDED_DEPTH = "Maximum dependency depth exceeded"

RECIPIENT_NOT_FOUND_ACTIVE = "The account must be active"

TASK_STATE_INVALID_COMPLETED_TRANSFER = "A completed task cannot be transferred"

USER_NOT_FOUND_DEPT_UNKNOWN = "Department unknown in this organization"
USER_NOT_FOUND_NO_CARD = "This agent has no card to validate"


class ApiError(Exception):
    """Business error returned to the client as ``error.code``.

    Attributes:
        code: stable error code, part of the API contract.
        message: informative, localizable message, never interpreted by the client.
    """

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or _MESSAGES.get(code, code))
        self.code = code
        self.message = message or _MESSAGES.get(code, code)
