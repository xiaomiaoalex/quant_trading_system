"""Contract tests for the cross-platform CI check entry point.

The runner must never turn a failed check, an unavailable platform, a timeout,
or missing evidence into a green result.  The tests use a fake checker only to
control exit codes; the real checker semantics stay in the existing scripts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import run_ci_checks  # noqa: E402


def test_scopes_cover_the_existing_ci_groups_without_new_selection_logic():
    assert run_ci_checks.SCOPES["p0"]["group"] == "p0"
    assert run_ci_checks.SCOPES["offline"]["group"] == "p0,offline"
    assert run_ci_checks.SCOPES["loopback"]["network"] == "loopback"
    assert run_ci_checks.SCOPES["postgres"]["network"] == "loopback"
    assert run_ci_checks.SCOPES["research-ai"]["subset"] == "research_ai"
    assert run_ci_checks.SCOPES["windows-guard"]["platforms"] == ("windows",)
    assert run_ci_checks.SCOPES["linux-docker"]["platforms"] == ("linux",)


def test_reports_dir_must_stay_inside_the_workspace():
    with pytest.raises(ValueError):
        run_ci_checks._resolve_workspace(Path("..") / "outside-reports")


def test_unsupported_platform_checks_are_required_failures():
    record = {
        "checks": [{"argv": ["require-platform:linux"], "exit_code": 2}],
        "evidence": {},
    }
    path = (
        ROOT
        / "artifacts"
        / "cicd"
        / "cross-platform-20261118"
        / "reports"
        / "platform-contract.json"
    )
    assert run_ci_checks._finalize(record, path) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["ok"] is False


def test_check_record_is_fail_closed_on_nonzero_exit(tmp_path):
    record = {
        "checks": [{"argv": ["fake", "check"], "exit_code": 1, "stderr": "boom"}],
        "evidence": {},
    }
    path = tmp_path / "record.json"
    assert run_ci_checks._finalize(record, path) == 1
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["ok"] is False
    assert any("CHECK_FAILED" in item for item in saved["failures"])


def test_missing_evidence_is_a_failure_even_when_checks_exit_zero(tmp_path):
    record = {
        "checks": [{"argv": ["fake", "check"], "exit_code": 0}],
        "evidence": {"reports/junit-offline.xml": {"present": False, "required": True}},
    }
    path = tmp_path / "record.json"
    assert run_ci_checks._finalize(record, path) == 1
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert any("EVIDENCE_MISSING" in item for item in saved["failures"])


def test_timeout_is_recorded_with_exit_code_124(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise __import__("subprocess").TimeoutExpired(["fake"], 0.1, output="partial")

    monkeypatch.setattr(run_ci_checks.subprocess, "run", raise_timeout)
    record: dict[str, object] = {"checks": []}
    run_ci_checks._run(
        ["fake", "check"],
        env={},
        cwd=ROOT,
        timeout=0.1,
        record=record,
    )
    check = record["checks"][0]
    assert check["exit_code"] == 124
    assert check["timed_out"] is True


def test_unsupported_platform_is_not_a_success(monkeypatch):
    monkeypatch.setattr(run_ci_checks, "_platform", lambda: "windows")
    reports = ROOT / "artifacts/cicd/cross-platform-20261118/reports/platform-contract"
    code = run_ci_checks.main(["--scope", "linux-docker", "--reports-dir", str(reports)])
    assert code == 1
    record = json.loads((reports / "run-ci-checks-linux-docker.json").read_text(encoding="utf-8"))
    assert record["platform"] == "windows"
    assert record["ok"] is False
    assert record["checks"][0]["exit_code"] == 2
    assert "CI_CHECK_PLATFORM_UNSUPPORTED" in record["checks"][0]["stderr"]


def test_unsupported_platform_checks_are_required_failures():
    record = {
        "checks": [{"argv": ["require-platform:linux"], "exit_code": 2}],
        "evidence": {},
    }
    path = ROOT / "artifacts/cicd/cross-platform-20261118/reports/platform-contract-record.json"
    assert run_ci_checks._finalize(record, path) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["ok"] is False


def test_manifest_scope_runs_the_real_checker():
    reports = ROOT / "artifacts/cicd/cross-platform-20261118/reports/contract-manifest"
    code = run_ci_checks.main(
        ["--scope", "manifest", "--reports-dir", str(reports), "--python", sys.executable]
    )
    assert code == 0
    record = json.loads((reports / "run-ci-checks-manifest.json").read_text(encoding="utf-8"))
    assert record["ok"] is True
    assert record["checks"][0]["exit_code"] == 0
    assert record["source"]["head"]


def test_required_junit_evidence_is_declared_for_all_test_scopes():
    for scope in ("p0", "offline", "loopback", "postgres", "research-ai", "windows-guard"):
        assert run_ci_checks.SCOPES[scope]["junit"].endswith(".xml")
        assert "junit" in run_ci_checks.SCOPES[scope]["checks"]
    assert "coverage" in run_ci_checks.SCOPES["offline"]["checks"]


def test_unsupported_platform_scope_has_no_silent_success():
    for scope, spec in run_ci_checks.SCOPES.items():
        assert spec["platforms"], scope
        assert spec["checks"], scope


def test_default_python_executable_is_resolved():
    assert run_ci_checks._resolve_executable("python") == "python"
    assert run_ci_checks._resolve_executable(str(Path(sys.executable))) == str(Path(sys.executable))
    with pytest.raises(ValueError):
        run_ci_checks._resolve_executable("../outside/python")


@pytest.mark.parametrize("scope", ["p0", "offline", "loopback", "postgres"])
def test_general_scopes_are_allowed_on_both_development_platforms(scope):
    assert set(run_ci_checks.SCOPES[scope]["platforms"]) == {"linux", "windows"}
