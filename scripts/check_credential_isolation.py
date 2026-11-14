"""Prove the Control/Execution exchange-credential boundary.

Only key names and presence booleans are printed. Values are never rendered,
logged or stored, so the check is safe to run in CI and evidence collection.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IN_CONTROL = (
    "BINANCE_API_KEY",
    "BINANCE_SECRET_KEY",
    "BINANCE_API_KEY_FILE",
    "BINANCE_SECRET_KEY_FILE",
)
EXECUTION_KEYS = (
    "BINANCE_API_KEY",
    "BINANCE_SECRET_KEY",
    "BINANCE_API_KEY_FILE",
    "BINANCE_SECRET_KEY_FILE",
)


def _env_keys(service: dict) -> set[str]:
    environment = service.get("environment") or {}
    if isinstance(environment, dict):
        return {str(key) for key in environment}
    keys: set[str] = set()
    for item in environment:
        if isinstance(item, str):
            keys.add(item.split("=", 1)[0])
    return keys


def _env_files(service: dict) -> list[str]:
    value = service.get("env_file") or []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def check_compose(path: Path) -> dict:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    services = document.get("services") or {}
    violations: list[str] = []
    if "control" not in services:
        violations.append("COMPOSE_CONTROL_SERVICE_MISSING")
    if "execution" not in services:
        violations.append("COMPOSE_EXECUTION_SERVICE_MISSING")
    control = services.get("control") or {}
    execution = services.get("execution") or {}
    control_keys = _env_keys(control)
    execution_keys = _env_keys(execution)
    forbidden = sorted(control_keys & set(FORBIDDEN_IN_CONTROL))
    if forbidden:
        violations.append("CONTROL_HAS_EXCHANGE_CREDENTIALS:" + ",".join(forbidden))
    control_env_files = _env_files(control)
    broad = [item for item in control_env_files if item in {".env", "./.env"}]
    if broad:
        violations.append("CONTROL_USES_BROAD_ENV_FILE")
    if not execution_keys & set(EXECUTION_KEYS):
        violations.append("EXECUTION_CREDENTIAL_REFERENCE_MISSING")
    shared = sorted(set(control_env_files) & set(_env_files(execution)))
    if shared:
        violations.append("CONTROL_AND_EXECUTION_SHARE_ENV_FILE")
    secrets = document.get("secrets") or {}
    if not any(name in secrets for name in ("binance_api_key", "binance_secret_key")):
        violations.append("COMPOSE_EXCHANGE_SECRETS_MISSING")
    return {
        "target": str(path),
        "services": sorted(services),
        "control_env_keys": sorted(control_keys),
        "execution_credential_key_names": sorted(execution_keys & set(EXECUTION_KEYS)),
        "violations": violations,
        "ok": not violations,
    }


def check_container(name: str) -> dict:
    raw = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Config.Env}}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if raw.returncode != 0:
        return {
            "target": f"container:{name}",
            "ok": False,
            "violations": [f"CONTAINER_INSPECT_FAILED:{raw.returncode}"],
        }
    entries = json.loads(raw.stdout or "[]")
    keys = {str(item).split("=", 1)[0] for item in entries}
    forbidden = sorted(keys & set(FORBIDDEN_IN_CONTROL))
    violations = ["CONTROL_HAS_EXCHANGE_CREDENTIALS:" + ",".join(forbidden)] if forbidden else []
    return {
        "target": f"container:{name}",
        "env_keys": sorted(keys),
        "violations": violations,
        "ok": not violations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="QTS exchange-credential isolation check")
    parser.add_argument("--compose", type=Path, default=ROOT / "compose.yml")
    parser.add_argument("--control-container", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [check_compose(args.compose)]
    if args.control_container:
        reports.append(check_container(args.control_container))
    result = {"ok": all(report["ok"] for report in reports), "reports": reports}
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
