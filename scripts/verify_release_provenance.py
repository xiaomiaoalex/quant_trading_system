"""Fail-closed provenance gate for protected Demo deploy/evidence workflows.

Before a job checks out an operator-supplied commit or executes repository
scripts with Demo secrets, it must prove that:

* the referenced Release Build run is a completed, successful run on ``main``;
* it was triggered by the ``workflow_run`` event (i.e. after CI Gate success);
* its head SHA equals the requested commit; and
* the downloaded release manifest matches the requested release id, commit and
  image digest.

The script can read a pre-fetched run JSON (tests/CI) or fetch the GitHub API
with a token from an environment variable.  It never prints the token.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"[0-9a-f]{40}$")


def _normalise_digest(value: object) -> str:
    text = str(value or "").strip()
    return text.rsplit("@", 1)[-1] if "@" in text else text


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def fetch_run(repository: str, run_id: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/actions/runs/{run_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def evaluate(
    *,
    run: dict[str, Any],
    manifest: dict[str, Any],
    repository: str,
    run_id: str,
    commit: str,
    digest: str,
    release_id: str,
    expected_branch: str = "main",
    expected_workflow_path: str = ".github/workflows/release-build.yml",
) -> list[str]:
    errors: list[str] = []
    expected_commit = commit.strip().lower()
    expected_digest = _normalise_digest(digest)
    if not COMMIT_RE.fullmatch(expected_commit):
        errors.append("EXPECTED_COMMIT_INVALID")
    if not DIGEST_RE.fullmatch(expected_digest):
        errors.append("EXPECTED_DIGEST_INVALID")

    if str(run.get("id") or "") != str(run_id):
        errors.append("PROVENANCE_RUN_ID_MISMATCH")
    repo = run.get("repository") or {}
    if str(repo.get("full_name") or "").lower() != repository.lower():
        errors.append("PROVENANCE_REPOSITORY_MISMATCH")
    if str(run.get("path") or "") != expected_workflow_path:
        errors.append("PROVENANCE_WORKFLOW_MISMATCH")
    if str(run.get("event") or "") != "workflow_run":
        errors.append("PROVENANCE_EVENT_MISMATCH")
    if str(run.get("head_branch") or "") != expected_branch:
        errors.append("PROVENANCE_BRANCH_MISMATCH")
    if str(run.get("status") or "") != "completed":
        errors.append("PROVENANCE_RUN_NOT_COMPLETED")
    if str(run.get("conclusion") or "") != "success":
        errors.append("PROVENANCE_RUN_NOT_SUCCESSFUL")
    if str(run.get("head_sha") or "").lower() != expected_commit:
        errors.append("PROVENANCE_HEAD_SHA_MISMATCH")

    if str(manifest.get("commit") or "").lower() != expected_commit:
        errors.append("MANIFEST_COMMIT_MISMATCH")
    if _normalise_digest(manifest.get("image_digest")) != expected_digest:
        errors.append("MANIFEST_DIGEST_MISMATCH")
    if str(manifest.get("release_id") or "") != release_id:
        errors.append("MANIFEST_RELEASE_ID_MISMATCH")
    if str(manifest.get("environment") or "") != "demo":
        errors.append("MANIFEST_ENVIRONMENT_NOT_DEMO")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-json", type=Path, default=None)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--expected-branch", default="main")
    parser.add_argument("--expected-workflow-path", default=".github/workflows/release-build.yml")
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--run-json-out", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    if args.run_json is not None:
        run = _read_json(args.run_json)
    else:
        token = os.getenv(args.token_env, "").strip()
        if not token:
            print(json.dumps({"ok": False, "errors": ["GITHUB_TOKEN_MISSING"]}))
            return 1
        run = fetch_run(args.repository, args.run_id, token)
        if args.run_json_out is not None:
            args.run_json_out.parent.mkdir(parents=True, exist_ok=True)
            args.run_json_out.write_text(
                json.dumps(run, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    manifest = _read_json(args.manifest)
    errors = evaluate(
        run=run,
        manifest=manifest,
        repository=args.repository,
        run_id=args.run_id,
        commit=args.commit,
        digest=args.digest,
        release_id=args.release_id,
        expected_branch=args.expected_branch,
        expected_workflow_path=args.expected_workflow_path,
    )
    result = {"ok": not errors, "errors": errors, "run_id": args.run_id}
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
