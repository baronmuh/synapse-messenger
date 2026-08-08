"""Tests for the synapse_architecte skill family scripts.

The family (repo `synapse_architecte/`) ships two shell tools:
- 03-provision-profiles/scripts/verify_providers.sh — provider
  inheritance proof (model match + auth 0600 + live LLM query);
- 05-evaluate-secure/scripts/audit_rbac.sh — grep audit for
  reserved/admin/credential patterns in agent-facing skills.

These tests cover syntax and behavior WITHOUT calling the LLM (the live
provider proof is a manual step with a real profile). The audit script
is tested both ways: no false positive (clean tree → rc 0) and no false
negative (a tree with patterns → rc 1).
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
FAMILY = REPO / "synapse_architecte"
VERIFY = FAMILY / "03-provision-profiles" / "scripts" / "verify_providers.sh"
AUDIT = FAMILY / "05-evaluate-secure" / "scripts" / "audit_rbac.sh"
AGENT_SKILLS = REPO / "agent-skills"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_scripts_have_valid_syntax():
    for script in (VERIFY, AUDIT):
        assert script.exists(), script
        proc = subprocess.run(["bash", "-n", str(script)], capture_output=True,
                              text=True)
        assert proc.returncode == 0, f"{script.name}: {proc.stderr}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_audit_rbac_clean_agent_skills():
    """No false positive: the agent-facing package must pass clean."""
    if not AGENT_SKILLS.is_dir():
        pytest.skip("agent-skills/ not present")
    proc = subprocess.run(["bash", str(AUDIT), str(AGENT_SKILLS)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_audit_rbac_detects_patterns(tmp_path):
    """No false negative: a skill documenting reserved calls is flagged."""
    bad = tmp_path / "skill"
    bad.mkdir()
    (bad / "SKILL.md").write_text(
        "# bad skill\n\nRun `synapse agent create bob --password-stdin`\n"
        "and export SYNAPSE_PASSWORD=secret\n", encoding="utf-8")
    proc = subprocess.run(["bash", str(AUDIT), str(bad)],
                          capture_output=True, text=True)
    assert proc.returncode == 1, "audit must reject reserved/admin patterns"
    assert "FOUND" in proc.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_verify_providers_usage_without_args():
    proc = subprocess.run(["bash", str(VERIFY)], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "usage" in proc.stderr.lower()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_verify_providers_nonexistent_profile():
    proc = subprocess.run(["bash", str(VERIFY), "no-such-profile-xyz"],
                          capture_output=True, text=True)
    assert proc.returncode != 0


def test_family_frontmatters_valid():
    """Every SKILL.md of the family respects the Hermes budgets:
    name ≤ 64, description ≤ 60 chars ending with a period, size range."""
    import re

    skills = sorted(
        md for md in FAMILY.rglob("SKILL.md")
        if "templates" not in md.parts and "references" not in md.parts
    )
    assert len(skills) >= 7, "the family must contain at least 7 skills"
    for md in skills:
        text = md.read_text(encoding="utf-8")
        name = re.search(r"^name:\s*(\S+)", text, re.M)
        desc = re.search(r'^description:\s*"(.+?)"', text, re.M)
        assert name, f"{md}: missing name"
        assert desc, f"{md}: missing description"
        assert len(name.group(1)) <= 64, f"{md}: name too long"
        assert len(desc.group(1)) <= 60, f"{md}: description > 60 chars"
        assert desc.group(1).endswith("."), f"{md}: description no period"
        assert 1000 <= len(text) <= 100_000, f"{md}: size out of range"
    # the explicit validation gate must exist and be wired in the umbrella
    gate = FAMILY / "06-present-validate" / "SKILL.md"
    assert gate.exists(), "06-present-validate missing"
    umbrella = FAMILY / "synapse-architect" / "SKILL.md"
    assert "present-validate-plan" in umbrella.read_text(encoding="utf-8"), \
        "umbrella must reference the validation gate"
