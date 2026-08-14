"""Unit coverage for a few remaining pure helpers in ``synapse/helpdoc.py``.

The help documentation is otherwise exercised end-to-end by the API tests;
this closes the last uncovered branches (condensed-role truncation).
"""

from __future__ import annotations

from synapse import helpdoc


def test_condensed_role_short():
    # Short role -> returned unchanged.
    role = "Sends messages between agents."
    assert helpdoc._condensed_role(role) == role


def test_condensed_role_long_truncated():
    # Role with first sentence >= 40 chars -> first sentence + "."
    role = ("This is a very long first sentence that describes the role of "
            "the agent in detail. Second sentence.")
    out = helpdoc._condensed_role(role)
    assert out.endswith(".")
    assert "Second" not in out


def test_condensed_role_no_period_long():
    # Long text without a ". " separator -> the whole role + "." (the
    # split has no effect, and len(first)>=40 triggers the + "." branch).
    role = "x" * 60
    out = helpdoc._condensed_role(role)
    assert out == role + "."
