"""Contract tests for deterministic PR candidate image planning."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import prepare_pr_preview as planner  # noqa: E402


def _base(tmp_path: Path) -> list[str]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    return [
        "--head-sha",
        "a" * 40,
        "--merge-sha",
        head,
        "--runtime-image",
        "qts-runtime:pr-a",
        "--runtime-image-id",
        "sha256:" + "b" * 64,
        "--ui-image",
        "qts-preview-ui:pr-a",
        "--ui-image-id",
        "sha256:" + "c" * 64,
        "--preview-plan",
        str(tmp_path / "preview-plan.json"),
    ]


def test_plan_separates_pr_head_from_tested_merge_sha(tmp_path):
    argv = _base(tmp_path)
    expected_head = argv[argv.index("--merge-sha") + 1]
    statuses = iter([expected_head, ""])

    def fake_status(*args, **kwargs):
        class Result:
            stdout = next(statuses)

        return Result()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(planner.subprocess, "run", fake_status)
    assert planner.main(argv) == 0
    monkeypatch.undo()
    plan = json.loads((tmp_path / "preview-plan.json").read_text(encoding="utf-8"))
    assert plan["source"]["pr_head_sha"] == "a" * 40
    assert plan["source"]["tested_merge_sha"] == plan["source"]["checkout_sha"]
    assert plan["source"]["pr_head_sha"] != plan["source"]["tested_merge_sha"]
    assert plan["artifacts"]["identity_kind"] == "local_image_id"


def test_registry_digest_cannot_be_used_as_a_local_image_id(tmp_path):
    argv = _base(tmp_path)
    argv[argv.index("qts-runtime:pr-a")] = "repo@sha256:" + "d" * 64
    assert planner.main(argv) == 1
    assert (
        "REGISTRY_DIGEST_NOT_LOCAL_IMAGE_ID"
        in json.loads((tmp_path / "preview-plan.json").read_text(encoding="utf-8"))["errors"]
    )


def test_checkout_mismatch_is_a_failure(tmp_path):
    argv = _base(tmp_path)
    merge_index = argv.index("--merge-sha") + 1
    argv[merge_index] = "xyz"
    statuses = iter(["actual-head-sha", ""])

    def fake_status(*args, **kwargs):
        class Result:
            stdout = next(statuses)

        return Result()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(planner.subprocess, "run", fake_status)
    assert planner.main(argv) == 1
    monkeypatch.undo()
    errors = json.loads((tmp_path / "preview-plan.json").read_text(encoding="utf-8"))["errors"]
    assert "INVALID_MERGE_SHA" in errors
    assert "CHECKOUT_SHA_MISMATCH" in errors
