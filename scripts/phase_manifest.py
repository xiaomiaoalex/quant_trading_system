"""Build a canonical phase source manifest without staging anything.

The manifest lists every changed/untracked task source path relative to the
phase base commit, its working-tree SHA-256 (``null`` for deletions), and a
canonical digest over the sorted ``[path, sha256]`` pairs. Operational metadata
(artifacts, workflow state, independent reports, nested worktrees) is excluded
and listed explicitly so a reviewer can reproduce the exact set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_EXCLUDES = (
    "artifacts/",
    ".workbuddy/",
    "workflow/",
    ".worktrees/",
    ".kilo/",
    "docs/FOUNDATION_WORKFLOW_STATE.json",
    "docs/UNIFIED_TRADING_ACCEPTANCE.md",
    "docs/CI_CD_IMPROVEMENT_RFC.md",
    "docs/CI_CD_WORKFLOW_STATE.json",
    "docs/PHASE2_INDEPENDENT_TEST_REPORT.md",
    "docs/PHASE3_INDEPENDENT_TEST_REPORT.md",
    "docs/PHASE4_INDEPENDENT_TEST_REPORT.md",
    "docs/PHASE5_INDEPENDENT_TEST_REPORT.md",
    "docs/PHASE6_INDEPENDENT_TEST_REPORT.md",
)


def _excluded(path: str, excludes: tuple[str, ...]) -> bool:
    return any(path == item or path.startswith(item) for item in excludes)


def _git(*argv: str) -> str:
    result = subprocess.run(["git", *argv], cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {argv} failed")
    return result.stdout


def _changed_paths(base: str) -> list[str]:
    paths: set[str] = set()
    for line in _git("diff", "--name-status", base).splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        # Renames carry old and new paths; the new path is the source of truth.
        paths.add(parts[-1])
    for line in _git("ls-files", "--others", "--exclude-standard").splitlines():
        if line.strip():
            paths.add(line.strip())
    return sorted(paths)


def build(base: str, excludes: tuple[str, ...]) -> dict:
    files = []
    skipped = []
    for path in _changed_paths(base):
        if _excluded(path, excludes):
            skipped.append(path)
            continue
        candidate = ROOT / path
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest() if candidate.is_file() else None
        files.append({"path": path, "sha256": digest})
    canonical = json.dumps(
        [[item["path"], item["sha256"]] for item in files],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "kind": "phase_source_manifest",
        "base": base,
        "file_count": len(files),
        "files": files,
        "source_digest": hashlib.sha256(canonical).hexdigest(),
        "digest_format": "sha256(canonical sorted [path,sha256] pairs; ensure_ascii=False; compact separators)",
        "excluded": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a phase source manifest")
    parser.add_argument("--base", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="additional path prefix to exclude (repeatable)",
    )
    args = parser.parse_args()
    excludes = DEFAULT_EXCLUDES + tuple(args.exclude)
    manifest = build(args.base, excludes)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "file_count": manifest["file_count"],
                "source_digest": manifest["source_digest"],
                "out": str(output),
            }
        )
    )


if __name__ == "__main__":
    main()
