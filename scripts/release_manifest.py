"""Generate the immutable release manifest for a built image.

The manifest binds the application image digest to the commit, workflow run,
dependency lock hashes, schema version and the test/front-end evidence.  It is
the only input the deploy preflight trusts for ``QTS_EXPECTED_DIGEST``.

No secret is read or written.  ``--image-digest`` must be the
``repo@sha256:<64 hex>`` form printed by the build step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}$")
REF_RE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"[0-9a-f]{40}$")


def _normalise_digest(value: object) -> str:
    text = str(value or "").strip()
    return text.rsplit("@", 1)[-1] if "@" in text else text


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_digest(path: Path) -> tuple[str, int]:
    """Deterministic hash of a directory tree (names + contents, sorted)."""
    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    for item in files:
        relative = item.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(item).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), len(files)


def _artifact(path: Path) -> dict[str, Any]:
    if path.is_dir():
        tree_digest, count = _directory_digest(path)
        return {
            "path": str(path),
            "present": True,
            "kind": "directory",
            "files": count,
            "sha256": tree_digest,
        }
    return {
        "path": str(path),
        "present": path.is_file(),
        "kind": "file",
        "sha256": sha256_file(path) if path.is_file() else None,
    }


def _load_smoke(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    commit = args.commit.strip().lower()
    if not COMMIT_RE.fullmatch(commit):
        raise SystemExit(f"invalid --commit (expected 40 lowercase hex): {args.commit!r}")
    image_digest = args.image_digest.rsplit("@", 1)[-1].strip().lower()
    if not DIGEST_RE.fullmatch(image_digest):
        raise SystemExit(
            f"invalid --image-digest (expected sha256:<64 hex>): {args.image_digest!r}"
        )
    image_ref = args.image_digest.strip()
    if not REF_RE.fullmatch(image_ref):
        raise SystemExit(f"invalid --image-digest reference: {args.image_digest!r}")

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "release_id": args.release_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "commit": commit,
        "tree_clean": bool(args.tree_clean),
        "ref": args.ref,
        "workflow": args.workflow,
        "workflow_run": args.workflow_run,
        "image": image_ref,
        "image_digest": image_digest,
        "schema_version_applied": args.schema_version,
        "environment": args.environment,
        "dependencies": {
            "ci_constraints": _artifact(Path(args.ci_constraints)),
            "runtime_constraints": _artifact(Path(args.runtime_constraints)),
        },
        "evidence": {
            "junit": [_artifact(Path(path)) for path in args.junit],
            "coverage": _artifact(Path(args.coverage)) if args.coverage else None,
            "frontend": _artifact(Path(args.frontend)) if args.frontend else None,
            "sbom": _artifact(Path(args.sbom)) if args.sbom else None,
            "scan": _artifact(Path(args.scan)) if args.scan else None,
            "startup_smoke": _artifact(Path(args.startup_smoke)) if args.startup_smoke else None,
        },
        "non_claims": [
            "This manifest does not claim the image has been deployed.",
            "This manifest does not claim the current digest passed the Demo 24h window.",
        ],
    }
    if not args.allow_missing_evidence:
        required = {
            "ci_constraints": manifest["dependencies"]["ci_constraints"]["present"],
            "runtime_constraints": manifest["dependencies"]["runtime_constraints"]["present"],
            "junit": bool(manifest["evidence"]["junit"])
            and all(item["present"] for item in manifest["evidence"]["junit"]),
            "coverage": bool(
                manifest["evidence"]["coverage"] and manifest["evidence"]["coverage"]["present"]
            ),
            "frontend": bool(
                manifest["evidence"]["frontend"] and manifest["evidence"]["frontend"]["present"]
            ),
            "sbom": bool(manifest["evidence"]["sbom"] and manifest["evidence"]["sbom"]["present"]),
            "scan": bool(manifest["evidence"]["scan"] and manifest["evidence"]["scan"]["present"]),
            "startup_smoke": bool(
                manifest["evidence"]["startup_smoke"]
                and manifest["evidence"]["startup_smoke"]["present"]
            ),
        }
        missing = sorted(key for key, present in required.items() if not present)
        if missing:
            raise SystemExit("RELEASE_EVIDENCE_MISSING: " + ",".join(missing))
        smoke = _load_smoke(args.startup_smoke)
        if smoke.get("passed") is not True:
            raise SystemExit("RELEASE_STARTUP_SMOKE_NOT_PASSED")
        if smoke.get("image_digest_ok") is not True:
            raise SystemExit("RELEASE_STARTUP_SMOKE_DIGEST_NOT_VERIFIED")
        if _normalise_digest(smoke.get("image")) != image_digest:
            raise SystemExit("RELEASE_STARTUP_SMOKE_DIGEST_MISMATCH")
        manifest["startup_smoke_summary"] = {
            "passed": smoke.get("passed"),
            "image": smoke.get("image"),
            "image_digest_ok": smoke.get("image_digest_ok"),
            "all_health": smoke.get("health"),
        }
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--ref", default="")
    parser.add_argument("--workflow", default="release-build")
    parser.add_argument("--workflow-run", default="")
    parser.add_argument("--image-digest", required=True, help="repo@sha256:<64 hex>")
    parser.add_argument("--tree-clean", action="store_true")
    parser.add_argument("--schema-version", type=int, default=3)
    parser.add_argument("--environment", default="demo")
    parser.add_argument(
        "--ci-constraints", default=str(ROOT / "trader/requirements-ci.constraints.txt")
    )
    parser.add_argument(
        "--runtime-constraints",
        default=str(ROOT / "trader/requirements-runtime.constraints.txt"),
    )
    parser.add_argument("--junit", action="append", default=[])
    parser.add_argument("--coverage", default="")
    parser.add_argument("--frontend", default="")
    parser.add_argument("--sbom", default="")
    parser.add_argument("--scan", default="")
    parser.add_argument("--startup-smoke", default="")
    parser.add_argument(
        "--allow-missing-evidence",
        action="store_true",
        help="Local/dev only: do not require every evidence artifact",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = build_manifest(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"ok": True, "output": str(args.output), "image_digest": manifest["image_digest"]}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
