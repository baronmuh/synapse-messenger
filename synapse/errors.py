"""Normalized errors of the API v2.

Each error has a stable code (used by clients) and a message
informatif localisable. Seul ``error.code`` fait partie du contrat de l'API.
"""

from __future__ import annotations

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

_MESSAGES: dict[str, str] = {
    AUTH_FAILED: "Identifiants invalides",
    ACCESS_DENIED: "Access denied",
    INVALID_ARGUMENT: "Argument invalide",
    UNKNOWN_COMMAND: "Commande unknowne",
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


class ApiError(Exception):
    """Business error returned to the client as ``error.code``.

    Attributes:
        code: code d'stable error, part of the API contractAPI.
        message: informative, localizable message, never interpreted by the client.
    """

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or _MESSAGES.get(code, code))
        self.code = code
        self.message = message or _MESSAGES.get(code, code)
