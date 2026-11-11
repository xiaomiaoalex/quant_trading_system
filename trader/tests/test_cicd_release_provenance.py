"""Fail-closed release provenance gate (parent F-P5)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.verify_release_provenance import evaluate

COMMIT = "c6fe450260eeb59d46c9caee591fb1625510b443"
DIGEST = "sha256:" + "a" * 64
RELEASE_ID = "rel-1"
REPOSITORY = "xiaomiaoalex/quant_trading_system"


def _run(**overrides):
    run = {
        "id": 12345,
        "repository": {"full_name": REPOSITORY},
        "path": ".github/workflows/release-build.yml",
        "event": "workflow_run",
        "head_branch": "main",
        "status": "completed",
        "conclusion": "success",
        "head_sha": COMMIT,
    }
    run.update(overrides)
    return run


def _manifest(**overrides):
    manifest = {
        "release_id": RELEASE_ID,
        "commit": COMMIT,
        "image_digest": DIGEST,
        "environment": "demo",
    }
    manifest.update(overrides)
    return manifest


def _errors(run=None, manifest=None):
    return evaluate(
        run=run or _run(),
        manifest=manifest or _manifest(),
        repository=REPOSITORY,
        run_id="12345",
        commit=COMMIT,
        digest=DIGEST,
        release_id=RELEASE_ID,
    )


def test_matching_trusted_main_release_is_accepted():
    assert _errors() == []


def test_non_main_or_unsuccessful_run_is_rejected():
    errors = _errors(run=_run(head_branch="feature/x", conclusion="failure", status="in_progress"))
    assert "PROVENANCE_BRANCH_MISMATCH" in errors
    assert "PROVENANCE_RUN_NOT_COMPLETED" in errors
    assert "PROVENANCE_RUN_NOT_SUCCESSFUL" in errors


def test_non_workflow_run_or_wrong_workflow_is_rejected():
    errors = _errors(run=_run(event="push", path=".github/workflows/other.yml"))
    assert "PROVENANCE_EVENT_MISMATCH" in errors
    assert "PROVENANCE_WORKFLOW_MISMATCH" in errors


def test_head_sha_and_manifest_mismatches_are_rejected():
    errors = _errors(
        run=_run(head_sha="0" * 40),
        manifest=_manifest(
            commit="0" * 40,
            image_digest="sha256:" + "b" * 64,
            release_id="other",
            environment="testnet",
        ),
    )
    assert "PROVENANCE_HEAD_SHA_MISMATCH" in errors
    assert "MANIFEST_COMMIT_MISMATCH" in errors
    assert "MANIFEST_DIGEST_MISMATCH" in errors
    assert "MANIFEST_RELEASE_ID_MISMATCH" in errors
    assert "MANIFEST_ENVIRONMENT_NOT_DEMO" in errors


def test_invalid_expected_commit_or_digest_is_rejected():
    errors = evaluate(
        run=_run(),
        manifest=_manifest(),
        repository=REPOSITORY,
        run_id="12345",
        commit="short",
        digest="latest",
        release_id=RELEASE_ID,
    )
    assert "EXPECTED_COMMIT_INVALID" in errors
    assert "EXPECTED_DIGEST_INVALID" in errors


def test_release_evidence_is_uploaded_only_for_published_main_builds():
    text = Path(".github/workflows/release-build.yml").read_text(encoding="utf-8")
    upload = text.split("- name: Upload release evidence", 1)[1]
    assert "if: env.PUBLISH == 'true'" in upload
    smoke = text.split("- name: Run fixed-digest isolated Compose startup smoke", 1)[1]
    assert "${IMAGE_NAME}@${{ steps.build.outputs.digest }}" in smoke
    assert "--require-digest" in smoke
