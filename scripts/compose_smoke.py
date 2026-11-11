"""Isolated Compose startup, health, credential and alert smoke drill.

Every resource is namespaced by a task-owned Compose project. The existing
``qts-postgres`` container and ``qts_pgdata`` volume are inspected only; they
are never started, stopped or mounted by this script.

Two modes are supported:

* ``--image ghcr.io/<owner>/<image>@sha256:<digest>`` starts the exact built
  artifact without rebuilding (the release path);
* without ``--image`` the local development image is built (``qts-runtime:local``).

The drill passes only when every started container is ``healthy``, the
credential and alert semantics hold, the pre-existing resources have exactly
the same identity after the drill (including "still absent"), and the
task-owned containers and volumes were removed by cleanup.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from http.client import RemoteDisconnected
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IN_CONTROL = {
    "BINANCE_API_KEY",
    "BINANCE_SECRET_KEY",
    "BINANCE_API_KEY_FILE",
    "BINANCE_SECRET_KEY_FILE",
}
EXISTING_CONTAINER = "qts-postgres"
EXISTING_VOLUME = "quant_trading_system_qts_pgdata"
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
STARTED_SERVICES = ("postgres", "alert-sink", "control", "research", "history")


def _run(
    argv: list[str],
    *,
    env: dict[str, str],
    cwd: Path = ROOT,
    timeout: float = 600,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def compose_argv(compose_files: list[str], project: str, *args: str) -> list[str]:
    """Build a Compose argv with every overlay file in order."""
    argv = ["docker", "compose"]
    for path in compose_files:
        argv.extend(["-f", str(path)])
    argv.extend(["-p", project, *args])
    return argv


def _docker_env(
    base: dict[str, str],
    project: str,
    ports: dict[str, int],
    secrets_dir: Path,
    evidence_dir: Path,
    image: str,
) -> dict[str, str]:
    env = dict(base)
    env.update(
        {
            "QTS_COMPOSE_PROJECT": project,
            "QTS_IMAGE": image,
            "POSTGRES_DB": "trading",
            "POSTGRES_USER": "trader",
            "POSTGRES_PASSWORD": "p5_" + uuid.uuid4().hex,
            "QTS_PG_PORT": str(ports["pg"]),
            "QTS_CONTROL_PORT": str(ports["control"]),
            "QTS_ALERT_SINK_PORT": str(ports["alert"]),
            # The Demo overlay uses dedicated variable names; keep both names
            # pointed at the same task-owned free ports.
            "QTS_DEMO_PG_PORT": str(ports["pg"]),
            "QTS_DEMO_CONTROL_PORT": str(ports["control"]),
            "QTS_DEMO_ALERT_PORT": str(ports["alert"]),
            "QTS_PG_BIND": "127.0.0.1",
            "QTS_CONTROL_BIND": "127.0.0.1",
            "QTS_ALERT_SINK_BIND": "127.0.0.1",
            "BINANCE_API_KEY_FILE_PATH": str(secrets_dir / "binance_api_key"),
            "BINANCE_SECRET_KEY_FILE_PATH": str(secrets_dir / "binance_secret_key"),
            "QTS_DR_EVIDENCE_DIR": str(evidence_dir),
        }
    )
    return env


def _free_ports(count: int) -> list[int]:
    import socket

    sockets = []
    try:
        for _ in range(count):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [int(sock.getsockname()[1]) for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def _http(url: str, timeout: float = 3.0, attempts: int = 8) -> tuple[int, dict[str, Any]]:
    # Bypass any host/system HTTP proxy for the task-published localhost port;
    # a proxy closing the connection would otherwise look like a service fault.
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
                return response.status, payload
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = {"error": type(exc).__name__}
            return int(exc.code), payload
        except (URLError, RemoteDisconnected, ConnectionError) as exc:
            last_error = type(exc).__name__
            if attempt < attempts:
                time.sleep(1)
    return 0, {"error": last_error or "HTTP_UNAVAILABLE"}


def _container_names(env: dict[str, str], project: str) -> dict[str, str]:
    result = _run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            '{{.Names}}\t{{.Label "com.docker.compose.service"}}',
        ],
        env=env,
        timeout=60,
    )
    names: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            names[parts[1]] = parts[0]
    return names


def _health_status(env: dict[str, str], container: str) -> str:
    result = _run(
        [
            "docker",
            "inspect",
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            container,
        ],
        env=env,
        timeout=60,
    )
    return result.stdout.strip()


def _wait_health(env: dict[str, str], container: str, timeout: float = 120) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = _health_status(env, container)
        if status in {"healthy", "unhealthy", "none"}:
            return status
        time.sleep(1)
    return "timeout"


def _env_keys(env: dict[str, str], container: str) -> list[str]:
    result = _run(
        ["docker", "inspect", "--format", "{{json .Config.Env}}", container],
        env=env,
        timeout=60,
    )
    if result.returncode != 0:
        return []
    values = json.loads(result.stdout or "[]")
    return sorted({str(item).split("=", 1)[0] for item in values})


def _container_identity(env: dict[str, str]) -> dict[str, Any]:
    result = _run(["docker", "inspect", EXISTING_CONTAINER], env=env, timeout=60)
    if result.returncode != 0:
        return {"present": False}
    payload = json.loads(result.stdout)[0]
    return {
        "present": True,
        "id": payload.get("Id", ""),
        "running": bool(payload.get("State", {}).get("Running")),
        "started_at": payload.get("State", {}).get("StartedAt", ""),
    }


def _volume_identity(env: dict[str, str], name: str) -> dict[str, Any]:
    result = _run(["docker", "volume", "inspect", name], env=env, timeout=60)
    if result.returncode != 0:
        return {"present": False}
    payload = json.loads(result.stdout)[0]
    return {
        "present": True,
        "name": payload.get("Name", name),
        "driver": payload.get("Driver", ""),
        "created_at": payload.get("CreatedAt", ""),
        "mountpoint": payload.get("Mountpoint", ""),
    }


def _compose_volume_names(env: dict[str, str], project: str) -> dict[str, dict[str, Any]]:
    result = _run(
        [
            "docker",
            "volume",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.Name}}",
        ],
        env=env,
        timeout=60,
    )
    return {name: _volume_identity(env, name) for name in sorted(result.stdout.split()) if name}


def resource_identity(env: dict[str, str], project: str) -> dict[str, Any]:
    """Identity of resources that must survive the drill unchanged.

    Absence is recorded explicitly, so a resource that did not exist before
    the drill cannot silently appear afterwards (and vice versa).
    """
    return {
        "container": _container_identity(env),
        "volume": _volume_identity(env, EXISTING_VOLUME),
        "compose_volumes": _compose_volume_names(env, project),
    }


def evaluate_evidence(evidence: dict[str, Any]) -> list[str]:
    """Return every reason the drill must be considered failed.

    Kept pure so the release gate can be regression-tested without Docker.
    """
    reasons: list[str] = []
    early = evidence.get("reason")
    if early:
        reasons.append(str(early))

    health = evidence.get("health") or {}
    expected = set(evidence.get("started_containers_expected") or [])
    for service in sorted(expected):
        status = health.get(service)
        if status is None:
            reasons.append(f"CONTAINER_MISSING_HEALTH:{service}")
        elif status != "healthy":
            reasons.append(f"CONTAINER_NOT_HEALTHY:{service}:{status}")

    if evidence.get("health_semantics_ok") is not True:
        reasons.append("HEALTH_SEMANTICS_FAILED")
    violations = evidence.get("control_credential_violations") or []
    if violations:
        reasons.append(f"CONTROL_HAS_EXCHANGE_CREDENTIALS:{','.join(sorted(violations))}")
    if not evidence.get("execution_credential_reference"):
        reasons.append("EXECUTION_CREDENTIAL_REFERENCE_MISSING")
    refusal = evidence.get("credential_refusal") or {}
    if refusal.get("exit_code") != 2 or refusal.get("reason_code_present") is not True:
        reasons.append("EXECUTION_CREDENTIAL_REFUSAL_FAILED")
    non_demo = evidence.get("non_demo_refusal") or {}
    if non_demo.get("exit_code") != 2 or non_demo.get("reason_code_present") is not True:
        reasons.append("NON_DEMO_REFUSAL_FAILED")
    if evidence.get("alert_delivery_ok") is not True:
        reasons.append("ALERT_DELIVERY_FAILED")
    if evidence.get("existing_resources_untouched") is not True:
        reasons.append("EXISTING_RESOURCES_CHANGED")
    if evidence.get("image_digest_ok") is not True:
        reasons.append("IMAGE_DIGEST_INVALID")
    cleanup_exit = evidence.get("cleanup_exit_code")
    if cleanup_exit != 0:
        reasons.append(f"CLEANUP_FAILED:{cleanup_exit}")
    remaining = evidence.get("remaining_containers") or {}
    if remaining:
        reasons.append(f"CONTAINERS_LEAKED:{','.join(sorted(remaining))}")
    remaining_volumes = evidence.get("remaining_volumes") or {}
    if remaining_volumes:
        reasons.append(f"VOLUMES_LEAKED:{','.join(sorted(remaining_volumes))}")
    return reasons


def run(args) -> dict[str, Any]:
    project = args.project or ("qts_p5_" + uuid.uuid4().hex[:10])
    compose_files = list(args.compose_file or ["compose.yml"])
    evidence_dir = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    secrets_dir = Path(tempfile.mkdtemp(prefix="qts-p5-secrets-"))
    # Empty files prove the missing-credential refusal without inventing a
    # usable exchange identity.
    (secrets_dir / "binance_api_key").write_text("", encoding="utf-8")
    (secrets_dir / "binance_secret_key").write_text("", encoding="utf-8")
    pg_port, control_port, alert_port = _free_ports(3)
    ports = {"pg": pg_port, "control": control_port, "alert": alert_port}
    image = args.image or "qts-runtime:local"
    env = _docker_env(dict(os.environ), project, ports, secrets_dir, evidence_dir, image)
    image_digest_ok = True
    if args.require_digest:
        image_digest_ok = bool(args.image and DIGEST_RE.search(args.image))
    evidence: dict[str, Any] = {
        "project": project,
        "compose_files": compose_files,
        "image": image,
        "require_digest": bool(args.require_digest),
        "image_digest_ok": image_digest_ok,
        "ports": ports,
        "started_at_ms": int(time.time() * 1000),
        "existing_before": resource_identity(env, project),
        "commands": [],
        "passed": False,
    }
    try:
        version = _run(["docker", "compose", "version"], env=env, timeout=60)
        evidence["docker_compose_version"] = version.stdout.strip()
        if version.returncode != 0:
            evidence["reason"] = "DOCKER_COMPOSE_UNAVAILABLE"
            return evidence

        config_argv = compose_argv(compose_files, project, "config", "--quiet")
        config = _run(config_argv, env=env, timeout=120)
        evidence["commands"].append({"argv": config_argv, "exit_code": config.returncode})
        if config.returncode != 0:
            evidence["reason"] = "COMPOSE_CONFIG_INVALID"
            evidence["stderr"] = config.stderr[-2000:]
            return evidence

        if not args.image:
            build_argv = compose_argv(compose_files, project, "build")
            build = _run(build_argv, env=env, timeout=args.build_timeout)
            evidence["commands"].append({"argv": build_argv, "exit_code": build.returncode})
            if build.returncode != 0:
                evidence["reason"] = "IMAGE_BUILD_FAILED"
                evidence["stderr"] = build.stderr[-4000:]
                return evidence

        up_argv = compose_argv(compose_files, project, "up", "-d", "--no-build", *STARTED_SERVICES)
        up = _run(up_argv, env=env, timeout=300)
        evidence["commands"].append({"argv": up_argv, "exit_code": up.returncode})
        if up.returncode != 0:
            evidence["reason"] = "COMPOSE_UP_FAILED"
            evidence["stderr"] = up.stderr[-4000:]
            return evidence

        names = _container_names(env, project)
        evidence["containers"] = names
        evidence["started_containers_expected"] = sorted(names)
        evidence["health"] = {
            service: _wait_health(env, name, timeout=args.startup_timeout)
            for service, name in sorted(names.items())
        }

        control_port = ports["control"]
        live_status, live = _http(f"http://127.0.0.1:{control_port}/health/live")
        ready_status, ready = _http(f"http://127.0.0.1:{control_port}/health/ready")
        trading_status, trading = _http(f"http://127.0.0.1:{control_port}/health/trading")
        evidence["health_endpoints"] = {
            "live": {"status": live_status, "body": live},
            "ready": {"status": ready_status, "body": ready},
            "trading": {"status": trading_status, "body": trading},
        }
        evidence["health_semantics_ok"] = (
            live_status == 200
            and ready_status == 200
            and trading_status == 503
            and "DR_EVIDENCE_MISSING" in (trading.get("reasons") or [])
        )

        control_keys = _env_keys(env, names.get("control", ""))
        evidence["control_env_keys"] = control_keys
        evidence["control_credential_violations"] = sorted(set(control_keys) & FORBIDDEN_IN_CONTROL)

        execution_probe = _run(
            compose_argv(
                compose_files,
                project,
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "execution",
                "-c",
                "import json,os; print(json.dumps(sorted(os.environ)))",
            ),
            env=env,
            timeout=120,
        )
        execution_keys: list[str] = []
        if execution_probe.returncode == 0:
            try:
                execution_keys = json.loads(execution_probe.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                execution_keys = []
        evidence["execution_env_keys"] = execution_keys
        evidence["execution_credential_reference"] = sorted(
            set(execution_keys) & FORBIDDEN_IN_CONTROL
        )

        # Empty Docker secrets must make the real Execution entry point refuse
        # to start with a stable reason code and a non-zero exit.
        refusal = _run(
            compose_argv(compose_files, project, "run", "--rm", "--no-deps", "execution"),
            env=env,
            timeout=120,
        )
        evidence["credential_refusal"] = {
            "exit_code": refusal.returncode,
            "stdout_tail": refusal.stdout[-1000:],
            "stderr_tail": refusal.stderr[-1000:],
            "reason_code_present": "BINANCE_CREDENTIALS_MISSING"
            in (refusal.stdout + refusal.stderr),
        }

        # A non-Demo environment must be refused before any exchange action.
        non_demo = _run(
            compose_argv(
                compose_files,
                project,
                "run",
                "--rm",
                "--no-deps",
                "-e",
                "BINANCE_ENV=production",
                "execution",
            ),
            env=env,
            timeout=120,
        )
        evidence["non_demo_refusal"] = {
            "exit_code": non_demo.returncode,
            "stdout_tail": non_demo.stdout[-1000:],
            "stderr_tail": non_demo.stderr[-1000:],
            "reason_code_present": "BINANCE_ENV_NOT_SANDBOX" in (non_demo.stdout + non_demo.stderr),
        }

        # The alert sink must observe heartbeats and the missing-execution
        # lease blocker from the running Control container.
        alert_status: dict[str, Any] = {}
        alert_deadline = time.time() + 45
        while time.time() < alert_deadline:
            status, payload = _http(f"http://127.0.0.1:{ports['alert']}/status")
            if status == 200:
                alert_status = payload
                if (
                    payload.get("heartbeats_received", 0) > 0
                    and payload.get("events_received", 0) > 0
                ):
                    break
            time.sleep(2)
        evidence["alert_sink_status"] = alert_status
        evidence["alert_delivery_ok"] = bool(
            alert_status.get("heartbeats_received", 0) > 0
            and alert_status.get("events_received", 0) > 0
            and "EXECUTION_LEASE_LOST" in (alert_status.get("codes") or [])
        )
        return evidence
    except Exception as exc:
        evidence["reason"] = f"SMOKE_FAILED:{type(exc).__name__}"
        evidence["error"] = str(exc)[-2000:]
        return evidence
    finally:
        down = _run(
            compose_argv(compose_files, project, "down", "-v", "--remove-orphans"),
            env=env,
            timeout=180,
        )
        evidence["cleanup_exit_code"] = down.returncode
        evidence["remaining_containers"] = _container_names(env, project)
        evidence["remaining_volumes"] = _compose_volume_names(env, project)
        evidence["existing_after"] = resource_identity(env, project)
        evidence["existing_resources_untouched"] = (
            evidence["existing_before"] == evidence["existing_after"]
        )
        evidence["reasons"] = evaluate_evidence(evidence)
        evidence["passed"] = not evidence["reasons"]
        evidence["completed_at_ms"] = int(time.time() * 1000)
        shutil.rmtree(secrets_dir, ignore_errors=True)
        (evidence_dir / "compose-smoke.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="QTS isolated Compose smoke drill")
    parser.add_argument("--project", default="")
    parser.add_argument(
        "--image",
        default="",
        help="Prebuilt image reference; when set the drill never builds",
    )
    parser.add_argument(
        "--require-digest",
        action="store_true",
        help="Fail unless --image is pinned to an immutable sha256 digest",
    )
    parser.add_argument(
        "--compose-file",
        action="append",
        default=None,
        help="Compose file to layer in order (default: compose.yml); repeat for overlays",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=ROOT / "artifacts/foundation/phase5/evidence",
    )
    parser.add_argument("--build-timeout", type=float, default=1800)
    parser.add_argument("--startup-timeout", type=float, default=180)
    args = parser.parse_args()
    evidence = run(args)
    print(
        json.dumps(
            {
                "passed": evidence.get("passed"),
                "project": evidence.get("project"),
                "reasons": evidence.get("reasons", []),
            }
        )
    )
    raise SystemExit(0 if evidence.get("passed") else 1)


if __name__ == "__main__":
    main()
