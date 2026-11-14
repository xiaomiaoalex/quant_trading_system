"""Ruleset migration contract (C2).

The file is a prepared migration, not an applied remote change.  These tests
keep the required-check name, strict sync and non-author review requirement
from drifting before an administrator applies it.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULESET = ROOT / "deploy" / "github-ruleset.ci-required.json"
MIGRATION_DOC = ROOT / "deploy" / "RULESET_MIGRATION.md"


def _ruleset() -> dict:
    return json.loads(RULESET.read_text(encoding="utf-8"))


def _rule(payload: dict, rule_type: str) -> dict:
    matches = [rule for rule in payload["rules"] if rule["type"] == rule_type]
    assert len(matches) == 1, rule_type
    return matches[0]


def test_only_the_aggregate_gate_is_required():
    checks = _rule(_ruleset(), "required_status_checks")["parameters"]["required_status_checks"]
    assert [check["context"] for check in checks] == ["CI Required"]


def test_strict_sync_and_non_author_review_are_enforced():
    payload = _ruleset()
    assert _rule(payload, "required_status_checks")["parameters"][
        "strict_required_status_checks_policy"
    ]
    review = _rule(payload, "pull_request")["parameters"]
    assert review["required_approving_review_count"] >= 1
    assert review["dismiss_stale_reviews_on_push"] is True
    assert review["require_last_push_approval"] is True
    assert review["required_review_thread_resolution"] is True


def test_main_is_protected_from_deletion_and_force_push():
    types = {rule["type"] for rule in _ruleset()["rules"]}
    assert {"deletion", "non_fast_forward"} <= types
    conditions = _ruleset()["conditions"]["ref_name"]["include"]
    assert conditions == ["refs/heads/main"]


def test_migration_document_states_it_was_not_applied():
    text = MIGRATION_DOC.read_text(encoding="utf-8")
    assert "未执行" in text
    assert "CI Required" in text
    assert "13369928" in text
