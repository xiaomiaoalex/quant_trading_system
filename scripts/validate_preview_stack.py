"""Fail-closed preflight for an isolated preview stack.

This entry point layers the real Compose files, validates every value the
runner is about to export or place in an env file, renders the effective
project, and only then writes a controlled env file for ``compose up``.

It distinguishes registry references from local image IDs.  A recorded
``sha256:<64 hex>`` is an image ID for this host; it is never rewritten as a
registry digest or used to retag an unrelated image.  The rendered config is
kept out of the JSON report because it can contain the operator password
hash.  No service is started and no volume is touched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SCRYPT_RE = re.compile(r"^scrypt\$[0-9a-f]{32}\$[0-9a-f]{128}$")
ORIGIN_RE = re.compile(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$")
ALLOWED_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,60}$")
ENV_KEYS = (
    "QTS_PREVIEW_COMPOSE_PROJECT",
    "QTS_PREVIEW_ID",
    "QTS_PREVIEW_COMMIT",
    "QTS_PREVIEW_ORIGIN",
    "QTS_PREVIEW_HTTP_PORT",
    "QTS_PREVIEW_RUNTIME_IMAGE",
    "QTS_PREVIEW_RUNTIME_IMAGE_ID",
    "QTS_PREVIEW_UI_IMAGE",
    "QTS_PREVIEW_POSTGRES_PASSWORD",
    "QTS_PREVIEW_PASSWORD_HASH",
)
FORBIDDEN_SERVICE_NAMES = ("execution", "backup")
VENUE_SECRET_MARKERS = ("binance", "secret_key", "api_key")


def collect(shell_env: dict[str, str]) -> dict[str, str]:
    """Take preview values only from the runner's explicit environment."""
    return {key: str(shell_env.get(key, "")).strip() for key in ENV_KEYS}


def validate(values: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for key, value in values.items():
        if not value:
            problems.append(f"MISSING_VALUE:{key}")
    if not ALLOWED_PROJECT_RE.fullmatch(values.get("QTS_PREVIEW_COMPOSE_PROJECT", "")):
        problems.append("INVALID_COMPOSE_PROJECT")
    if not COMMIT_RE.fullmatch(values.get("QTS_PREVIEW_COMMIT", "")):
        problems.append("INVALID_COMMIT")
    if not IMAGE_ID_RE.fullmatch(values.get("QTS_PREVIEW_RUNTIME_IMAGE_ID", "")):
        problems.append("INVALID_RUNTIME_IMAGE_ID")
    if not SCRYPT_RE.fullmatch(values.get("QTS_PREVIEW_PASSWORD_HASH", "")):
        problems.append("INVALID_PASSWORD_HASH")
    if not ORIGIN_RE.fullmatch(values.get("QTS_PREVIEW_ORIGIN", "")):
        problems.append("INVALID_LOOPBACK_ORIGIN")
    port = values.get("QTS_PREVIEW_HTTP_PORT", "")
    if port and not (port.isdigit() and 1024 <= int(port) <= 65535):
        problems.append("INVALID_HTTP_PORT")
    runtime = values.get("QTS_PREVIEW_RUNTIME_IMAGE", "")
    if runtime and "@sha256:" in runtime:
        problems.append("REGISTRY_DIGEST_NOT_AN_IMAGE_ID")
    for marker in ("REPLACE_WITH", "CHANGE_ME", "PLACEHOLDER", "TODO", "example.com"):
        if any(marker in value.upper() for value in values.values()):
            problems.append(f"PLACEHOLDER_VALUE:{marker}")
    return sorted(set(problems))


def _docker(*argv: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *argv],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=120,
        check=False,
    )


def inspect_local_image(reference: str, env: dict[str, str]) -> str:
    result = _docker("image", "inspect", reference, "--format", "{{.Id}}", env=env)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def check_artifacts(values: dict[str, str], env: dict[str, str]) -> list[str]:
    problems: list[str] = []
    recorded = values.get("QTS_PREVIEW_RUNTIME_IMAGE_ID", "")
    runtime = values.get("QTS_PREVIEW_RUNTIME_IMAGE", "")
    if recorded and runtime:
        actual = inspect_local_image(runtime, env)
        if not actual:
            problems.append("RUNTIME_IMAGE_NOT_PRESENT")
        elif actual != recorded:
            problems.append("RUNTIME_IMAGE_ID_MISMATCH")
    ui = values.get("QTS_PREVIEW_UI_IMAGE", "")
    if ui and not inspect_local_image(ui, env):
        problems.append("UI_IMAGE_NOT_PRESENT")
    return problems


def render(
    compose_files: list[str], project: str, values: dict[str, str], env: dict[str, str]
) -> tuple[dict[str, Any] | None, list[str]]:
    argv: list[str] = ["docker", "compose"]
    for path in compose_files:
        argv.extend(["-f", path])
    argv.extend(["-p", project, "config", "--format", "json"])
    result = subprocess.run(
        argv,
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        return None, [f"COMPOSE_CONFIG_FAILED: {(result.stderr or result.stdout).strip()[:500]}"]
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, [f"COMPOSE_CONFIG_INVALID_JSON: {exc}"]
    if not isinstance(payload, dict):
        return None, ["COMPOSE_CONFIG_INVALID_TYPE"]
    return payload, []


def evaluate_rendered(
    payload: dict[str, Any], values: dict[str, str], env: dict[str, str]
) -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    services = payload.get("services")
    if not isinstance(services, dict) or not services:
        return ["COMPOSE_NO_SERVICES"], {}
    expected_commit = values["QTS_PREVIEW_COMMIT"]
    expected_id = values["QTS_PREVIEW_RUNTIME_IMAGE_ID"]
    expected_runtime = values["QTS_PREVIEW_RUNTIME_IMAGE"]
    summary: dict[str, Any] = {
        "services": sorted(services),
        "published_ports": [],
        "build_paths": [],
        "venue_secret_names": [],
        "image_bindings_match": False,
        "commit_labels_match": False,
    }
    for name, service in sorted(services.items()):
        if not isinstance(service, dict):
            problems.append(f"INVALID_SERVICE:{name}")
            continue
        if name in FORBIDDEN_SERVICE_NAMES:
            problems.append(f"FORBIDDEN_SERVICE:{name}")
        if service.get("build") not in (None, {}):
            summary["build_paths"].append(name)
            problems.append(f"RESIDUAL_BUILD_PATH:{name}")
        labels = service.get("labels") or {}
        if not isinstance(labels, dict):
            problems.append(f"INVALID_LABELS:{name}")
            labels = {}
        for key, expected in (
            ("com.qts.preview.runtime_image_id", expected_id),
            ("com.qts.preview.commit", expected_commit),
        ):
            if key in labels:
                if labels[key] != expected:
                    problems.append(f"LABEL_MISMATCH:{name}:{key}")
            elif name not in ("postgres", "web"):
                problems.append(f"LABEL_MISSING:{name}:{key}")
        for port in service.get("ports") or []:
            host_ip = port.get("host_ip", "") if isinstance(port, dict) else ""
            published = port.get("published", "") if isinstance(port, dict) else str(port)
            summary["published_ports"].append(
                {"service": name, "host_ip": host_ip, "published": str(published)}
            )
            if host_ip not in ("127.0.0.1", "::1"):
                problems.append(f"NON_LOOPBACK_PORT:{name}")
        image = str(service.get("image") or "")
        if image.startswith(f"{values['QTS_PREVIEW_RUNTIME_IMAGE']}@sha256:"):
            problems.append(f"IMAGE_ID_RETAG:{name}")
    bound_images = [
        service.get("image")
        for service in services.values()
        if isinstance(service, dict)
        and (service.get("labels") or {}).get("com.qts.preview.runtime_image_id")
    ]
    summary["image_bindings_match"] = bool(bound_images) and all(
        image == expected_runtime for image in bound_images
    )
    summary["commit_labels_match"] = all(
        (service.get("labels") or {}).get("com.qts.preview.commit") == expected_commit
        for service in services.values()
        if isinstance(service, dict)
        and (service.get("labels") or {}).get("com.qts.preview.commit") is not None
    )
    if not summary["image_bindings_match"]:
        problems.append("IMAGE_BINDINGS_MISMATCH")
    secrets = payload.get("secrets")
    if isinstance(secrets, dict):
        summary["venue_secret_names"] = sorted(
            name
            for name in secrets
            if any(marker in str(name).lower() for marker in VENUE_SECRET_MARKERS)
        )
        if summary["venue_secret_names"]:
            problems.append("VENUE_SECRETS_PRESENT")
    elif secrets not in (None, []):
        problems.append("INVALID_SECRETS_SECTION")
    return problems, summary


def write_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized: list[str] = []
    for key, value in sorted(values.items()):
        # Compose expands $NAME in unquoted and double-quoted dotenv values.
        # A scrypt hash contains two $ separators and must stay literal.
        rendered = f"'{value}'" if "$" in value else value
        serialized.append(f"{key}={rendered}")
    lines = [
        f"# Generated by validate_preview_stack.py; no secrets in this report.",
        *serialized,
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", action="append", required=True)
    parser.add_argument("--env-out", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument(
        "--skip-docker",
        action="store_true",
        help="contract-only mode for platforms without a Docker daemon",
    )
    args = parser.parse_args(argv)

    values = collect(dict(os.environ))
    problems = validate(values)
    env = dict(os.environ)
    env.update(values)
    summary: dict[str, Any] = {"compose_files": args.compose_file, "services": []}
    if not problems and not args.skip_docker:
        problems.extend(check_artifacts(values, env))
        payload, render_problems = render(
            args.compose_file, values["QTS_PREVIEW_COMPOSE_PROJECT"], values, env
        )
        problems.extend(render_problems)
        if payload is not None:
            rendered_problems, rendered_summary = evaluate_rendered(payload, values, env)
            problems.extend(rendered_problems)
            summary.update(rendered_summary)
    elif args.skip_docker:
        summary["docker_checks"] = "SKIPPED:--skip-docker"

    result = {
        "ok": not problems,
        "problems": sorted(set(problems)),
        "summary": summary,
        "source": {
            "head": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip(),
            "revision_env": values["QTS_PREVIEW_COMMIT"],
        },
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"ok": result["ok"], "problems": result["problems"], "report": str(args.json_out)}
        )
    )
    if result["ok"] and not args.skip_docker:
        write_env(args.env_out, values)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
