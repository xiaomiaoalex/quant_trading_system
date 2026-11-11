"""Fail a CI job unless its JUnit report proves the required tests ran.

``pytest --junitxml`` is the machine-readable record of what actually executed.
A green process exit alone is not enough: a misconfigured job can collect zero
tests, and Postgres jobs historically turned missing infrastructure into skips.
This checker makes those outcomes explicit failures.

Usage:
    python scripts/check_junit.py reports/junit-p0.xml --min-tests 1 \
        --require-zero-skipped
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def _suite_elements(root: ET.Element) -> list[ET.Element]:
    if root.tag == "testsuite":
        return [root]
    if root.tag == "testsuites":
        suites = list(root.findall("testsuite"))
        if suites:
            return suites
        # Some reporters nest a single testsuite under testsuites.
        return [root]
    return [root]


def inspect(path: Path) -> dict[str, int]:
    tree = ET.parse(path)
    root = tree.getroot()
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "xfailed": 0}
    for suite in _suite_elements(root):
        for key in ("tests", "failures", "errors", "skipped"):
            totals[key] += int(suite.get(key, "0") or 0)
        for case in suite.iter("testcase"):
            for skipped in case.findall("skipped"):
                if skipped.get("type") == "pytest.xfail":
                    totals["xfailed"] += 1
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("junit", type=Path)
    parser.add_argument("--min-tests", type=int, default=1)
    parser.add_argument("--require-zero-skipped", action="store_true")
    parser.add_argument("--require-zero-xfailed", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    errors: list[str] = []
    if not args.junit.exists():
        errors.append(f"JUNIT_MISSING: {args.junit}")
        totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "xfailed": 0}
    else:
        try:
            totals = inspect(args.junit)
        except (ET.ParseError, OSError) as exc:
            errors.append(f"JUNIT_INVALID: {args.junit}: {exc}")
            totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "xfailed": 0}

    if totals["tests"] < args.min_tests:
        errors.append(f"JUNIT_TOO_FEW_TESTS: {totals['tests']} < {args.min_tests}")
    if totals["failures"] or totals["errors"]:
        errors.append(f"JUNIT_NOT_GREEN: failures={totals['failures']} errors={totals['errors']}")
    if args.require_zero_skipped and totals["skipped"]:
        errors.append(f"JUNIT_SKIPPED_NOT_ALLOWED: {totals['skipped']} skipped")
    if args.require_zero_xfailed and totals["xfailed"]:
        errors.append(f"JUNIT_XFAILED_NOT_ALLOWED: {totals['xfailed']} xfailed")

    result = {"ok": not errors, "junit": str(args.junit), **totals, "errors": errors}
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
