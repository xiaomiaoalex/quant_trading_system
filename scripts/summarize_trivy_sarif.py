"""Summarise a Trivy SARIF report so a failed scan gate explains itself.

The release workflow fails the job when the built image contains fixable
findings.  The SARIF report is still written by Trivy in that case, but the
workflow log only shows the exit code.  This script turns the report into a
compact table (severity, id, package, installed version, fixed version) so the
failure reason is visible without downloading an artifact.

Exit codes:

* ``0`` - the report parsed and contains no finding at or above the
  requested severity,
* ``1`` - the report contains at least one finding at or above the
  requested severity,
* ``2`` - the report is missing, cannot be read/decoded, or is not a
  structurally valid Trivy SARIF document.

The script is fail-closed: a report that cannot be understood is never
reported as "no findings".  Only the minimal structure a Trivy SARIF
document is expected to have is validated (top-level object, ``runs``
array, ``results`` array, object records); this is deliberately not a
generic SARIF schema check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITIES)}


class SarifFormatError(ValueError):
    """The report is not a structurally valid Trivy SARIF document."""


def _field(text: str, label: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(f"{label.lower()}:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def _expect_mapping(value: object, context: str) -> dict:
    if not isinstance(value, dict):
        raise SarifFormatError(f"{context} must be a JSON object")
    return value


def _expect_array(value: object, context: str) -> list:
    if not isinstance(value, list):
        raise SarifFormatError(f"{context} must be a JSON array")
    return value


def _optional_text(container: dict, key: str, context: str) -> str:
    if key not in container:
        return ""
    value = container[key]
    if not isinstance(value, str):
        raise SarifFormatError(f"{context}.{key} must be a string")
    return value


def _message_text(result: dict, context: str) -> str:
    if "message" not in result:
        return ""
    message = _expect_mapping(result["message"], f"{context}.message")
    text = message.get("text", "")
    if not isinstance(text, str):
        raise SarifFormatError(f"{context}.message.text must be a string")
    return text


def _location_text(result: dict, context: str) -> str:
    if "locations" not in result:
        return ""
    locations = _expect_array(result["locations"], f"{context}.locations")
    if not locations:
        return ""
    location = _expect_mapping(locations[0], f"{context}.locations[0]")
    if "message" not in location:
        return ""
    message = _expect_mapping(location["message"], f"{context}.locations[0].message")
    text = message.get("text", "")
    if not isinstance(text, str):
        raise SarifFormatError(f"{context}.locations[0].message.text must be a string")
    return text


def parse_findings(payload: object) -> list[dict[str, str]]:
    """Return the findings of a Trivy SARIF payload as flat dictionaries.

    Raises :class:`SarifFormatError` when the payload is not structurally a
    Trivy SARIF document, so that a broken report can never be mistaken for
    a clean scan.
    """
    document = _expect_mapping(payload, "sarif report")
    if "runs" not in document:
        raise SarifFormatError("sarif report has no 'runs' array")
    runs = _expect_array(document["runs"], "'runs'")

    findings: list[dict[str, str]] = []
    for run_index, run in enumerate(runs):
        run_context = f"runs[{run_index}]"
        run_record = _expect_mapping(run, run_context)
        if "results" not in run_record:
            raise SarifFormatError(f"{run_context} has no 'results' array")
        results = _expect_array(run_record["results"], f"{run_context}.results")
        for result_index, result in enumerate(results):
            context = f"{run_context}.results[{result_index}]"
            result_record = _expect_mapping(result, context)
            message = _message_text(result_record, context)
            target = _location_text(result_record, context)
            findings.append(
                {
                    "id": _optional_text(result_record, "ruleId", context),
                    "severity": _field(message, "Severity").upper(),
                    "package": _field(message, "Package"),
                    "installed": _field(message, "Installed Version"),
                    "fixed": _field(message, "Fixed Version"),
                    "link": _field(message, "Link"),
                    "target": target,
                    "level": _optional_text(result_record, "level", context),
                }
            )
    return findings


def sort_findings(findings: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        findings,
        key=lambda item: (
            SEVERITY_RANK.get(item["severity"], len(SEVERITIES)),
            item["id"],
            item["package"],
        ),
    )


def render(findings: list[dict[str, str]]) -> str:
    if not findings:
        return "No findings in the Trivy SARIF report."
    header = f"{'SEVERITY':<9} {'ID':<22} {'PACKAGE':<24} {'INSTALLED':<22} {'FIXED':<22}"
    lines = [header, "-" * len(header)]
    for item in sort_findings(findings):
        lines.append(
            f"{item['severity']:<9} {item['id']:<22} {item['package']:<24} "
            f"{item['installed']:<22} {item['fixed']:<22}"
        )
    lines.append("")
    lines.append(f"Total findings: {len(findings)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sarif", help="path to the Trivy SARIF report")
    parser.add_argument(
        "--fail-on",
        default="HIGH",
        choices=SEVERITIES,
        help="lowest severity that makes the script exit non-zero",
    )
    args = parser.parse_args(argv)

    path = Path(args.sarif)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"TRIVY_SARIF_UNREADABLE: {path}: {exc}", file=sys.stderr)
        return 2
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"TRIVY_SARIF_UNREADABLE: {path}: {exc}", file=sys.stderr)
        return 2

    try:
        findings = parse_findings(payload)
    except SarifFormatError as exc:
        print(f"TRIVY_SARIF_INVALID: {path}: {exc}", file=sys.stderr)
        return 2

    print(render(findings))
    threshold = SEVERITY_RANK[args.fail_on]
    blocking = [
        item
        for item in findings
        if SEVERITY_RANK.get(item["severity"], len(SEVERITIES)) <= threshold
    ]
    return 1 if blocking else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
