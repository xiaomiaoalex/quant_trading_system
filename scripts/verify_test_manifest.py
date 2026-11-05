"""Verify the complete, auditable classification of ``trader/tests/test_*.py``.

The CI gate must never silently shrink its test selection.  ``ci/test_manifest.json``
is the single source of truth: every current test file belongs to exactly one
group (``p0``, ``offline``, ``postgres``, ``ops`` or ``external``) and every
required CI job selects its group through ``--qts-group``.

This checker is dependency-free (stdlib only) so it can run before pip install.
It fails on:

* a test file that is not classified (new file without manifest update);
* a manifest entry that no longer exists (stale path);
* a file classified twice (conflicting ownership);
* an empty group or a missing group;
* a required group with no CI job, or a CI job that no longer selects it;
* raw ``trader/tests/test_*.py`` file lists or ``-k`` expressions in the
  workflow, which bypass the manifest and can silently narrow coverage.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "ci" / "test_manifest.json"
DEFAULT_WORKFLOWS = (
    ROOT / ".github" / "workflows" / "ci-gate.yml",
    ROOT / ".github" / "workflows" / "optional-gates.yml",
    ROOT / ".github" / "workflows" / "release-build.yml",
    ROOT / ".github" / "workflows" / "deploy-demo.yml",
    ROOT / ".github" / "workflows" / "demo-evidence.yml",
)
REQUIRED_GROUPS = ("p0", "offline", "postgres", "ops", "external")
PYTEST_LINE = re.compile(r"(?:python\s+-m\s+)?pytest\b")
RAW_TEST_FILE = re.compile(r"trader/tests/test_[A-Za-z0-9_*.-]+\.py")
GROUP_ENV = re.compile(r"QTS_TEST_GROUP\s*[:=]\s*['\"]?([A-Za-z0-9_,-]+)")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    @property
    def ok(self) -> bool:
        return not self.errors


def _load_manifest(path: Path, report: Report) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        report.error(f"TEST_MANIFEST_MISSING: {path}")
        return {}
    except json.JSONDecodeError as exc:
        report.error(f"TEST_MANIFEST_INVALID_JSON: {path}: {exc}")
        return {}
    if not isinstance(payload, dict):
        report.error("TEST_MANIFEST_INVALID: top-level value must be an object")
        return {}
    return payload


def _validate_manifest(
    manifest: dict[str, Any], root: Path, report: Report
) -> dict[str, list[str]]:
    groups = manifest.get("groups")
    if not isinstance(groups, dict):
        report.error("TEST_MANIFEST_INVALID: 'groups' must be an object")
        return {}

    missing_groups = [group for group in REQUIRED_GROUPS if group not in groups]
    for group in missing_groups:
        report.error(f"TEST_GROUP_MISSING: {group}")
    extra_groups = sorted(set(groups) - set(REQUIRED_GROUPS))
    for group in extra_groups:
        report.warn(f"TEST_GROUP_UNKNOWN: {group} (not part of the documented taxonomy)")

    discovered = sorted(path.name for path in (root / "trader" / "tests").glob("test_*.py"))
    discovered_set = set(discovered)
    owner: dict[str, str] = {}
    normalised: dict[str, list[str]] = {}

    for group, entries in groups.items():
        if not isinstance(entries, list) or not all(isinstance(item, str) for item in entries):
            report.error(f"TEST_GROUP_INVALID: {group} must be a list of file names")
            continue
        normalised[group] = sorted(entries)
        if not entries:
            report.error(f"TEST_GROUP_EMPTY: {group}")
        for name in entries:
            if Path(name).name != name:
                report.error(f"TEST_ENTRY_NOT_A_FILE_NAME: {group}: {name}")
                continue
            if name in owner:
                report.error(f"TEST_FILE_DUPLICATE: {name} in {owner[name]} and {group}")
            else:
                owner[name] = group
            if name not in discovered_set:
                report.error(f"TEST_FILE_STALE: {group}: {name}")

    unclassified = sorted(discovered_set - set(owner))
    for name in unclassified:
        report.error(f"TEST_FILE_UNCLASSIFIED: {name}")

    subsets = manifest.get("ci_subsets") or {}
    if not isinstance(subsets, dict):
        report.error("TEST_MANIFEST_INVALID: 'ci_subsets' must be an object")
        subsets = {}
    for name, entries in subsets.items():
        if not isinstance(entries, list) or not entries:
            report.error(f"CI_SUBSET_INVALID: {name}")
            continue
        if len(entries) != len(set(entries)):
            report.error(f"CI_SUBSET_DUPLICATE_ENTRY: {name}")
        for entry in entries:
            if entry not in discovered_set:
                report.error(f"CI_SUBSET_STALE: {name}: {entry}")
            elif entry not in owner:
                report.error(f"CI_SUBSET_UNCLASSIFIED: {name}: {entry}")

    ci_jobs = manifest.get("ci_jobs")
    if not isinstance(ci_jobs, dict) or not ci_jobs:
        report.error("TEST_MANIFEST_INVALID: 'ci_jobs' must be a non-empty object")
    else:
        covered = set()
        for job, spec in ci_jobs.items():
            if not isinstance(spec, dict):
                report.error(f"CI_JOB_INVALID: {job}")
                continue
            job_group: object = spec.get("group")
            subset = spec.get("subset")
            if not isinstance(job_group, str) and not isinstance(subset, str):
                report.error(f"CI_JOB_INVALID: {job}")
                continue
            if isinstance(job_group, str):
                if job_group not in groups:
                    report.error(f"CI_JOB_UNKNOWN_GROUP: {job}: {job_group}")
                covered.add(job_group)
            if isinstance(subset, str) and subset not in subsets:
                report.error(f"CI_JOB_UNKNOWN_SUBSET: {job}: {subset}")
            if not isinstance(spec.get("required"), bool):
                report.error(f"CI_JOB_REQUIRED_FLAG_INVALID: {job}")
        for group in REQUIRED_GROUPS:
            if group not in covered:
                report.error(f"TEST_GROUP_WITHOUT_CI_JOB: {group}")

    return normalised


def _workflow_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def _job_block(text: str, job: str) -> str | None:
    match = re.search(rf"^  {re.escape(job)}:\s*$", text, re.MULTILINE)
    if not match:
        return None
    rest = text[match.end() :]
    following = re.search(r"^  [A-Za-z0-9_-]+:\s*$", rest, re.MULTILINE)
    return rest[: following.start()] if following else rest


def _check_workflows(
    manifest: dict[str, Any], workflow_paths: tuple[Path, ...], report: Report
) -> None:
    raw_jobs = manifest.get("ci_jobs")
    ci_jobs: dict[str, Any] = raw_jobs if isinstance(raw_jobs, dict) else {}
    observed_groups: set[str] = set()
    seen_jobs: set[str] = set()
    ci_gate_text = ""
    for path in workflow_paths:
        text = "\n".join(_workflow_lines(path))
        if not text:
            report.warn(f"CI_WORKFLOW_MISSING: {path}")
            continue
        if path.name == "ci-gate.yml":
            ci_gate_text = text
        for job, spec in ci_jobs.items():
            if not isinstance(spec, dict):
                continue
            block = _job_block(text, job)
            if block is None:
                continue
            seen_jobs.add(job)
            group = spec.get("group")
            groups_in_block = {
                part for raw in GROUP_ENV.findall(block) for part in raw.split(",") if part
            }
            observed_groups.update(groups_in_block)
            if group and group not in groups_in_block:
                report.error(
                    f"CI_JOB_GROUP_MISSING: {path.name}: {job}: " f"expected QTS_TEST_GROUP={group}"
                )
            if spec.get("required") and path.name != "ci-gate.yml":
                report.error(f"CI_REQUIRED_JOB_OUTSIDE_GATE: {path.name}: {job}")
        for number, line in enumerate(text.splitlines(), start=1):
            if not PYTEST_LINE.search(line):
                continue
            if RAW_TEST_FILE.search(line):
                report.error(
                    f"CI_RAW_TEST_SELECTION: {path.name}:{number}: "
                    "select files through ci/test_manifest.json, not a raw list"
                )
            if re.search(r"(?<!\S)-k(?!\S)", line) or "--ignore" in line:
                report.error(
                    f"CI_SELECTION_NARROWING: {path.name}:{number}: "
                    "-k/--ignore hides tests from the auditable group selection"
                )

    for job, spec in ci_jobs.items():
        if not isinstance(spec, dict):
            continue
        if job not in seen_jobs:
            report.error(f"CI_JOB_MISSING: {job}")
        group = spec.get("group")
        subset = spec.get("subset")
        if spec.get("required") and group and group not in observed_groups:
            report.error(f"CI_REQUIRED_GROUP_NOT_EXECUTED: {job}: {group}")
        if spec.get("required") and subset and job not in seen_jobs:
            report.error(f"CI_REQUIRED_SUBSET_NOT_EXECUTED: {job}: {subset}")

    # The aggregate gate must depend on every required job and on no optional
    # job; otherwise a failing/skipped required job could still be green.
    extra_required = manifest.get("ci_required_non_test_jobs") or []
    if not isinstance(extra_required, list) or not all(
        isinstance(item, str) for item in extra_required
    ):
        report.error("CI_REQUIRED_NON_TEST_JOBS_INVALID")
        extra_required = []
    for job in extra_required:
        if _job_block(ci_gate_text, job) is None:
            report.error(f"CI_REQUIRED_NON_TEST_JOB_MISSING: {job}")
    known_jobs = set(ci_jobs) | set(extra_required) | {"ci-required"}
    jobs_text = ci_gate_text.split("\njobs:", 1)[-1]
    for job in sorted(set(re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", jobs_text, re.M)) - known_jobs):
        report.error(f"CI_JOB_UNREGISTERED: {job}")
    gate = _job_block(ci_gate_text, "ci-required")
    if gate is None:
        report.error("CI_REQUIRED_JOB_MISSING")
    else:
        needs_match = re.search(r"needs:\s*\n((?:\s*-\s*[A-Za-z0-9_-]+\s*(?:\n|$))+)", gate)
        needs = (
            set(re.findall(r"^\s*-\s*([A-Za-z0-9_-]+)\s*$", needs_match.group(1), re.MULTILINE))
            if needs_match
            else set()
        )
        required_jobs = {
            job for job, spec in ci_jobs.items() if isinstance(spec, dict) and spec.get("required")
        } | set(extra_required)
        for job in sorted(required_jobs - needs):
            report.error(f"CI_REQUIRED_GATE_MISSING_NEED: {job}")
        for job in sorted(needs - required_jobs):
            report.error(f"CI_REQUIRED_GATE_OPTIONAL_NEED: {job}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--workflow",
        type=Path,
        action="append",
        default=None,
        help="Workflow to check; defaults to ci-gate.yml and optional-gates.yml",
    )
    parser.add_argument("--no-workflow-check", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    report = Report()
    manifest = _load_manifest(args.manifest, report)
    groups: dict[str, list[str]] = {}
    if manifest:
        groups = _validate_manifest(manifest, ROOT, report)
        if not args.no_workflow_check:
            workflows = tuple(args.workflow) if args.workflow else DEFAULT_WORKFLOWS
            _check_workflows(manifest, workflows, report)

    result = {
        "ok": report.ok,
        "manifest": str(args.manifest),
        "total_test_files": sum(len(entries) for entries in groups.values()),
        "groups": {group: len(entries) for group, entries in sorted(groups.items())},
        "errors": report.errors,
        "warnings": report.warnings,
    }
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
