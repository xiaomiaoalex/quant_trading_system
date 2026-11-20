"""Collect runtime evidence that binds an observation window to the deployed release.

Strict Demo evidence must not trust a hand-written ``release-binding.json``.
This script runs on the Demo host, inspects the actual Compose containers and
the Control health endpoints, and writes a binding with
``runtime_inspected=true`` plus container/image/label/health facts.

It never reads or writes secret values.  Expected values are compared with
container labels and image references; only non-secret identifiers are emitted.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release_binding import normalise_digest  # noqa: E402

DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}$")
EXPECTED_SERVICES = (
    "postgres",
    "alert-sink",
    "control",
    "execution",
    "research",
    "history",
    "backup",
)
APP_SERVICES = ("control", "execution", "research", "history", "backup")
LABEL_RELEASE_ID = "com.qts.release.id"
LABEL_RELEASE_COMMIT = "com.qts.release.commit"
LABEL_ENVIRONMENT = "com.qts.environment"


def _run(argv: list[str], *, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def _http(url: str, *, attempts: int = 5, timeout: float = 5.0) -> dict[str, Any]:
    opener = build_opener(ProxyHandler({}))
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            with opener.open(url, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                try:
                    payload = json.loads(body)
                except ValueError:
                    payload = {"raw": body[:500]}
                return {"status": int(response.status), "body": payload}
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = {"error": type(exc).__name__}
            return {"status": int(exc.code), "body": payload}
        except (URLError, ConnectionError) as exc:
            last_error = type(exc).__name__
            if attempt < attempts:
                time.sleep(0.5)
    return {"status": 0, "body": {"error": last_error or "HTTP_UNAVAILABLE"}}


def _docker_inspect(name: str) -> dict[str, Any] | None:
    result = _run(["docker", "inspect", name])
    if result.returncode != 0:
        return None
    payload = json.loads(result.stdout)
    return payload[0] if payload else None


def collect_containers(project: str) -> dict[str, dict[str, Any]]:
    result = _run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            '{{.Names}}\t{{.Label "com.docker.compose.service"}}',
        ]
    )
    containers: dict[str, dict[str, Any]] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        name, service = parts
        payload = _docker_inspect(name)
        if payload is None:
            containers[service] = {
                "name": name,
                "present": False,
                "running": False,
                "health": "missing",
                "image": "",
                "image_id": "",
                "labels": {},
            }
            continue
        state = payload.get("State") or {}
        health = (state.get("Health") or {}).get("Status") or "none"
        config = payload.get("Config") or {}
        containers[service] = {
            "name": name,
            "present": True,
            "running": bool(state.get("Running")),
            "health": health,
            "image": str(config.get("Image") or ""),
            "image_id": str(payload.get("Image") or ""),
            "labels": {
                key: value
                for key, value in (config.get("Labels") or {}).items()
                if key.startswith("com.qts.")
            },
        }
    return containers


def _digest_of(image: str) -> str:
    return image.rsplit("@", 1)[-1] if "@" in image else ""


def runtime_identity_from_health(health: dict[str, Any]) -> dict[str, Any]:
    """Read the runtime identity the Control runtime reported.

    The identity is produced by the process that answered the health request,
    so it ties the collected facts to the release that was actually running.
    """
    trading_body = (health.get("trading") or {}).get("body") or {}
    trading: dict[str, Any] = trading_body if isinstance(trading_body, dict) else {}
    raw_release = trading.get("release")
    release: dict[str, Any] = raw_release if isinstance(raw_release, dict) else {}
    return {
        "release_id": str(release.get("release_id") or ""),
        "commit": str(release.get("commit") or "").lower(),
        "image_digest": str(release.get("image_digest") or ""),
        "environment": str(release.get("environment") or "").lower(),
        "instance_owner": str(trading.get("instance_owner") or ""),
        "lease_token": str(trading.get("lease_token") or ""),
    }


def build_binding(
    *,
    project: str,
    containers: dict[str, dict[str, Any]],
    health: dict[str, Any],
    expected_release_id: str,
    expected_commit: str,
    expected_digest: str,
    expected_environment: str = "demo",
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    services = set(containers)
    for service in EXPECTED_SERVICES:
        if service not in services:
            errors.append(f"CONTAINER_MISSING:{service}")
    for service, item in sorted(containers.items()):
        if not item.get("present") or not item.get("running"):
            errors.append(f"CONTAINER_NOT_RUNNING:{service}")
        if item.get("health") != "healthy":
            errors.append(f"CONTAINER_NOT_HEALTHY:{service}:{item.get('health')}")
    for service in APP_SERVICES:
        item = containers.get(service) or {}
        labels = item.get("labels") or {}
        if labels.get(LABEL_RELEASE_ID) != expected_release_id:
            errors.append(f"LABEL_RELEASE_ID_MISMATCH:{service}")
        if str(labels.get(LABEL_RELEASE_COMMIT) or "").lower() != expected_commit.lower():
            errors.append(f"LABEL_RELEASE_COMMIT_MISMATCH:{service}")
        if labels.get(LABEL_ENVIRONMENT) != expected_environment:
            errors.append(f"LABEL_ENVIRONMENT_MISMATCH:{service}")
        if _digest_of(str(item.get("image") or "")) != expected_digest:
            errors.append(f"IMAGE_DIGEST_MISMATCH:{service}")

    identity = runtime if isinstance(runtime, dict) else {}
    if not identity:
        errors.append("RUNTIME_IDENTITY_MISSING")
    else:
        if identity.get("release_id") != expected_release_id:
            errors.append("RUNTIME_IDENTITY_MISMATCH:release_id")
        if str(identity.get("commit") or "").lower() != expected_commit.lower():
            errors.append("RUNTIME_IDENTITY_MISMATCH:commit")
        if normalise_digest(identity.get("image_digest")) != normalise_digest(expected_digest):
            errors.append("RUNTIME_IDENTITY_MISMATCH:image_digest")
        if str(identity.get("environment") or "").lower() != expected_environment.lower():
            errors.append("RUNTIME_IDENTITY_MISMATCH:environment")
        if not str(identity.get("instance_owner") or "").strip():
            errors.append("RUNTIME_IDENTITY_MISSING:instance_owner")

    live = health.get("live") or {}
    ready = health.get("ready") or {}
    trading = health.get("trading") or {}
    if live.get("status") != 200:
        errors.append("HEALTH_LIVE_NOT_200")
    if ready.get("status") != 200:
        errors.append("HEALTH_READY_NOT_200")
    if trading.get("status") != 200 or not (trading.get("body") or {}).get("ready"):
        errors.append("HEALTH_TRADING_NOT_READY")

    return {
        "schema_version": 1,
        "runtime_inspected": True,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "compose_project": project,
        "environment": expected_environment,
        "release_id": expected_release_id,
        "commit": expected_commit.lower(),
        "image_digest": expected_digest,
        "containers": containers,
        "health": health,
        "runtime": identity,
        "owner_evidence": {
            "source": "health_endpoint",
            "owner": None,
            "fencing_token": None,
            "reason": "the Control health response does not expose lease owner/fencing token",
        },
        "errors": errors,
        "checks": {
            "all_running": not any(
                e.startswith(("CONTAINER_NOT_RUNNING", "CONTAINER_MISSING")) for e in errors
            ),
            "all_healthy": not any(
                e.startswith(("CONTAINER_NOT_HEALTHY", "CONTAINER_MISSING")) for e in errors
            ),
            "labels_match": not any(e.startswith("LABEL_") for e in errors),
            "image_digest_match": not any(e.startswith("IMAGE_DIGEST") for e in errors),
            "trading_ready": trading.get("status") == 200
            and bool((trading.get("body") or {}).get("ready")),
        },
    }


def collect(
    *,
    project: str,
    control_url: str,
    expected_release_id: str,
    expected_commit: str,
    expected_digest: str,
    expected_environment: str = "demo",
) -> dict[str, Any]:
    containers = collect_containers(project)
    health = {
        "live": _http(control_url.rstrip("/") + "/health/live"),
        "ready": _http(control_url.rstrip("/") + "/health/ready"),
        "trading": _http(control_url.rstrip("/") + "/health/trading"),
    }
    return build_binding(
        project=project,
        containers=containers,
        health=health,
        expected_release_id=expected_release_id,
        expected_commit=expected_commit,
        expected_digest=expected_digest,
        expected_environment=expected_environment,
        runtime=runtime_identity_from_health(health),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--control-url", default="http://127.0.0.1:18090")
    parser.add_argument("--expected-release-id", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-digest", required=True)
    parser.add_argument("--expected-environment", default="demo")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not DIGEST_RE.fullmatch(args.expected_digest):
        raise SystemExit("--expected-digest must be sha256:<64 hex>")
    binding = collect(
        project=args.project,
        control_url=args.control_url,
        expected_release_id=args.expected_release_id,
        expected_commit=args.expected_commit,
        expected_digest=args.expected_digest,
        expected_environment=args.expected_environment,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": not binding["errors"], "errors": binding["errors"]}, sort_keys=True))
    return 0 if not binding["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
