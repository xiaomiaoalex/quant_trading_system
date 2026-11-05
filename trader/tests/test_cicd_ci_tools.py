"""Regression tests for the CI evidence tools (C2/C3)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest
from scripts import check_coverage, check_junit, release_manifest, verify_test_manifest

ROOT = Path(__file__).resolve().parents[2]


def _junit(tmp_path, *, tests=3, failures=0, errors=0, skipped=0, xfailed=0):
    path = tmp_path / "junit.xml"
    cases = []
    for index in range(tests):
        cases.append(f'<testcase classname="c" name="t{index}" />')
    if skipped:
        cases.append(f'<testcase classname="c" name="skipped"><skipped /></testcase>')
    if xfailed:
        cases.append(
            '<testcase classname="c" name="xfail"><skipped type="pytest.xfail" /></testcase>'
        )
    path.write_text(
        "<testsuites><testsuite "
        f'tests="{tests + skipped + xfailed}" failures="{failures}" '
        f'errors="{errors}" skipped="{skipped + xfailed}">'
        + "".join(cases)
        + "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def test_check_junit_rejects_skips_and_xfails(tmp_path, monkeypatch):
    path = _junit(tmp_path, skipped=1, xfailed=1)
    monkeypatch.setattr(
        sys,
        "argv",
        ["check_junit", str(path), "--require-zero-skipped", "--require-zero-xfailed"],
    )
    assert check_junit.main() == 1


def test_check_junit_accepts_green_evidence(tmp_path, monkeypatch):
    path = _junit(tmp_path, tests=4)
    monkeypatch.setattr(sys, "argv", ["check_junit", str(path), "--min-tests", "4"])
    assert check_junit.main() == 0


def test_check_junit_rejects_zero_tests(tmp_path, monkeypatch):
    path = _junit(tmp_path, tests=0)
    monkeypatch.setattr(sys, "argv", ["check_junit", str(path)])
    assert check_junit.main() == 1


def _coverage_xml(tmp_path, hits):
    lines = "".join(f'<line number="{index}" hits="{hit}" />' for index, hit in enumerate(hits))
    path = tmp_path / "coverage.xml"
    path.write_text(
        "<coverage><packages><package><classes>"
        '<class filename="trading/service.py"><lines>'
        + lines
        + "</lines></class></classes></package></packages></coverage>",
        encoding="utf-8",
    )
    return path


def _thresholds(tmp_path, minimum):
    path = tmp_path / "thresholds.json"
    path.write_text(
        json.dumps(
            {
                "thresholds": [
                    {
                        "path": "trader/trading/service.py",
                        "kind": "file",
                        "min_percent": minimum,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_check_coverage_passes_at_or_above_floor(tmp_path):
    result = check_coverage.evaluate(
        _coverage_xml(tmp_path, [1, 1, 0, 0]), _thresholds(tmp_path, 50)
    )
    assert result["ok"] is True
    assert result["results"][0]["percent"] == 50.0


def test_check_coverage_fails_below_floor(tmp_path):
    result = check_coverage.evaluate(
        _coverage_xml(tmp_path, [1, 0, 0, 0]), _thresholds(tmp_path, 50)
    )
    assert result["ok"] is False
    assert result["failed"] == ["trader/trading/service.py"]


def _release_args(tmp_path):
    ci = tmp_path / "ci.txt"
    runtime = tmp_path / "runtime.txt"
    ci.write_text("pytest==8.3.4\n", encoding="utf-8")
    runtime.write_text("fastapi==1.0\n", encoding="utf-8")
    junit = _junit(tmp_path, tests=2)
    return argparse.Namespace(
        release_id="rel-1",
        commit="c6fe450260eeb59d46c9caee591fb1625510b443",
        ref="refs/heads/main",
        workflow="release-build",
        workflow_run="123",
        image_digest="ghcr.io/example/qts@sha256:" + "a" * 64,
        tree_clean=True,
        schema_version=3,
        environment="demo",
        ci_constraints=str(ci),
        runtime_constraints=str(runtime),
        junit=[str(junit)],
        coverage="",
        frontend="",
        sbom="",
        scan="",
        startup_smoke="",
        allow_missing_evidence=True,
    )


def test_release_manifest_records_real_hashes(tmp_path):
    manifest = release_manifest.build_manifest(_release_args(tmp_path))
    assert manifest["commit"] == "c6fe450260eeb59d46c9caee591fb1625510b443"
    assert manifest["image_digest"] == "sha256:" + "a" * 64
    dependency = manifest["dependencies"]["ci_constraints"]
    assert dependency["present"] is True
    assert len(dependency["sha256"]) == 64
    assert manifest["evidence"]["junit"][0]["present"] is True


def test_release_manifest_rejects_mutable_or_wrong_references(tmp_path):
    args = _release_args(tmp_path)
    args.image_digest = "ghcr.io/example/qts:latest"
    with pytest.raises(SystemExit):
        release_manifest.build_manifest(args)
    args = _release_args(tmp_path)
    args.commit = "short"
    with pytest.raises(SystemExit):
        release_manifest.build_manifest(args)


def test_repository_manifest_and_workflows_are_consistent():
    assert verify_test_manifest.main([]) == 0


def test_unregistered_ci_job_is_rejected(tmp_path):
    workflow = tmp_path / "ci-gate.yml"
    workflow.write_text(
        "\n".join(
            [
                "jobs:",
                "  p0-gate:",
                "    env:",
                "      QTS_TEST_GROUP: p0",
                "  rogue-job:",
                "    runs-on: ubuntu-latest",
                "  ci-required:",
                "    needs:",
                "      - p0-gate",
                "",
            ]
        ),
        encoding="utf-8",
    )
    manifest = {
        "ci_jobs": {"p0-gate": {"group": "p0", "required": True}},
        "ci_required_non_test_jobs": [],
    }
    report = verify_test_manifest.Report()
    verify_test_manifest._check_workflows(manifest, (workflow,), report)
    assert "CI_JOB_UNREGISTERED: rogue-job" in report.errors


def test_ci_required_gate_must_depend_on_every_required_job(tmp_path):
    workflow = tmp_path / "ci-gate.yml"
    workflow.write_text(
        "\n".join(
            [
                "jobs:",
                "  p0-gate:",
                "    env:",
                "      QTS_TEST_GROUP: p0",
                "  ci-required:",
                "    needs:",
                "      - other-gate",
                "",
            ]
        ),
        encoding="utf-8",
    )
    manifest = {
        "ci_jobs": {"p0-gate": {"group": "p0", "required": True}},
        "ci_required_non_test_jobs": ["manifest-gate"],
    }
    report = verify_test_manifest.Report()
    verify_test_manifest._check_workflows(manifest, (workflow,), report)
    assert "CI_REQUIRED_GATE_MISSING_NEED: p0-gate" in report.errors
    assert "CI_REQUIRED_GATE_MISSING_NEED: manifest-gate" in report.errors
    assert "CI_REQUIRED_GATE_OPTIONAL_NEED: other-gate" in report.errors


def test_release_manifest_requires_all_evidence_and_startup_smoke(tmp_path):
    args = _release_args(tmp_path)
    args.allow_missing_evidence = False
    coverage = tmp_path / "coverage.xml"
    frontend = tmp_path / "dist.txt"
    sbom = tmp_path / "sbom.json"
    scan = tmp_path / "scan.sarif"
    smoke = tmp_path / "compose-smoke.json"
    for path in (coverage, frontend, sbom, scan):
        path.write_text("{}", encoding="utf-8")
    smoke.write_text(
        json.dumps(
            {
                "passed": True,
                "image": "ghcr.io/example/qts@sha256:" + "a" * 64,
                "image_digest_ok": True,
                "health": {"control": "healthy"},
            }
        ),
        encoding="utf-8",
    )
    args.coverage = str(coverage)
    args.frontend = str(frontend)
    args.sbom = str(sbom)
    args.scan = str(scan)
    args.startup_smoke = str(smoke)
    manifest = release_manifest.build_manifest(args)
    assert manifest["evidence"]["startup_smoke"]["present"] is True
    assert manifest["startup_smoke_summary"]["passed"] is True

    missing = _release_args(tmp_path)
    missing.allow_missing_evidence = False
    missing.coverage = str(coverage)
    missing.frontend = str(frontend)
    missing.sbom = str(sbom)
    missing.scan = str(scan)
    missing.startup_smoke = ""
    with pytest.raises(SystemExit, match="RELEASE_EVIDENCE_MISSING"):
        release_manifest.build_manifest(missing)

    failed = _release_args(tmp_path)
    failed.allow_missing_evidence = False
    failed.coverage = str(coverage)
    failed.frontend = str(frontend)
    failed.sbom = str(sbom)
    failed.scan = str(scan)
    smoke.write_text(
        json.dumps({"passed": False, "image": "x", "image_digest_ok": True}),
        encoding="utf-8",
    )
    failed.startup_smoke = str(smoke)
    with pytest.raises(SystemExit, match="RELEASE_STARTUP_SMOKE_NOT_PASSED"):
        release_manifest.build_manifest(failed)
