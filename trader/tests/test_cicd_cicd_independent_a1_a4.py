"""Independent contract tests for the new cross-platform CI/CD phase.

These checks derive expectations from the parent work order A1-A4 rather than
from the implementation's own tests.  They intentionally include negative
cases for fail-closed behavior, PR preview isolation and evidence preservation.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import prepare_pr_preview, preview_browser_smoke, run_ci_checks  # noqa: E402
from scripts import validate_preview_stack as stack_gate  # noqa: E402


def _artifacts(name: str) -> Path:
    path = ROOT / "artifacts" / "cicd" / "cross-platform-20261118" / f"tester-{name}"
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------- #
# A1: unified runner
# --------------------------------------------------------------------------- #


def test_a1_failed_checker_is_recorded_with_raw_output_and_nonzero_exit():
    workdir = _artifacts("a1-failed-checker")
    fake_python = workdir / "fake_python.py"
    fake_python.write_text(
        "import sys\nsys.stdout.write('FAKE_STDOUT\\n')\n"
        "sys.stderr.write('FAKE_STDERR\\n')\nraise SystemExit(7)\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/run_ci_checks.py"),
            "--scope",
            "manifest",
            "--python",
            str(fake_python),
            "--reports-dir",
            str(workdir / "reports"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    record = json.loads(
        (workdir / "reports/run-ci-checks-manifest.json").read_text(encoding="utf-8")
    )
    assert record["ok"] is False
    assert record["checks"][0]["exit_code"] == 7
    assert "FAKE_STDOUT" in record["checks"][0]["stdout"]
    assert "FAKE_STDERR" in record["checks"][0]["stderr"]
    assert any("CHECK_FAILED" in item for item in record["failures"])
    assert record["started_at"] and record["ended_at"]
    assert record["platform"] in {"linux", "windows"}
    assert record["source"]["head"]


def test_a1_unsupported_platform_is_not_a_success(monkeypatch):
    monkeypatch.setattr(run_ci_checks, "_platform", lambda: "windows")
    reports = _artifacts("a1-unsupported-platform")
    code = run_ci_checks.main(["--scope", "linux-docker", "--reports-dir", str(reports)])
    assert code == 1
    record = json.loads((reports / "run-ci-checks-linux-docker.json").read_text(encoding="utf-8"))
    assert record["ok"] is False
    assert record["checks"][0]["exit_code"] == 2
    assert "CI_CHECK_PLATFORM_UNSUPPORTED" in record["checks"][0]["stderr"]


def test_a1_missing_required_junit_is_a_failure(tmp_path):
    record = {
        "checks": [{"argv": ["pytest"], "exit_code": 0}],
        "evidence": {"reports/junit-offline.xml": {"present": False, "required": True}},
    }
    output = _artifacts("a1-missing-evidence") / "record.json"
    assert run_ci_checks._finalize(record, output) == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert any("EVIDENCE_MISSING" in failure for failure in payload["failures"])


def test_a1_reports_directory_cannot_escape_the_workspace():
    with pytest.raises(ValueError):
        run_ci_checks._resolve_workspace(Path("..") / "outside-reports")


# --------------------------------------------------------------------------- #
# A2: PR image and browser smoke
# --------------------------------------------------------------------------- #


def test_a2_ci_required_includes_the_pr_preview_gate():
    workflow = (ROOT / ".github/workflows/ci-gate.yml").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "ci/test_manifest.json").read_text(encoding="utf-8"))
    assert re.search(r"^  pr-preview-smoke-gate:\s*$", workflow, re.MULTILINE)
    assert "pr-preview-smoke-gate" in manifest["ci_required_non_test_jobs"]
    needs_block = workflow.split("  ci-required:", 1)[1]
    assert "- pr-preview-smoke-gate" in needs_block


def test_a2_pr_preview_project_is_unique_per_run():
    workflow = (ROOT / ".github/workflows/ci-gate.yml").read_text(encoding="utf-8")
    match = re.search(r"QTS_PR_PREVIEW_PROJECT:\s*(.+)", workflow)
    assert match, "PR preview project must be explicitly configured"
    value = match.group(1).strip()
    assert "run_id" in value or "run_attempt" in value or "github.run" in value, (
        "PR preview project must include a run-unique component; "
        f"got {value!r}, which reuses Compose volumes across repeated runs"
    )


def test_a2_cleanup_removes_only_task_owned_volumes():
    workflow = (ROOT / ".github/workflows/ci-gate.yml").read_text(encoding="utf-8")
    cleanup = workflow.split("Remove only this PR's Compose resources", 1)[1]
    cleanup = cleanup.split("  ci-required:", 1)[0]
    assert re.search(r"down\s+.*-v", cleanup), (
        "PR preview cleanup must remove only the task-owned project's volumes; "
        "otherwise repeated runs reuse stale database state"
    )


def test_a2_browser_failure_writes_evidence_without_traceback(tmp_path, monkeypatch):
    password = tmp_path / "password.txt"
    password.write_text("synthetic-password", encoding="utf-8")
    evidence = _artifacts("a2-browser-failure") / "browser.json"
    monkeypatch.setattr(preview_browser_smoke, "browser_available", lambda: (True, "ok"))

    def raise_launch(**kwargs):
        raise RuntimeError("BROWSER_LAUNCH_FAILED")

    monkeypatch.setattr(preview_browser_smoke, "_run_browser_checks", raise_launch)
    code = preview_browser_smoke.main(
        [
            "--origin",
            "http://127.0.0.1:18080",
            "--password-file",
            str(password),
            "--evidence",
            str(evidence),
        ]
    )
    assert code == 1
    assert evidence.is_file(), "a browser runtime failure must still save evidence"
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert any("BROWSER" in problem for problem in payload["problems"])
    assert "synthetic-password" not in evidence.read_text(encoding="utf-8")


def test_a2_prepare_script_does_not_expose_a_dirty_checkout_bypass():
    source = (ROOT / "scripts/prepare_pr_preview.py").read_text(encoding="utf-8")
    assert "getenv" not in source and "environ" not in source, (
        "a production preflight must not read environment-controlled test bypasses"
    )
    assert "DEVNULL" not in source
    assert '"git", "status", "--porcelain"' in source


# --------------------------------------------------------------------------- #
# A3: pre-compose configuration gate
# --------------------------------------------------------------------------- #


def _stack_values() -> dict[str, str]:
    return {
        "QTS_PREVIEW_COMPOSE_PROJECT": "qts_independent",
        "QTS_PREVIEW_ID": "independent",
        "QTS_PREVIEW_COMMIT": "a" * 40,
        "QTS_PREVIEW_ORIGIN": "http://127.0.0.1:18080",
        "QTS_PREVIEW_HTTP_PORT": "18080",
        "QTS_PREVIEW_RUNTIME_IMAGE": "qts-runtime:independent",
        "QTS_PREVIEW_RUNTIME_IMAGE_ID": "sha256:" + "b" * 64,
        "QTS_PREVIEW_UI_IMAGE": "qts-preview-ui:independent",
        "QTS_PREVIEW_POSTGRES_PASSWORD": "pg_independent_1234567890",
        "QTS_PREVIEW_PASSWORD_HASH": "scrypt$" + "c" * 32 + "$" + "d" * 128,
    }


def test_a3_invalid_shell_value_stops_before_env_file_is_written(tmp_path, monkeypatch):
    for key, value in _stack_values().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("QTS_PREVIEW_ORIGIN", "http://example.com")
    report = tmp_path / "preview-stack.json"
    env_out = tmp_path / "preview.env"
    code = stack_gate.main(
        ["--compose-file", "compose.yml", "--env-out", str(env_out), "--json-out", str(report)]
    )
    assert code == 1
    assert "INVALID_LOOPBACK_ORIGIN" in json.loads(report.read_text(encoding="utf-8"))["problems"]
    assert not env_out.exists()


def test_a3_rendered_report_is_redacted():
    values = _stack_values()
    rendered = {
        "services": {
            "control": {
                "image": values["QTS_PREVIEW_RUNTIME_IMAGE"],
                "ports": [],
                "labels": {
                    "com.qts.preview.runtime_image_id": values["QTS_PREVIEW_RUNTIME_IMAGE_ID"],
                    "com.qts.preview.commit": values["QTS_PREVIEW_COMMIT"],
                },
            }
        },
        "secrets": {},
    }
    problems, summary = stack_gate.evaluate_rendered(rendered, values, {})
    assert problems == []
    assert values["QTS_PREVIEW_PASSWORD_HASH"] not in json.dumps(summary)
    assert "POSTGRES_PASSWORD" not in json.dumps(summary)


def test_a3_generated_env_single_quotes_dollar_values_for_compose(tmp_path):
    values = _stack_values()
    password_hash = values["QTS_PREVIEW_PASSWORD_HASH"]
    env_file = tmp_path / "preview.env"
    stack_gate.write_env(env_file, values)
    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert f"QTS_PREVIEW_PASSWORD_HASH='{password_hash}'" in lines
    assert not any(line.startswith("QTS_PREVIEW_PASSWORD_HASH=scrypt$") for line in lines)


def test_a3_render_rejects_non_loopback_ports_and_venue_secrets():
    values = _stack_values()
    rendered = {
        "services": {
            "gateway": {
                "image": values["QTS_PREVIEW_UI_IMAGE"],
                "ports": [{"host_ip": "0.0.0.0", "published": "18080"}],
                "labels": {},
            }
        },
        "secrets": {"binance_api_key": {}},
    }
    problems, _ = stack_gate.evaluate_rendered(rendered, values, {})
    assert "NON_LOOPBACK_PORT:gateway" in problems
    assert "VENUE_SECRETS_PRESENT" in problems


# --------------------------------------------------------------------------- #
# A4: platform conventions and documentation
# --------------------------------------------------------------------------- #


def test_a4_gitattributes_requires_lf_for_shell_entrypoints():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert re.search(r"\*\.sh\s+text\s+eol=lf", text)


def test_a4_runbook_documents_stage_ownership_and_no_rebuild():
    text = (ROOT / "docs/CI_CD_RELEASE_RUNBOOK.md").read_text(encoding="utf-8")
    for phrase in (
        "scripts/run_ci_checks.py",
        "pr-preview-smoke-gate",
        "不得在预览主机重建",
        "schema 向前回滚不能替代容器回滚",
    ):
        assert phrase in text, phrase
