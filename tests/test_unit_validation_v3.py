"""Coverage of the validators' error branches (direct unit calls,
like test_unit_validation.py).
"""

from __future__ import annotations

import pytest

import synapse.validation as v
from synapse.errors import ApiError


def _raises(func, *args):
    with pytest.raises(ApiError):
        func(*args)


def test_business_reference_non_string():
    _raises(v.normalize_business_reference, 123)


def test_card_text_errors():
    _raises(v._normalize_card_text, 42, "field", 64)
    _raises(v._normalize_card_text, "a\x00b", "field", 64)


def test_due_at_errors():
    _raises(v.validate_due_at, 123)
    _raises(v.validate_due_at, "pas-une-date")


def test_depends_on_errors():
    _raises(v.validate_depends_on, "pas-une-liste")
    _raises(v.validate_depends_on, ["11111111-1111-4111-8111-111111111111"] * 21)


def test_event_types_errors():
    _raises(v.validate_event_types, "chaîne")
    _raises(v.validate_event_types, ["task.created"] * 21)


def test_budget_errors():
    _raises(v.validate_budget, "cinq")
    _raises(v.validate_budget, 0)
    _raises(v.validate_budget, -3)
    _raises(v.validate_budget, True)  # boolean refused


def test_capabilities_errors():
    _raises(v.normalize_capabilities, None)
    _raises(v.normalize_capabilities, "pas-une-liste")
    _raises(v.normalize_capabilities, [123])
    _raises(v.normalize_capabilities, ["a"] * 51)


def test_tools_errors():
    _raises(v.normalize_tools, "pas-une-liste")
    _raises(v.normalize_tools, [123])
    _raises(v.normalize_tools, ["a"] * 51)


def test_group_name_errors():
    _raises(v.normalize_group_name, 42)
    _raises(v.normalize_group_name, "")


def test_principal_type_errors():
    from synapse.errors import ApiError

    with pytest.raises(ApiError):
        v.validate_principal_type("robot")
    with pytest.raises(ApiError):
        v.validate_principal_type(42)


def test_org_role_errors():
    from synapse.errors import ApiError

    with pytest.raises(ApiError):
        v.validate_org_role("ceo")
    with pytest.raises(ApiError):
        v.validate_org_role(42)


def test_priority_filter_passthrough():
    # the priority filter accepts None (no filter)
    assert v._validate_priority_filter(None) is None
    assert v._validate_priority_filter("high") == "high"


def test_optional_client_id_none():
    assert v._validate_optional_client_id(None) is None
    assert v._validate_optional_client_id("abc-123") == "abc-123"


def test_task_text_and_state_errors():
    # _normalize_task_text: non-string, control character
    _raises(v._normalize_task_text, 42, "field", 1, 64)
    _raises(v._normalize_task_text, "a\x00b", "field", 1, 64)
    # validate_task_state: unknown value
    _raises(v.validate_task_state, 123)
    _raises(v.validate_task_state, "done")


def test_update_task_state_error_message_excludes_pending_approval():
    """m1: the update_task_state validation message must not advertise
    pending_approval as settable — it is a derived state (request_approval)
    that the state machine then refuses (TASK_STATE_INVALID)."""
    import synapse.validation as v_mod

    with pytest.raises(ApiError) as exc:
        v_mod.validate_task_state("nimporte-quoi")
    assert "pending_approval" not in str(exc.value)
    assert "submitted" in str(exc.value)
    # the derived state stays accepted as a list_tasks filter
    assert v_mod.validate_task_state("pending_approval") == "pending_approval"


def test_parse_bool_cli():
    """Flexible conversion of booleans of the raw ``api`` access (successor of
    the old _parse_bool of the flat CLI)."""
    from synapse.cli.api import _coerce

    assert _coerce("true") is True
    assert _coerce("0") is False
    assert _coerce("yes") is True
    assert _coerce("no") is False
    assert _coerce("42") == 42  # integer
    assert _coerce("done") == "done"  # string unchanged
