"""Isolated Compose startup, health, credential and alert smoke drill.

Every resource is namespaced by a task-owned Compose project. The existing
``qts-postgres`` container and ``qts_pgdata`` volume are inspected only; they
are never started, stopped or mounted by this script.
"""

from __future__ import annotations

import argparse
import json
import os
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


def _docker_env(
    base: dict[str, str], project: str, ports: dict[str, int], secrets_dir: Path, evidence_dir: Path
) -> dict[str, str]:
    env = dict(base)
    env.update(
        {
            "QTS_COMPOSE_PROJECT": project,
            "POSTGRES_DB": "trading",
            "POSTGRES_USER": "trader",
            "POSTGRES_PASSWORD": "p5_" + uuid.uuid4().hex,
            "QTS_PG_PORT": str(ports["pg"]),
            "QTS_CONTROL_PORT": str(ports["control"]),
            "QTS_ALERT_SINK_PORT": str(ports["alert"]),
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
        if status in {"healthy", "unhealthy"}:
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


def _existing_resources(env: dict[str, str]) -> dict[str, Any]:
    inspect = _run(["docker", "inspect", "qts-postgres"], env=env, timeout=60)
    container_id = ""
    running = False
    if inspect.returncode == 0:
        payload = json.loads(inspect.stdout)[0]
        container_id = payload.get("Id", "")
        running = bool(payload.get("State", {}).get("Running"))
    volume = _run(
        ["docker", "volume", "inspect", "quant_trading_system_qts_pgdata"],
        env=env,
        timeout=60,
    )
    return {
        "container_id": container_id,
        "running": running,
        "volume_present": volume.returncode == 0,
    }


def run(args) -> dict[str, Any]:
    project = args.project or ("qts_p5_" + uuid.uuid4().hex[:10])
    evidence_dir = args.evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    secrets_dir = Path(tempfile.mkdtemp(prefix="qts-p5-secrets-"))
    # Empty files prove the missing-credential refusal without inventing a
    # usable exchange identity.
    (secrets_dir / "binance_api_key").write_text("", encoding="utf-8")
    (secrets_dir / "binance_secret_key").write_text("", encoding="utf-8")
    pg_port, control_port, alert_port = _free_ports(3)
    ports = {"pg": pg_port, "control": control_port, "alert": alert_port}
    env = _docker_env(os.environ, project, ports, secrets_dir, evidence_dir)
    evidence: dict[str, Any] = {
        "project": project,
        "ports": ports,
        "started_at_ms": int(time.time() * 1000),
        "existing_before": _existing_resources(env),
        "commands": [],
        "passed": False,
    }
    try:
        version = _run(["docker", "compose", "version"], env=env, timeout=60)
        evidence["docker_compose_version"] = version.stdout.strip()
        if version.returncode != 0:
            evidence["reason"] = "DOCKER_COMPOSE_UNAVAILABLE"
            return evidence

        config = _run(
            ["docker", "compose", "-p", project, "config", "--quiet"], env=env, timeout=120
        )
        evidence["commands"].append(
            {
                "argv": ["docker", "compose", "-p", project, "config", "--quiet"],
                "exit_code": config.returncode,
            }
        )
        if config.returncode != 0:
            evidence["reason"] = "COMPOSE_CONFIG_INVALID"
            evidence["stderr"] = config.stderr[-2000:]
            return evidence

        build = _run(
            ["docker", "compose", "-p", project, "build"], env=env, timeout=args.build_timeout
        )
        evidence["commands"].append(
            {"argv": ["docker", "compose", "-p", project, "build"], "exit_code": build.returncode}
        )
        if build.returncode != 0:
            evidence["reason"] = "IMAGE_BUILD_FAILED"
            evidence["stderr"] = build.stderr[-4000:]
            return evidence

        up = _run(
            [
                "docker",
                "compose",
                "-p",
                project,
                "up",
                "-d",
                "postgres",
                "alert-sink",
                "control",
                "research",
                "history",
            ],
            env=env,
            timeout=300,
        )
        evidence["commands"].append(
            {
                "argv": ["docker", "compose", "-p", project, "up", "-d", "..."],
                "exit_code": up.returncode,
            }
        )
        if up.returncode != 0:
            evidence["reason"] = "COMPOSE_UP_FAILED"
            evidence["stderr"] = up.stderr[-4000:]
            return evidence

        names = _container_names(env, project)
        evidence["containers"] = names
        evidence["health"] = {
            service: _wait_health(env, name) for service, name in sorted(names.items())
        }
        evidence["running_services"] = sorted(names)

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
            [
                "docker",
                "compose",
                "-p",
                project,
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                "execution",
                "-c",
                "import json,os; print(json.dumps(sorted(os.environ)))",
            ],
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
            ["docker", "compose", "-p", project, "run", "--rm", "--no-deps", "execution"],
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

        evidence["existing_after"] = _existing_resources(env)
        evidence["existing_resources_untouched"] = (
            evidence["existing_before"]["container_id"]
            == evidence["existing_after"]["container_id"]
            and evidence["existing_after"]["running"]
            and evidence["existing_after"]["volume_present"]
        )
        evidence["passed"] = bool(
            evidence["health_semantics_ok"]
            and not evidence["control_credential_violations"]
            and evidence["execution_credential_reference"]
            and evidence["credential_refusal"]["exit_code"] == 2
            and evidence["credential_refusal"]["reason_code_present"]
            and evidence["alert_delivery_ok"]
            and evidence["existing_resources_untouched"]
        )
        return evidence
    except Exception as exc:
        evidence["reason"] = f"SMOKE_FAILED:{type(exc).__name__}"
        evidence["error"] = str(exc)[-2000:]
        return evidence
    finally:
        down = _run(
            ["docker", "compose", "-p", project, "down", "-v", "--remove-orphans"],
            env=env,
            timeout=180,
        )
        evidence["cleanup_exit_code"] = down.returncode
        evidence["remaining_containers"] = _container_names(env, project)
        evidence["completed_at_ms"] = int(time.time() * 1000)
        shutil.rmtree(secrets_dir, ignore_errors=True)
        (evidence_dir / "compose-smoke.json").write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="QTS isolated Compose smoke drill")
    parser.add_argument("--project", default="")
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=ROOT / "artifacts/foundation/phase5/evidence",
    )
    parser.add_argument("--build-timeout", type=float, default=1800)
    args = parser.parse_args()
    evidence = run(args)
    print(json.dumps({"passed": evidence.get("passed"), "project": evidence.get("project")}))
    raise SystemExit(0 if evidence.get("passed") else 1)


if __name__ == "__main__":
    main()
