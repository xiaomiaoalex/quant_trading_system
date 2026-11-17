"""Release image scan gate: the gate must stay strict and stay diagnosable.

The release workflow fails a build when the built image contains fixable
CRITICAL/HIGH vulnerabilities.  Two properties are asserted here:

* the gate is not weakened (severity, exit code, ignore-unfixed and the
  absence of any ignore file are all pinned), and
* a failing scan still leaves machine-readable and human-readable evidence
  behind, so the reason for a failure can be audited without re-running it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release-build.yml"
DOCKERFILE = ROOT / "Dockerfile"
REAL_SARIF_SAMPLE = (
    ROOT
    / "artifacts"
    / "branch-preview-20261112"
    / "engineer-scan-20261115"
    / "trivy-preview.sarif"
)
sys.path.insert(0, str(ROOT))

from scripts import summarize_trivy_sarif  # noqa: E402

# Advisory floors used by the pinned dependencies (see the DEVELOPMENT_LOG
# entry for Release 43727736396).  A version below these floors leaves known
# fixable HIGH findings in the released image.
AIOHTTP_FIXED_IN = (3, 14, 4)
PIP_FIXED_IN = (26, 2, 0)


def _release_workflow_text() -> str:
    return RELEASE_WORKFLOW.read_text(encoding="utf-8")


def _step_block(text: str, step_name: str) -> str:
    """Return the YAML body of the step whose ``name:`` matches ``step_name``."""
    marker = f"- name: {step_name}"
    assert marker in text, f"step not found: {step_name}"
    start = text.index(marker)
    following = text.find("\n      - name:", start + 1)
    return text[start:] if following == -1 else text[start:following]


def _workflow_step(step_name: str) -> dict:
    """Return a workflow step by name, parsed from YAML."""
    workflow = yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            if step.get("name") == step_name:
                return step
    raise AssertionError(f"workflow step not found: {step_name}")


def _step_runs_in_actions(condition: str, *, gate_failed: bool, gate_outcome: str) -> bool:
    """Model GitHub Actions' status-check semantics for a step-level ``if``.

    A step-level ``if`` that names no status function receives the implicit
    ``success()``.  After a failed step the job status is ``failure``, so such
    a step is skipped - which is exactly the situation in which the scan
    report has to be printed.
    """
    terms = [term.strip() for term in condition.split("&&")]
    status_functions = {"always()", "failure()", "success()", "cancelled()"}
    if not any(term in status_functions for term in terms):
        # Actions injects the implicit success() when no status function is named.
        terms.append("success()")
    result = True
    for term in terms:
        if term == "always()":
            value = True
        elif term == "failure()":
            value = gate_failed
        elif term == "success()":
            value = not gate_failed
        elif term == "cancelled()":
            value = False
        elif term.startswith("steps.scan-gate.outcome"):
            value = term.split("==", 1)[1].strip().strip("'\"") == gate_outcome
        else:
            raise AssertionError(f"unsupported condition term: {term}")
        result = result and value
    return result


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    return tuple(int(part) for part in parts[:3])


def test_release_gate_keeps_its_strict_parameters():
    block = _step_block(_release_workflow_text(), "Fail on critical or high image vulnerabilities")
    assert "severity: CRITICAL,HIGH" in block
    assert 'exit-code: "1"' in block
    assert "ignore-unfixed: true" in block
    # No ignore file may be wired into the gate.
    assert "trivyignores" not in block
    # Only the repository itself is scanned: a gitignored local artefact
    # directory must never decide whether this gate is strict enough.
    assert not list(ROOT.glob(".trivyignore*"))
    assert not list((ROOT / ".github").rglob(".trivyignore*"))


def test_scan_evidence_is_uploaded_even_when_the_gate_fails():
    text = _release_workflow_text()
    block = _step_block(text, "Upload scan evidence")
    assert "if: always()" in block
    assert "reports/trivy.sarif" in block
    assert "reports/sbom.cdx.json" in block
    assert "if-no-files-found: warn" in block


def test_diagnosis_step_runs_after_a_failed_gate():
    gate = _workflow_step("Fail on critical or high image vulnerabilities")
    assert gate.get("id") == "scan-gate"
    assert "if" not in gate

    details = _workflow_step("Show image vulnerability details when the gate fails")
    condition = details.get("if")
    assert condition, "the diagnosis step has no condition"
    # The condition must still key on the gate's own outcome.
    assert "steps.scan-gate.outcome" in condition
    # It must also survive the job status after that failure.
    assert _step_runs_in_actions(condition, gate_failed=True, gate_outcome="failure")
    # Control: a condition that only inspects the step outcome would be
    # skipped by the implicit success(), i.e. exactly when it is needed.
    assert not _step_runs_in_actions(
        "steps.scan-gate.outcome == 'failure'",
        gate_failed=True,
        gate_outcome="failure",
    )
    # A passing gate must not print a finding list.
    assert not _step_runs_in_actions(condition, gate_failed=False, gate_outcome="success")

    assert "scripts/summarize_trivy_sarif.py reports/trivy.sarif" in details["run"]
    # The detail step must never turn a failure into a pass.
    assert details.get("continue-on-error") is True


def test_dockerfile_pins_both_base_images_by_digest():
    text = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in text.splitlines() if line.startswith("FROM ")]
    assert from_lines, "Dockerfile declares no base image"
    for line in from_lines:
        assert re.search(r"@sha256:[0-9a-f]{64}(\s+AS\s+\S+)?$", line.strip()), line


def test_dockerfile_applies_the_zlib_security_update():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "--only-upgrade" in text
    assert "zlib1g" in text
    # The targeted upgrade must not turn into a blanket "upgrade everything".
    assert "apt-get upgrade" not in text


def test_dockerfile_pins_a_patched_pip():
    text = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r'"pip==([0-9.]+)"', text)
    assert match, "pip is not pinned in the Dockerfile"
    assert _version_tuple(match.group(1)) >= PIP_FIXED_IN
    # The runtime image must not ship a package manager: pip 26.x carries a
    # vendored CycloneDX SBOM that Trivy reads as an installed setuptools
    # 70.3.0 (CVE-2025-47273, HIGH).
    assert "pip uninstall -y pip" in text


def test_aiohttp_is_pinned_to_the_advisory_fixed_version_everywhere():
    pinned = {
        "trader/requirements-runtime.txt": _aiohttp_pin(
            ROOT / "trader" / "requirements-runtime.txt"
        ),
        "trader/requirements-ci.txt": _aiohttp_pin(ROOT / "trader" / "requirements-ci.txt"),
        "trader/requirements-runtime.constraints.txt": _aiohttp_pin(
            ROOT / "trader" / "requirements-runtime.constraints.txt"
        ),
        "trader/requirements-ci.constraints.txt": _aiohttp_pin(
            ROOT / "trader" / "requirements-ci.constraints.txt"
        ),
        "trader/requirements-ci.windows.constraints.txt": _aiohttp_pin(
            ROOT / "trader" / "requirements-ci.windows.constraints.txt"
        ),
    }
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'"aiohttp==([0-9.]+)"', pyproject)
    assert match, "pyproject.toml does not pin aiohttp"
    pinned["pyproject.toml"] = match.group(1)

    versions = set(pinned.values())
    assert len(versions) == 1, f"aiohttp is pinned inconsistently: {pinned}"
    version = versions.pop()
    assert _version_tuple(version) >= AIOHTTP_FIXED_IN


def _aiohttp_pin(path: Path) -> str:
    match = re.search(
        r"^aiohttp==([0-9.]+)",
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, f"aiohttp is not pinned in {path.name}"
    return match.group(1)


def _sarif_result(rule_id: str, severity: str, package: str, fixed: str) -> dict:
    message = (
        f"Package: {package}\n"
        "Installed Version: 1.0\n"
        f"Vulnerability {rule_id}\n"
        f"Severity: {severity}\n"
        f"Fixed Version: {fixed}\n"
        f"Link: [{rule_id}](https://avd.aquasec.com/nvd/{rule_id})"
    )
    return {
        "ruleId": rule_id,
        "level": "error" if severity in {"CRITICAL", "HIGH"} else "warning",
        "message": {"text": message},
        "locations": [{"message": {"text": f"image: {package}@1.0"}}],
    }


def test_sarif_summary_lists_findings_in_severity_order():
    payload = {
        "runs": [
            {
                "results": [
                    _sarif_result("CVE-0000-2", "MEDIUM", "pip", "26.1"),
                    _sarif_result("CVE-0000-1", "CRITICAL", "aiohttp", "3.14.4"),
                    _sarif_result("CVE-0000-3", "HIGH", "apt", "3.0.4"),
                ]
            }
        ]
    }
    findings = summarize_trivy_sarif.parse_findings(payload)
    assert [item["severity"] for item in summarize_trivy_sarif.sort_findings(findings)] == [
        "CRITICAL",
        "HIGH",
        "MEDIUM",
    ]
    rendered = summarize_trivy_sarif.render(findings)
    assert "CVE-0000-1" in rendered
    assert "aiohttp" in rendered
    assert "Total findings: 3" in rendered


def test_sarif_summary_exit_code_tracks_the_threshold(tmp_path):
    report = tmp_path / "trivy.sarif"
    report.write_text(
        json.dumps({"runs": [{"results": [_sarif_result("CVE-0000-1", "HIGH", "apt", "3.0.4")]}]}),
        encoding="utf-8",
    )
    assert summarize_trivy_sarif.main([str(report)]) == 1
    assert summarize_trivy_sarif.main([str(report), "--fail-on", "CRITICAL"]) == 0
    assert summarize_trivy_sarif.main([str(tmp_path / "missing.json")]) == 2


MALFORMED_SARIF_PAYLOADS = {
    "empty_object": {},
    "null_runs": {"runs": None},
    "runs_not_an_array": {"runs": "invalid"},
    "run_without_results": {"runs": [{}]},
    "results_not_an_array": {"runs": [{"results": "invalid"}]},
    "result_not_an_object": {"runs": [{"results": [None]}]},
    "message_not_an_object": {"runs": [{"results": [{"message": "invalid"}]}]},
    "locations_not_an_array": {"runs": [{"results": [{"locations": "invalid"}]}]},
    "top_level_array": [],
    "top_level_string": "invalid",
    "top_level_null": None,
}


@pytest.mark.parametrize(
    "payload", MALFORMED_SARIF_PAYLOADS.values(), ids=list(MALFORMED_SARIF_PAYLOADS)
)
def test_summary_fails_closed_on_malformed_reports(tmp_path, capsys, payload):
    report = tmp_path / "malformed.sarif"
    report.write_text(json.dumps(payload), encoding="utf-8")
    assert summarize_trivy_sarif.main([str(report)]) == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "No findings" not in captured.out


def test_summary_fails_closed_on_invalid_utf8(tmp_path, capsys):
    report = tmp_path / "invalid-utf8.sarif"
    report.write_bytes(b'{"runs": [{"results": []}]}\xff\xfe')
    assert summarize_trivy_sarif.main([str(report)]) == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "No findings" not in captured.out


def test_summary_accepts_a_valid_report_without_findings(tmp_path):
    report = tmp_path / "empty.sarif"
    report.write_text(json.dumps({"runs": [{"results": []}]}), encoding="utf-8")
    assert summarize_trivy_sarif.main([str(report)]) == 0


def test_summary_still_reproduces_the_real_failing_scan():
    if not REAL_SARIF_SAMPLE.is_file():
        return
    payload = json.loads(REAL_SARIF_SAMPLE.read_text(encoding="utf-8"))
    findings = summarize_trivy_sarif.parse_findings(payload)
    assert len(findings) == 52
    assert summarize_trivy_sarif.main([str(REAL_SARIF_SAMPLE)]) == 1
