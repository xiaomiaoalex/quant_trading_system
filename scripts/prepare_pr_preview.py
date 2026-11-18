"""Prepare a deterministic PR preview without touching any running service.

The workflow supplies a clean checkout, a candidate runtime image id and a
synthetic operator password.  This script writes a non-secret plan containing
the exact source revision, image ids and the isolated Compose project only
after the preview preflight succeeds.  It never builds, pulls, tags, or starts
a container, and it never reads venue credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--merge-sha", required=True)
    parser.add_argument("--runtime-image", required=True)
    parser.add_argument("--runtime-image-id", required=True)
    parser.add_argument("--ui-image", required=True)
    parser.add_argument("--ui-image-id", required=True)
    parser.add_argument("--preview-plan", type=Path, required=True)
    args = parser.parse_args(argv)

    errors: list[str] = []
    for name, value in (
        ("head_sha", args.head_sha),
        ("merge_sha", args.merge_sha),
    ):
        if not COMMIT_RE.fullmatch(value):
            errors.append(f"INVALID_{name.upper()}")
    for name, value in (
        ("runtime_image_id", args.runtime_image_id),
        ("ui_image_id", args.ui_image_id),
    ):
        if not IMAGE_ID_RE.fullmatch(value):
            errors.append(f"INVALID_{name.upper()}")
    if "@sha256:" in args.runtime_image or "@sha256:" in args.ui_image:
        errors.append("REGISTRY_DIGEST_NOT_LOCAL_IMAGE_ID")

    current = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()
    if current != args.merge_sha:
        errors.append("CHECKOUT_SHA_MISMATCH")
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if dirty:
        errors.append("CHECKOUT_DIRTY")

    plan: dict[str, Any] = {
        "ok": not errors,
        "errors": errors,
        "source": {
            "pr_head_sha": args.head_sha,
            "tested_merge_sha": args.merge_sha,
            "checkout_sha": current,
        },
        "artifacts": {
            "runtime_image": args.runtime_image,
            "runtime_image_id": args.runtime_image_id,
            "ui_image": args.ui_image,
            "ui_image_id": args.ui_image_id,
            "identity_kind": "local_image_id",
        },
        "compose_files": [
            str(Path("compose.yml")),
            str(Path("deploy/compose.preview.yml")),
        ],
        "constraints_sha256": {
            "ci": sha256_file(ROOT / "trader/requirements-ci.constraints.txt"),
            "runtime": sha256_file(ROOT / "trader/requirements-runtime.constraints.txt"),
        },
        "non_claims": [
            "No registry digest is claimed for a locally built PR image.",
            "This plan does not start services or validate functional behavior.",
        ],
    }
    args.preview_plan.parent.mkdir(parents=True, exist_ok=True)
    args.preview_plan.write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": plan["ok"], "errors": errors, "plan": str(args.preview_plan)}))
    return 0 if plan["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
