"""Tests for the ``help`` command (SPEC.txt section 13): built-in
    documentation, full and targeted modes, validation, authentication,
    permissions, and consistency with COMMAND_SPECS / the error codes.
"""

from __future__ import annotations

import pytest

from synapse.client import ApiClientError
from synapse.errors import AUTH_FAILED, INVALID_ARGUMENT, UNKNOWN_COMMAND
from synapse.helpdoc import (
    COMMAND_DOCS,
    MAX_DOCUMENTATION_BYTES,
    build_documentation,
    _ERROR_MEANINGS,
    _PARAM_FORMATS,
    _param_default,
)
from synapse.validation import COMMAND_SPECS

from .conftest import ORG_NAME, ORG_PASSWORD, ALICE, ALICE_PASSWORD, BOB, BOB_PASSWORD


# ---------------------------------------------------------------------------
# Drift guards: the documentation is generated from the source of truth,
# but we verify no item is missing or phantom.
# ---------------------------------------------------------------------------


def test_help_docs_cover_all_commands():
    """Every API command has documentary prose, and vice versa (no phantom
    command documented)."""
    assert set(COMMAND_DOCS) == set(COMMAND_SPECS)


def test_help_docs_cover_all_params():
    """Every parameter of every command has a documented format."""
    declared = {p[0] for spec in COMMAND_SPECS.values() for p in spec[1]}
    assert declared == set(_PARAM_FORMATS)


def test_help_docs_cover_all_errors():
    """The error documentation covers exactly the API codes.

    The codes are the keys of ``_MESSAGES`` (the stable API contract) —
    NOT every UPPER_CASE constant in ``errors.py``: the module also
    carries contextual message constants (``ACCESS_DENIED_HUMAN_COMMANDS``
    etc.) which are not error codes and must not be documented as such.
    """
    import synapse.errors as errors_mod

    codes = set(errors_mod._MESSAGES)
    assert set(_ERROR_MEANINGS) == codes


def test_param_default_guards_raising_validator():
    """A validator that raises on None does not break default generation."""

    def boom(value):  # noqa: ANN001
        raise ValueError(value)

    assert _param_default(boom) is None


# ---------------------------------------------------------------------------
# Full mode
# ---------------------------------------------------------------------------


def test_help_full_documentation(fx):
    data = fx.client.help(ALICE, ALICE_PASSWORD)
    assert set(data) == {"documentation"}
    doc = data["documentation"]
    assert isinstance(doc, str) and doc.strip()
    assert len(doc.encode("utf-8")) <= MAX_DOCUMENTATION_BYTES
    # no control characters in the text
    assert not any(ord(ch) < 32 and ch not in "\n\t" for ch in doc)


def test_help_full_documents_all_commands(fx):
    doc = fx.client.help(ALICE, ALICE_PASSWORD)["documentation"]
    for name in COMMAND_SPECS:
        assert f"COMMAND: {name}" in doc
        assert f"Signature: {name}(" in doc


def test_help_full_documents_all_params(fx):
    doc = fx.client.help(ALICE, ALICE_PASSWORD)["documentation"]
    for spec in COMMAND_SPECS.values():
        for param_name, _type, required, _validator in spec[1]:
            assert f"- {param_name} :" in doc
            flag = "required" if required else "optional"
            assert flag in doc
    # default value derived from the validator (limit -> 50)
    assert "- limit : integer, optional (default: 50)" in doc


def test_help_full_structure_15_sections(fx):
    """SPEC.txt §14: the full mode contains the 15 mandated sections, in
    order, with their titles (exhaustive check, not just the count)."""
    doc = fx.client.help(ALICE, ALICE_PASSWORD)["documentation"]
    for i in range(1, 16):
        assert f"{i}. " in doc
    for title in (
        "PRESENTATION",
        "ACCESS AND TRANSPORT",
        "ORGANIZATIONS AND ACCOUNTS",
        "DIRECTORY",
        "COMMUNICATION POLICIES",
        "AVAILABLE COMMANDS",
        "COMMAND DETAILS",
        "CONVERSATIONS",
        "READ STATES",
        "REPLY STATES",
        "NOTIFICATIONS",
        "PAGINATION",
        "ERRORS",
        "IMPORTANT RULES AND LIMITATIONS",
        "CALL EXAMPLES",
    ):
        assert title in doc


def test_help_doc_mentions_description_directory(fx):
    """The documentation covers the directory (description, get_agent_description)."""
    doc = fx.client.help(ALICE, ALICE_PASSWORD)["documentation"]
    assert "DIRECTORY" in doc
    assert "public description" in doc
    assert "get_agent_description" in doc
    assert "COMMAND: get_agent_description" in doc


def test_help_doc_explains_communication(fx):
    doc = fx.client.help(ALICE, ALICE_PASSWORD)["documentation"]
    assert "send" in doc and "conversation" in doc
    assert "needs_reply" in doc and "no_reply_needed" in doc
    assert "read_at" in doc and "unread" in doc
    assert "PAGINATION" in doc and "NOTIFICATIONS" in doc
    assert "AUTH_FAILED" in doc and "UNKNOWN_COMMAND" in doc


# ---------------------------------------------------------------------------
# Targeted mode
# ---------------------------------------------------------------------------


def test_help_single_command(fx):
    doc = fx.client.help(ALICE, ALICE_PASSWORD, "send_message")["documentation"]
    assert "COMMAND: send_message" in doc
    assert "Signature: send_message(" in doc
    assert "Example: {" in doc
    assert "recipient_username" in doc
    assert "client_message_id" in doc
    # strictly targeted documentation
    assert "COMMAND: get_messages" not in doc
    assert "COMMAND: help" not in doc
    assert "COMMAND: create_agent" not in doc


def test_help_single_help_command(fx):
    doc = fx.client.help(ALICE, ALICE_PASSWORD, "help")["documentation"]
    assert "COMMAND: help" in doc
    assert "command_name" in doc
    assert "Example: {" in doc


def test_help_every_command_targeted(fx):
    """Every command answers in targeted mode with its own documentation."""
    for name in COMMAND_SPECS:
        doc = fx.client.help(ALICE, ALICE_PASSWORD, name)["documentation"]
        assert f"COMMAND: {name}" in doc
        for other in COMMAND_SPECS:
            if other != name:
                assert f"COMMAND: {other}" not in doc


# ---------------------------------------------------------------------------
# Validation and errors
# ---------------------------------------------------------------------------


def test_help_unknown_command_name(fx):
    for bad in ("teleport", "", "SEND_MESSAGE", "send message"):
        with pytest.raises(ApiClientError) as exc:
            fx.client.help(ALICE, ALICE_PASSWORD, bad)
        assert exc.value.code == UNKNOWN_COMMAND


def test_help_command_name_wrong_type(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.request(
            "help",
            {
                "my_name_auth": ALICE,
                "my_password_auth": ALICE_PASSWORD,
                "command_name": 42,
            },
        )
    assert exc.value.code == INVALID_ARGUMENT


def test_help_validation_precedes_auth(fx):
    """An unknown command_name is rejected even before authentication."""
    with pytest.raises(ApiClientError) as exc:
        fx.client.help("ghost", "motdepasse-inexistant", "teleport")
    assert exc.value.code == UNKNOWN_COMMAND


def test_help_envelope_strict(fx, raw_socket_client):
    import json as json_mod

    base = {
        "api_version": "v2",
        "command": "help",
        "parameters": {
            "my_name_auth": ALICE,
            "my_password_auth": ALICE_PASSWORD,
            "command_name": None,
        },
    }
    # unknown field rejected
    extra = json_mod.loads(json_mod.dumps(base))
    extra["parameters"]["extra"] = 1
    resp = raw_socket_client(json_mod.dumps(extra) + "\n")
    assert resp["error"]["code"] == INVALID_ARGUMENT
    # missing required field rejected
    missing = json_mod.loads(json_mod.dumps(base))
    del missing["parameters"]["my_name_auth"]
    resp = raw_socket_client(json_mod.dumps(missing) + "\n")
    assert resp["error"]["code"] == INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# Authentication and permissions
# ---------------------------------------------------------------------------


def test_help_auth_failures(fx):
    with pytest.raises(ApiClientError) as exc:
        fx.client.help(ALICE, "mauvais-motdepasse")
    assert exc.value.code == AUTH_FAILED
    with pytest.raises(ApiClientError) as exc:
        fx.client.help("ghost", "nimporte-quel-motdepasse")
    assert exc.value.code == AUTH_FAILED


def test_help_disabled_caller_denied(fx):
    fx.client.create_agent("carol", "motdepasse-carol-1", "Agent carol", ORG_NAME, ORG_PASSWORD)
    fx.client.deactivate_agent("carol", ORG_NAME, ORG_PASSWORD)
    with pytest.raises(ApiClientError) as exc:
        fx.client.help("carol", "motdepasse-carol-1")
    assert exc.value.code == AUTH_FAILED


def test_help_available_to_any_active_agent(fx):
    """help is accessible to any active agent account (not an organization
    command: no ACCESS_DENIED)."""
    assert fx.client.help(ALICE, ALICE_PASSWORD)["documentation"]
    assert fx.client.help(BOB, BOB_PASSWORD)["documentation"]


def test_help_idempotent(fx):
    first = fx.client.help(ALICE, ALICE_PASSWORD)["documentation"]
    second = fx.client.help(ALICE, ALICE_PASSWORD)["documentation"]
    assert first == second
    # no notification generated (no side effects)
    assert fx.client.get_notifications(ALICE, ALICE_PASSWORD)["needs_reply"] == []


def test_help_response_envelope(fx, raw_socket_client):
    import json as json_mod

    resp = raw_socket_client(
        json_mod.dumps(
            {
                "api_version": "v2",
                "command": "help",
                "parameters": {
                    "my_name_auth": ALICE,
                    "my_password_auth": ALICE_PASSWORD,
                    "command_name": None,
                },
            }
        )
        + "\n"
    )
    assert set(resp) == {"success", "data", "error"}
    assert resp["success"] is True and resp["error"] is None
    assert set(resp["data"]) == {"documentation"}
    assert isinstance(resp["data"]["documentation"], str)


# ---------------------------------------------------------------------------
# The documentation claims nothing nonexistent
# ---------------------------------------------------------------------------


def test_help_doc_claims_only_real_commands(fx):
    doc = fx.client.help(ALICE, ALICE_PASSWORD)["documentation"]
    for line in doc.splitlines():
        if line.startswith("COMMAND: "):
            name = line[len("COMMAND: "):]
            assert name in COMMAND_SPECS


def test_help_doc_contains_no_real_secrets(fx):
    doc = fx.client.help(ALICE, ALICE_PASSWORD)["documentation"]
    for secret in (ALICE_PASSWORD, BOB_PASSWORD, ORG_PASSWORD):
        assert secret not in doc


def test_build_documentation_direct():
    """Direct generation (without a server) produces both modes."""
    full = build_documentation()
    assert full.startswith("1. PRESENTATION")
    assert "15. CALL EXAMPLES" in full
    single = build_documentation("read_message")
    assert single.startswith("COMMAND: read_message")
    assert "Example: " in single
