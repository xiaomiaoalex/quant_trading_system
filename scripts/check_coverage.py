"""Enforce reviewable per-module coverage floors from a coverage.py XML report.

A single repo-wide percentage hides missing coverage in the trading core.  The
thresholds live in ``ci/coverage_thresholds.json`` with the measured baseline,
the exact command that produced it and the rationale for each floor.
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_THRESHOLDS = ROOT / "ci" / "coverage_thresholds.json"


def _file_rows(path: Path) -> dict[str, tuple[int, int]]:
    root = ET.parse(path).getroot()
    rows: dict[str, tuple[int, int]] = {}
    for cls in root.iter("class"):
        filename = (cls.get("filename") or "").replace("\\", "/")
        if not filename.endswith(".py"):
            continue
        repo_path = filename if filename.startswith("trader/") else f"trader/{filename}"
        lines = cls.findall(".//line")
        total = len(lines)
        covered = sum(1 for line in lines if int(line.get("hits", "0")) > 0)
        previous_total, previous_covered = rows.get(repo_path, (0, 0))
        rows[repo_path] = (previous_total + total, previous_covered + covered)
    return rows


def _measure(rows: dict[str, tuple[int, int]], selector: str, kind: str) -> tuple[int, int]:
    selected: Iterable[tuple[int, int]]
    if kind == "overall":
        selected = rows.values()
    elif kind == "package":
        selected = [value for path, value in rows.items() if path.startswith(selector)]
    else:
        selected = [rows[selector]] if selector in rows else []
    total = sum(item[0] for item in selected)
    covered = sum(item[1] for item in selected)
    return total, covered


def evaluate(coverage_xml: Path, thresholds_path: Path) -> dict[str, Any]:
    config = json.loads(thresholds_path.read_text(encoding="utf-8"))
    rows = _file_rows(coverage_xml)
    results = []
    for entry in config.get("thresholds", []):
        selector = str(entry.get("path") or "")
        kind = str(entry.get("kind") or "")
        total, covered = _measure(rows, selector, kind)
        percent = round(100.0 * covered / total, 2) if total else 0.0
        minimum = float(entry.get("min_percent") or 0)
        results.append(
            {
                "path": selector or "<overall>",
                "kind": kind,
                "statements": total,
                "covered": covered,
                "percent": percent,
                "min_percent": minimum,
                "baseline_percent": entry.get("baseline_percent"),
                "passed": bool(total) and percent >= minimum,
            }
        )
    failed = [item for item in results if not item["passed"]]
    return {
        "ok": not failed,
        "coverage_xml": str(coverage_xml),
        "thresholds": str(thresholds_path),
        "results": results,
        "failed": [item["path"] for item in failed],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_xml", type=Path)
    parser.add_argument("--thresholds", type=Path, default=DEFAULT_THRESHOLDS)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()
    if not args.coverage_xml.is_file():
        result = {
            "ok": False,
            "coverage_xml": str(args.coverage_xml),
            "errors": ["COVERAGE_XML_MISSING"],
        }
    else:
        result = evaluate(args.coverage_xml, args.thresholds)
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
