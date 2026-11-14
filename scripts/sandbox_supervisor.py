"""Repeatable >=24h non-real-funds sandbox window with continuous DR evidence.

What it does (all task-owned resources):

1. Preflight: verifies ``BINANCE_ENV`` is a supported sandbox, proves the demo
   endpoint/account identity by API behaviour, checks the account has no open or
   recently traded foreign orders for the configured symbols, checks Docker and
   the designated off-host backup target.
2. Starts an isolated Compose project (unique project name, ports, volumes).
3. Runs a DR feeder: every cycle performs a real pg_dump to the off-host S3
   target, restores that exact object into a separate database, verifies the
   fact digests/invariants, and atomically publishes ``dr-evidence.json``. This
   keeps the runtime's RPO evidence genuinely fresh for the whole window.
4. Starts the execution process only after the first valid DR evidence exists,
   waits for ``/health/trading`` and allocates the strategy book explicitly.
5. Runs ``scripts/observe_trading_runtime.py`` for the whole window and records
   its own supervisor journal, transitions, restart counts and final verdict in
   ``window.json``.
6. Never prints or writes exchange/database/operator secrets. The remote S3
   credential is read over the existing strict SSH connection and kept in
   memory.

A host crash or supervision restart is recorded as ``window_aborted``; the
window is never retroactively called continuous.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from trader.api.auth import hash_password  # noqa: E402
from trader.ops.backup import (  # noqa: E402
    BackupConfig,
    ContainerPgDumpRunner,
    backup_once,
    summarize_rpo,
)
from trader.ops.dr_evidence import evaluate_dr_evidence_file  # noqa: E402
from trader.ops.restore import (  # noqa: E402
    ContainerPgRestoreRunner,
    RestoreConfig,
    restore_and_verify,
    write_dr_evidence,
)
from trader.ops.s3 import S3Config, S3Target  # noqa: E402
from trader.ops.ssh_target import (  # noqa: E402
    RemoteTargetNotConfigured,
    resolve_host_key_alias,
    resolve_remote_host,
    ssh_base_argv,
)
from trader.ops.startup_guard import (  # noqa: E402
    GuardHeldError,
    OrphanCheckUnavailable,
    OrphanDetectedError,
    StartupGuard,
)


def _run(argv: list[str], *, timeout: float = 120, env: dict | None = None):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)


def _checked(argv: list[str], *, timeout: float = 120, env: dict | None = None) -> str:
    result = _run(argv, timeout=timeout, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"COMMAND_FAILED:{argv[0]}:{result.returncode}:{result.stderr[-500:]}")
    return result.stdout


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, timeout: float, *, expect: int = 200) -> dict:
    import httpx

    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=5.0)
            last = {"status_code": response.status_code, "body": response.text[:500]}
            if response.status_code == expect:
                return last
        except Exception as exc:
            last = {"error": f"{type(exc).__name__}: {exc}"}
        time.sleep(2.0)
    return last or {"error": "TIMEOUT"}


def _ssh_checked(
    destination: str,
    script: str,
    *,
    timeout: float = 120,
    host_key_alias: str = "",
) -> str:
    payload = (script.rstrip("\n") + "\n").encode("utf-8")
    result = subprocess.run(
        ssh_base_argv(host_key_alias) + [destination, "sh", "-s"],
        input=payload,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError("SSH_COMMAND_FAILED:" + result.stderr.decode("utf-8", "replace")[-500:])
    return result.stdout.decode("utf-8", "replace")


def _remote_probe(
    host: str,
    user: str,
    expected_hostname: str,
    *,
    host_key_alias: str = "",
) -> dict[str, Any]:
    destination = f"{user}@{host}"
    output = _ssh_checked(
        destination,
        """
set -eu
printf 'hostname='; hostname
printf 'user='; id -un
printf 'rclone='; docker ps --format '{{.Names}}' | grep -i rclone | head -1 || true
printf 'port='; docker ps --format '{{.Names}} {{.Ports}}' | grep -i rclone | sed -n 's/.*127.0.0.1:\\([0-9][0-9]*\\)->9000.*/\\1/p' | head -1
printf 'secret='; test -r "$HOME/qts-backups/secrets/rclone.env" && echo yes || echo no
""",
        host_key_alias=host_key_alias,
    )
    parsed = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            parsed[key.strip()] = value.strip()
    if parsed.get("hostname") != expected_hostname:
        raise RuntimeError("REMOTE_HOSTNAME_MISMATCH:" + str(parsed.get("hostname")))
    if not parsed.get("rclone") or not parsed.get("port"):
        raise RuntimeError("REMOTE_RCLONE_NOT_RUNNING")
    if parsed.get("secret") != "yes":
        raise RuntimeError("REMOTE_RCLONE_SECRET_UNREADABLE")
    parsed["destination"] = destination
    return parsed


def _remote_credentials(destination: str, *, host_key_alias: str = "") -> tuple[str, str]:
    output = _ssh_checked(
        destination,
        'cat "$HOME/qts-backups/secrets/rclone.env"',
        host_key_alias=host_key_alias,
    )
    match = re.search(r'RCLONE_AUTH_KEY="?([^",\s]+),([^"\s]+)"?', output)
    if not match:
        raise RuntimeError("REMOTE_RCLONE_CREDENTIAL_INVALID")
    return match.group(1), match.group(2)


def _start_tunnel(
    destination: str,
    remote_port: int,
    *,
    host_key_alias: str = "",
) -> tuple[subprocess.Popen, int]:
    local_port = _free_port()
    diagnostics = Path(tempfile.gettempdir()) / f"qts-tunnel-{local_port}.log"
    handle = diagnostics.open("wb")
    process = subprocess.Popen(
        ssh_base_argv(host_key_alias)
        + [
            "-N",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=30",
            "-L",
            f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
            destination,
        ],
        stdout=subprocess.DEVNULL,
        stderr=handle,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=2):
                handle.close()
                with suppress(OSError):
                    diagnostics.unlink()
                return process, local_port
        except OSError:
            if process.poll() is not None:
                break
            time.sleep(0.5)
    process.terminate()
    with suppress(Exception):
        handle.close()
    tail = ""
    with suppress(OSError):
        tail = _sanitize_diagnostic(diagnostics.read_text(encoding="utf-8", errors="replace"))
        diagnostics.unlink()
    raise RuntimeError("SSH_TUNNEL_NOT_READY:" + tail)


def _sanitize_diagnostic(text: str, *, secrets: tuple[str, ...] = (), limit: int = 400) -> str:
    """Keep a short, redacted diagnostic tail instead of discarding it."""
    cleaned = " ".join(str(text).split())
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "<redacted>")
    cleaned = re.sub(
        r"(?i)(secret|token|password|access[_-]?key)[=:]\S+", r"\1=<redacted>", cleaned
    )
    return cleaned[-limit:]


def _s3_client(config: S3Config):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        region_name=config.region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _assert_task_target(config: S3Config) -> None:
    """The object store target must be this task's own bucket and prefix."""
    host = (urlparse(config.endpoint_url).hostname or "").strip().lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("S3_ENDPOINT_NOT_TASK_LOOPBACK:" + _sanitize_diagnostic(host))
    if not str(config.bucket).startswith("qts-"):
        raise RuntimeError("S3_BUCKET_NOT_TASK_TARGET:" + _sanitize_diagnostic(config.bucket))
    if not str(config.prefix).startswith("phase6/"):
        raise RuntimeError("S3_PREFIX_NOT_TASK_TARGET:" + _sanitize_diagnostic(config.prefix))


def _probe_bucket(config: S3Config) -> None:
    """Prove the object store works at the application level.

    A live SSH process and a listening local port only prove the transport
    started; the task needs a real object-store operation to succeed.
    """
    client = _s3_client(config)
    try:
        client.list_objects_v2(Bucket=config.bucket, Prefix=config.prefix, MaxKeys=1)
    except Exception as exc:
        raise RuntimeError(
            "S3_READBACK_FAILED:"
            + _sanitize_diagnostic(
                f"{type(exc).__name__}: {exc}",
                secrets=(config.access_key, config.secret_key),
            )
        ) from None


def _create_bucket(config: S3Config) -> None:
    """Create the task bucket idempotently without swallowing real failures.

    A reconnect finds the verified task bucket already present. That is only
    accepted when the target is this task's own bucket and our credentials can
    actually reach it. Authorization, ownership, host-key and integrity
    failures are never suppressed.
    """
    client = _s3_client(config)
    try:
        client.create_bucket(Bucket=config.bucket)
        return
    except client.exceptions.BucketAlreadyOwnedByYou:
        return
    except client.exceptions.BucketAlreadyExists:
        _assert_task_target(config)
        try:
            client.head_bucket(Bucket=config.bucket)
        except Exception as exc:
            raise RuntimeError(
                "BUCKET_EXISTING_BUT_NOT_ACCESSIBLE:"
                + _sanitize_diagnostic(
                    f"{type(exc).__name__}: {exc}",
                    secrets=(config.access_key, config.secret_key),
                )
            ) from None
        return


def _container_id(project: str, service: str) -> str:
    """Return the container *name* (a 64-char ID fails the backup name gate)."""
    output = _checked(["docker", "compose", "-p", project, "ps", "-q", service]).strip()
    if not output:
        raise RuntimeError(f"COMPOSE_SERVICE_NOT_RUNNING:{service}")
    container_id = output.splitlines()[0].strip()
    name = _checked(["docker", "inspect", "-f", "{{.Name}}", container_id]).strip()
    return name.lstrip("/")


def _restart_count(container_id: str) -> int:
    output = _checked(["docker", "inspect", "-f", "{{.RestartCount}}", container_id]).strip()
    return int(output or "0")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


class DrFeeder:
    """Backup -> restore that exact object -> verified DR evidence, on a loop."""

    def __init__(self, args, context: dict[str, Any]):
        self.args = args
        self.context = context
        self.evidence_path: Path = context["dr_evidence_path"]
        self.journal_path: Path = context["dr_journal_path"]
        self.backup_dir: Path = context["backup_dir"]
        self.source_dsn: str = context["source_dsn"]
        self.container: str = context["postgres_container"]
        self.s3_config: S3Config = context["s3_config"]
        self.database = context["database"]
        self.user = args.pg_user
        self.state: dict[str, Any] = {"cycles": 0, "successes": 0, "failures": 0, "last": None}

    def _backup_config(self) -> BackupConfig:
        return BackupConfig(
            dsn=self.source_dsn,
            output_dir=self.backup_dir,
            interval_seconds=self.args.dr_cycle_seconds,
            s3=self.s3_config,
            off_host_attested=True,
            attested_by=(
                f"user-designated {self.context['ssh_destination']}; "
                "StrictHostKeyChecking=yes; remote hostname verified"
            ),
            target_kind="s3_compatible",
            transport="ssh_tunnel",
            remote_host=self.args.remote_host,
            ssh_host_key_verified=True,
        )

    async def cycle(self) -> dict[str, Any]:
        started = int(time.time() * 1000)
        cycle_id = f"dr-{self.state['cycles'] + 1:05d}"
        record: dict[str, Any] = {"cycle_id": cycle_id, "started_at_ms": started}
        try:
            backup = await backup_once(
                self._backup_config(),
                dump_runner=ContainerPgDumpRunner(
                    self.container, database=self.database, user=self.user
                ),
                s3_target=S3Target(self.s3_config),
            )
            record["backup_status"] = backup.get("status")
            record["backup_sha256"] = (backup.get("backup") or {}).get("sha256")
            if backup.get("status") != "succeeded":
                raise RuntimeError("BACKUP_FAILED:" + str(backup.get("reason")))
            target_database = f"qts_p6_rst_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            restore = await restore_and_verify(
                RestoreConfig(
                    dsn=self.source_dsn,
                    manifest_path=self.backup_dir / "latest-backup.json",
                    target_database=target_database,
                    output_dir=self.context["restore_dir"],
                    drop_after=True,
                ),
                restore_runner=ContainerPgRestoreRunner(
                    self.container, target_database, user=self.user, source_db=self.database
                ),
                s3_target=S3Target(self.s3_config),
            )
            write_dr_evidence(restore, self.evidence_path)
            gate = evaluate_dr_evidence_file(self.evidence_path)
            record.update(
                {
                    "status": "succeeded" if gate.ready else "gate_failed",
                    "restore_database": target_database,
                    "rto_seconds": (restore.get("restore") or {}).get("rto_seconds"),
                    "verified": (restore.get("restore") or {}).get("verified"),
                    "gate_ready": gate.ready,
                    "gate_reasons": list(gate.reasons),
                    "backup_age_seconds": gate.backup_age_seconds,
                }
            )
            if not gate.ready:
                raise RuntimeError("DR_GATE_NOT_READY:" + ",".join(gate.reasons))
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"[-1200:]
        record["completed_at_ms"] = int(time.time() * 1000)
        self.state["cycles"] += 1
        if record.get("status") == "succeeded":
            self.state["successes"] += 1
        else:
            self.state["failures"] += 1
        self.state["last"] = record
        _append_jsonl(self.journal_path, record)
        return record

    async def loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.cycle()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.args.dr_cycle_seconds)


def _new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]


def _preflight_lookback_ms(args) -> int:
    """The clean-account check must cover the full runtime reconcile lookback.

    The launcher passes 1 h explicitly, but a direct default start must not
    silently check only the 5-minute legacy default: a fill/order inside the
    runtime lookback would otherwise be adopted into the fresh ledger.
    """

    return max(int(args.lookback_ms), int(getattr(args, "runtime_lookback_ms", args.lookback_ms)))


class SandboxSupervisor:
    def __init__(self, args, run_id: str | None = None, guard: StartupGuard | None = None):
        self.args = args
        self.run_id = run_id or _new_run_id()
        self.guard = guard
        self.guard_metadata = guard.summary() if guard is not None else None
        self.resume_from = str(getattr(args, "resume_from_run", "") or "")
        self.segment_index = int(getattr(args, "segment_index", 0) or 0)
        self.original_evidence_dir: Path | None = None
        self.original_evidence_digest = ""
        if self.resume_from:
            self.project = (
                f"{args.project_prefix}_{self.resume_from.lower().replace('-', '_')}"
            )
        else:
            self.project = f"{args.project_prefix}_{self.run_id.lower().replace('-', '_')}"
        self.evidence_dir = (ROOT / args.evidence_root / self.run_id).resolve()
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.window_path = self.evidence_dir / "window.json"
        self.supervisor_journal = self.evidence_dir / "supervisor.jsonl"
        self.state_path = self.evidence_dir / "window-state.json"
        self.observation_path = self.evidence_dir / "observation.jsonl"
        self.dr_dir = self.evidence_dir / "dr"
        self.dr_evidence_path = self.dr_dir / "dr-evidence.json"
        self.dr_journal_path = self.dr_dir / "dr-cycles.jsonl"
        self.backup_dir = self.dr_dir / "backup"
        self.restore_dir = self.dr_dir / "restore"
        for directory in (self.dr_dir, self.backup_dir, self.restore_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.start_ms: int | None = None
        self.end_ms: int | None = None
        self.aborted_reason = ""
        self.observer: subprocess.Popen | None = None
        self.tunnel: subprocess.Popen | None = None
        self.context: dict[str, Any] = {}
        # The known-hosts identity used for every off-host SSH command.  It is
        # carried in the context so the probe, the credential read and the
        # tunnel all use the same strict identity.
        self.context["host_key_alias"] = str(getattr(args, "host_key_alias", "") or "")
        self.feeder: DrFeeder | None = None
        self.dr_stop = asyncio.Event()
        self._dr_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ setup
    def _load_env(self) -> dict[str, str]:
        load_dotenv(ROOT / ".env", override=False)
        env = dict(os.environ)
        # This task's clients use one explicit egress: the Demo REST/WS and
        # preflight path must not silently diverge from the containers, and
        # the loopback/LAN DR clients must not be routed through an
        # unintended application proxy. This only touches this process'
        # environment and the sandbox env; the host's global proxy
        # configuration is not modified.
        for proxy_key in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            env.pop(proxy_key, None)
            os.environ.pop(proxy_key, None)
        environment = str(env.get("BINANCE_ENV", "")).strip().lower()
        if environment not in {"demo", "testnet", "test"}:
            raise RuntimeError("BINANCE_ENV_NOT_SANDBOX:" + environment)
        for key in ("BINANCE_API_KEY", "BINANCE_SECRET_KEY"):
            if not env.get(key):
                raise RuntimeError("BINANCE_CREDENTIALS_MISSING:" + key)
        return env

    async def _preflight(self, env: dict[str, str], resume: bool = False) -> dict[str, Any]:

        from trader.adapters.broker.binance_spot_demo_broker import (
            BinanceSpotDemoBroker,
            BinanceSpotDemoBrokerConfig,
        )
        from trader.api.env_config import get_binance_env, get_binance_env_config

        environment = get_binance_env(env)
        endpoints = get_binance_env_config(env)
        evidence: dict[str, Any] = {
            "environment": environment,
            "rest_base": endpoints["rest_base"],
            "private_ws_base": endpoints["private_ws_base"],
            "recorded_at_ms": int(time.time() * 1000),
        }

        # Preflight and the sandbox containers must use the same egress path;
        # a host-only proxy made the preflight and the containers diverge.
        # The preflight runs on the host and the trading stack runs in the
        # containers: their reachable routes can differ (a host without a direct
        # route to the venue still needs its explicit proxy). Both must reach the
        # same venue endpoint and account, so the preflight keeps its own
        # explicit egress while the containers use --container-proxy.
        env["BINANCE_PROXY_URL"] = str(
            getattr(self.args, "preflight_proxy", "") or env.get("BINANCE_PROXY_URL", "") or ""
        )
        env["QTS_COVERAGE_LOOKBACK_MS"] = str(_preflight_lookback_ms(self.args))
        env["QTS_RECONCILE_LOOKBACK_MS"] = str(
            getattr(self.args, "runtime_lookback_ms", self.args.lookback_ms)
        )
        env["QTS_SYMBOLS"] = self.args.symbols

        async def probe() -> None:
            broker = BinanceSpotDemoBroker(
                BinanceSpotDemoBrokerConfig.for_env(
                    env["BINANCE_API_KEY"],
                    env["BINANCE_SECRET_KEY"],
                    env=environment,
                    proxy_url=env.get("BINANCE_PROXY_URL") or None,
                )
            )
            await broker.connect()
            try:
                server_time = await broker.get_server_time()
                account = await broker._fetch_account()
                evidence["server_time_ms"] = server_time
                evidence["account_type"] = account.get("accountType")
                evidence["can_trade"] = account.get("canTrade")
                evidence["permissions"] = account.get("permissions")
                evidence["non_zero_assets"] = sorted(
                    str(item.get("asset"))
                    for item in account.get("balances", [])
                    if float(item.get("free", 0) or 0) + float(item.get("locked", 0) or 0) > 0
                )
                open_orders = await broker.get_open_orders()
                evidence["open_orders"] = len(open_orders)
                symbols = [
                    item.strip().upper()
                    for item in str(env.get("QTS_SYMBOLS", "BTCUSDT")).split(",")
                    if item.strip()
                ]
                lookback = int(env.get("QTS_COVERAGE_LOOKBACK_MS", "86400000"))
                for symbol in symbols:
                    orders, trades = await asyncio.gather(
                        broker.get_all_orders(symbol),
                        broker.get_my_trades(symbol),
                    )
                    since = server_time - lookback
                    recent_trades = [t for t in trades if int(t.get("time", 0) or 0) >= since]
                    foreign = []
                    own = []
                    own_history = []
                    if resume:
                        history_since = server_time - int(
                            getattr(self.args, "coverage_lookback_ms", 86400000)
                        )
                        for trade in trades:
                            if int(trade.get("time", 0) or 0) < history_since:
                                continue
                            matching = [
                                order
                                for order in orders
                                if str(order.broker_order_id) == str(trade.get("orderId"))
                            ]
                            if matching and any(
                                str(order.cl_ord_id).startswith("qts-")
                                for order in matching
                            ):
                                own_history.append(
                                    {
                                        "trade_id": trade.get("id"),
                                        "order_id": trade.get("orderId"),
                                        "cl_ord_id": str(matching[0].cl_ord_id),
                                        "time_ms": int(trade.get("time", 0) or 0),
                                    }
                                )
                    for trade in recent_trades:
                        matching = [
                            order
                            for order in orders
                            if str(order.broker_order_id) == str(trade.get("orderId"))
                        ]
                        if matching and any(
                            str(order.cl_ord_id).startswith("qts-") for order in matching
                        ):
                            own.append(trade)
                        else:
                            foreign.append(trade)
                    symbol_evidence = {
                        "recent_trades": len(recent_trades),
                        "foreign_recent_trades": len(foreign),
                        "own_recent_trades": len(own),
                    }
                    if resume:
                        symbol_evidence["own_history_trades"] = len(own_history)
                        symbol_evidence["own_history"] = own_history
                    evidence.setdefault("symbols", {})[symbol] = symbol_evidence
            finally:
                await broker.disconnect()

        last_error = None
        for attempt in range(1, 6):
            try:
                await probe()
                evidence["preflight_attempts"] = attempt
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                evidence["preflight_attempts"] = attempt
                evidence.setdefault("preflight_errors", []).append(
                    f"attempt {attempt}: {type(exc).__name__}: {exc}"[-400:]
                )
                if attempt < 5:
                    await asyncio.sleep(min(5 * attempt, 20))
        if last_error is not None:
            raise RuntimeError("SANDBOX_PREFLIGHT_UNAVAILABLE") from last_error
        if evidence.get("account_type") != "SPOT" or evidence.get("can_trade") is not True:
            raise RuntimeError("SANDBOX_ACCOUNT_NOT_TRADABLE")
        if any(item["foreign_recent_trades"] for item in evidence.get("symbols", {}).values()):
            raise RuntimeError("SANDBOX_ACCOUNT_NOT_DEDICATED")
        if not resume and any(
            item["own_recent_trades"] for item in evidence.get("symbols", {}).values()
        ):
            # A previous run's qts- trade inside the recovery lookback would be
            # adopted into a fresh local ledger and double-booked. Wait for a
            # clean window instead of guessing ownership.
            raise RuntimeError("SANDBOX_LOOKBACK_NOT_CLEAN")
        if int(evidence.get("open_orders") or 0) != 0:
            # A live venue order would survive into the fresh ledger as an
            # orphan. Never hide it behind a timestamp or a local reset.
            raise RuntimeError("SANDBOX_ACCOUNT_HAS_OPEN_ORDERS")
        evidence["endpoint_identity_source"] = (
            "BINANCE_ENV + resolved official sandbox URL + API response"
        )
        return evidence

    def _prepare_secrets(self, env: dict[str, str]) -> dict[str, str]:
        secret_dir = ROOT / "secrets" / f"phase6_{self.run_id.lower()}"
        secret_dir.mkdir(parents=True, exist_ok=True)
        api_path = secret_dir / "binance_api_key"
        secret_path = secret_dir / "binance_secret_key"
        pg_path = secret_dir / "pg_password"
        operator_path = secret_dir / "operator_password"
        api_path.write_text(env["BINANCE_API_KEY"], encoding="utf-8")
        secret_path.write_text(env["BINANCE_SECRET_KEY"], encoding="utf-8")
        pg_password = secrets.token_urlsafe(24)
        operator_password = secrets.token_urlsafe(24)
        pg_path.write_text(pg_password, encoding="utf-8")
        operator_path.write_text(operator_password, encoding="utf-8")
        return {
            "api_path": str(api_path),
            "secret_path": str(secret_path),
            "pg_password": pg_password,
            "operator_password": operator_password,
            "operator_password_file": str(operator_path),
            "password_hash": hash_password(operator_password),
        }

    # ---------------------------------------------------------------- resume
    def _psql_rows(self, sql: str) -> list[list[str]]:
        output = _checked(
            [
                "docker",
                "exec",
                self.context["postgres_container"],
                "psql",
                "-U",
                self.args.pg_user,
                "-d",
                self.args.database,
                "-At",
                "-F",
                "\t",
                "-c",
                sql,
            ],
            timeout=90,
        )
        return [line.split("\t") for line in output.splitlines() if line]

    def _original_host_port(self, service: str, container_port: str) -> int:
        name = f"{self.project}-{service}-1"
        output = _checked(
            [
                "docker",
                "inspect",
                name,
                "--format",
                "{{json .HostConfig.PortBindings}}",
            ],
            timeout=30,
        )
        bindings = json.loads(output)
        entries = bindings.get(container_port) or []
        if not entries:
            raise RuntimeError(f"RESUME_PORT_MISSING:{service}:{container_port}")
        return int(entries[0]["HostPort"])

    def _prepare_resume(self, env: dict[str, str]) -> None:
        if not self.resume_from:
            return
        # Reuse the original host ports so the existing postgres container is
        # not recreated with a different binding.
        self.args.pg_port = self._original_host_port("postgres", "5432/tcp")
        self.args.control_port = self._original_host_port("control", "8090/tcp")
        self.args.alert_port = self._original_host_port("alert-sink", "8085/tcp")
        # Observation-only: keep the original strategy module (book identity)
        # but never emit a signal.
        self.args.strategy_config = json.dumps({"observation_only": True})
        candidates = [
            ROOT / "artifacts/foundation/phase6/windows" / self.resume_from,
            ROOT / "artifacts/foundation/phase6/windows-aborted" / self.resume_from,
        ]
        self.original_evidence_dir = next((p for p in candidates if p.is_dir()), None)
        if self.original_evidence_dir is None:
            raise RuntimeError("RESUME_EVIDENCE_MISSING:" + self.resume_from)
        files = []
        for path in sorted(self.original_evidence_dir.rglob("*")):
            if path.is_file():
                files.append(
                    {
                        "path": path.relative_to(self.original_evidence_dir).as_posix(),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
        self.original_evidence_digest = hashlib.sha256(
            json.dumps(
                [[item["path"], item["sha256"]] for item in files],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.context["resume_from"] = self.resume_from
        self.context["segment_index"] = self.segment_index
        self.context["original_evidence_dir"] = str(self.original_evidence_dir)
        self.context["original_evidence_digest"] = self.original_evidence_digest

    def _resume_secrets(self) -> dict[str, str]:
        secret_dir = ROOT / "secrets" / f"phase6_{self.resume_from.lower()}"
        api_path = secret_dir / "binance_api_key"
        secret_path = secret_dir / "binance_secret_key"
        pg_path = secret_dir / "pg_password"
        operator_path = secret_dir / "operator_password"
        for path in (api_path, secret_path, pg_path, operator_path):
            if not path.is_file():
                raise RuntimeError("RESUME_SECRET_MISSING:" + path.name)
        operator_password = operator_path.read_text(encoding="utf-8").strip()
        return {
            "api_path": str(api_path),
            "secret_path": str(secret_path),
            "pg_password": pg_path.read_text(encoding="utf-8").strip(),
            "operator_password": operator_password,
            "operator_password_file": str(operator_path),
            "password_hash": hash_password(operator_password),
        }

    def _resume_db_check(self, preflight: dict, secrets_info: dict) -> None:
        continuation = (
            ROOT
            / "artifacts/foundation/phase6/segment-continuation-20261109/db-baseline-continuation-20261110.json"
        )
        baseline_path = (
            continuation
            if continuation.is_file()
            else ROOT
            / "artifacts/foundation/phase6/segment-continuation-20261109/db-baseline.json"
        )
        if not baseline_path.is_file():
            raise RuntimeError("RESUME_BASELINE_MISSING")
        self.context["resume_baseline_path"] = str(baseline_path)
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        counts = {
            row[0]: int(row[1])
            for row in self._psql_rows(
                "select 'accounts',count(*) from trading_accounts union all "
                "select 'commands',count(*) from trading_commands union all "
                "select 'events',count(*) from trading_events union all "
                "select 'executions',count(*) from trading_executions union all "
                "select 'ledger',count(*) from trading_ledger union all "
                "select 'outbox',count(*) from trading_outbox order by 1;"
            )
        }
        if counts != baseline["counts"]:
            raise RuntimeError("RESUME_DB_COUNTS_MISMATCH:" + json.dumps(counts))
        identity = {}
        for name, table, column in (
            ("executions", "trading_executions", "execution_key"),
            ("ledger", "trading_ledger", "entry_id"),
            ("events", "trading_events", "event_id"),
        ):
            ids = sorted(row[0] for row in self._psql_rows(f"select {column} from {table};"))
            identity[name] = hashlib.sha256(
                json.dumps(ids, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        expected = baseline.get("identity_hashes") or {}
        for name, key in (
            ("executions", "executions_ids_sha256"),
            ("ledger", "ledger_ids_sha256"),
            ("events", "events_ids_sha256"),
        ):
            if expected.get(key) and identity[name] != expected[key]:
                raise RuntimeError("RESUME_DB_IDENTITY_MISMATCH:" + name)
        lease = self._psql_rows(
            "select scope,owner,fencing_token,(expires_at < now()) from trading_runtime_leases;"
        )
        if not lease or str(lease[0][3]).lower() not in {"t", "true"}:
            raise RuntimeError("RESUME_LEASE_NOT_EXPIRED:" + json.dumps(lease))
        outbox_rows = self._psql_rows(
            "select delivered,count(*) from trading_outbox group by delivered;"
        )
        if any(str(row[0]).lower() not in {"t", "true"} for row in outbox_rows):
            raise RuntimeError("RESUME_OUTBOX_UNDELIVERED:" + json.dumps(outbox_rows))
        command_status = self._psql_rows(
            "select status,count(*) from trading_commands group by status;"
        )
        if any(row[0] != "CONFIRMED" for row in command_status):
            raise RuntimeError("RESUME_COMMAND_NOT_TERMINAL:" + json.dumps(command_status))
        order_status = self._psql_rows(
            "select status,count(*) from trading_order_projection group by status;"
        )
        if any(row[0] != "FILLED" for row in order_status):
            raise RuntimeError("RESUME_ORDER_NOT_FILLED:" + json.dumps(order_status))
        books_row = self._psql_rows("select state->'books' from trading_accounts;")
        if not books_row:
            raise RuntimeError("RESUME_BOOKS_MISSING")
        books = json.loads(books_row[0][0])
        book_id = f"book:{self.args.strategy_module}"
        if book_id not in books:
            raise RuntimeError("RESUME_BOOK_MISSING:" + book_id)
        command_ids = {
            row[0] for row in self._psql_rows("select cl_ord_id from trading_commands;")
        }
        execution_keys = [
            row[0] for row in self._psql_rows("select execution_key from trading_executions;")
        ]
        matched = 0
        for symbol_entry in (preflight.get("symbols") or {}).values():
            for trade in symbol_entry.get("own_history") or []:
                cl_ord_id = str(trade.get("cl_ord_id") or "")
                trade_id = str(trade.get("trade_id") or "")
                if cl_ord_id not in command_ids:
                    raise RuntimeError("RESUME_OWN_TRADE_COMMAND_MISMATCH:" + cl_ord_id)
                hits = [key for key in execution_keys if trade_id and trade_id in key]
                if len(hits) != 1:
                    raise RuntimeError("RESUME_OWN_TRADE_EXECUTION_MISMATCH:" + trade_id)
                matched += 1
        dual = None
        for _attempt in range(20):
            first = self._psql_rows(
                "select version,"
                "md5((state->'books')::text || (state->'positions')::text || "
                "(state->'holds')::text),"
                "state->'reconciliation'->>'complete' from trading_accounts;"
            )
            time.sleep(6)
            second = self._psql_rows(
                "select version,"
                "md5((state->'books')::text || (state->'positions')::text || "
                "(state->'holds')::text),"
                "state->'reconciliation'->>'complete' from trading_accounts;"
            )
            if (
                first
                and second
                and first[0][0] == second[0][0]
                and first[0][1] == second[0][1]
            ):
                dual = first
                break
        if dual is None:
            raise RuntimeError("RESUME_DUAL_SNAPSHOT_UNSTABLE")
        if str(dual[0][2]).lower() not in {"t", "true"}:
            raise RuntimeError("RESUME_RECONCILIATION_INCOMPLETE")
        evidence = {
            "counts": counts,
            "identity_hashes": identity,
            "lease_before": lease,
            "outbox": outbox_rows,
            "command_status": command_status,
            "order_status": order_status,
            "books": books,
            "own_trades_matched": matched,
            "dual_snapshot": {
                "version": dual[0][0],
                "state_md5": dual[0][1],
                "reconciliation_complete": True,
            },
        }
        _write_json(self.evidence_dir / "resume-preflight.json", evidence)
        self.context["resume_preflight"] = evidence
        self.context["lease_before_token"] = int(lease[0][2])

    def _resume_lease_check(self) -> None:
        rows = self._psql_rows(
            "select scope,owner,fencing_token,(expires_at > now()) "
            "from trading_runtime_leases;"
        )
        if not rows:
            raise RuntimeError("RESUME_LEASE_MISSING")
        token = int(rows[0][2])
        before = int(self.context.get("lease_before_token") or 0)
        if token <= before:
            raise RuntimeError(f"RESUME_FENCING_TOKEN_NOT_INCREMENTED:{before}->{token}")
        self.context["lease_after"] = rows[0]
        _append_jsonl(
            self.supervisor_journal,
            {
                "kind": "resume_lease",
                "before": before,
                "after": token,
                "owner": rows[0][1],
                "ts_ms": int(time.time() * 1000),
            },
        )

    def _write_segment_metadata(self, preflight: dict, secrets_info: dict) -> None:
        meta = {
            "kind": "phase6_segment",
            "segment_run_id": self.run_id,
            "resume_from": self.resume_from,
            "segment_index": self.segment_index,
            "observation_only": True,
            "original_evidence_dir": str(self.original_evidence_dir),
            "original_evidence_digest": self.original_evidence_digest,
            "qualified_rule_version": "qualified-v3",
            "db_baseline": self.context.get("resume_baseline_path")
            or "artifacts/foundation/phase6/segment-continuation-20261109/db-baseline.json",
            "db_identity_hashes": (self.context.get("resume_preflight") or {}).get(
                "identity_hashes"
            ),
            "lease_before": self.context.get("lease_before_token"),
            "lease_after": self.context.get("lease_after"),
            "started_at_ms": self.start_ms,
            "dr_ready_cycles": 3,
        }
        _write_json(self.evidence_dir / "segment.json", meta)
        self.context["segment"] = meta

    def _compose_env(self, args, secrets_info: dict[str, str]) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "QTS_COMPOSE_PROJECT": self.project,
                "QTS_PG_PORT": str(args.pg_port),
                "QTS_CONTROL_PORT": str(args.control_port),
                "QTS_ALERT_SINK_PORT": str(args.alert_port),
                "QTS_ACCOUNT_ID": args.account_id,
                "QTS_AUTH_REQUIRED": "1",
                "QTS_USERNAME": args.operator_username,
                "QTS_PASSWORD_HASH": secrets_info["password_hash"],
                "QTS_ORIGIN": f"http://127.0.0.1:{args.control_port}",
                "QTS_DR_REQUIRED": "1",
                "QTS_DR_EVIDENCE_DIR": self.dr_dir.as_posix(),
                "QTS_BACKUP_RPO_SECONDS": "300",
                "POSTGRES_DB": args.database,
                "POSTGRES_USER": args.pg_user,
                "POSTGRES_PASSWORD": secrets_info["pg_password"],
                "BINANCE_ENV": "demo",
                "BINANCE_PROXY_URL": args.container_proxy,
                "BINANCE_API_KEY_FILE_PATH": Path(secrets_info["api_path"]).as_posix(),
                "BINANCE_SECRET_KEY_FILE_PATH": Path(secrets_info["secret_path"]).as_posix(),
                "QTS_SYMBOLS": args.symbols,
                "QTS_RECONCILE_LOOKBACK_MS": str(
                    getattr(args, "runtime_lookback_ms", args.lookback_ms)
                ),
                "QTS_COVERAGE_LOOKBACK_MS": str(getattr(args, "coverage_lookback_ms", 86400000)),
                "QTS_INSTRUMENTS": args.instruments,
                "QTS_STRATEGY_MODULE": args.strategy_module,
                "QTS_STRATEGY_CONFIG": args.strategy_config,
                "QTS_STRATEGY_WARMUP_EVENTS": "0",
                "QTS_DEPLOYMENT_ID": args.deployment_id,
                "QTS_PRIVATE_STREAM_MODE": "ws_api_signature",
                "QTS_STRATEGY_MARKET_INTERVAL": args.market_interval,
                "QTS_MARKET_POLL_SECONDS": args.market_poll_seconds,
                "QTS_RECONCILE_INTERVAL_SECONDS": str(args.reconcile_seconds),
                "QTS_RISK_POLICY_JSON": json.dumps(
                    {
                        "max_reconciliation_age_ms": args.reconcile_seconds * 1500,
                        "max_mark_age_ms": args.reconcile_seconds * 1500,
                    }
                ),
                "QTS_EXECUTION_HEALTH_FILE": "/var/lib/qts/execution-health.json",
                "QTS_ALERT_WEBHOOK_URL": f"http://alert-sink:8085/webhook",
                "QTS_ALERT_HEARTBEAT_URL": f"http://alert-sink:8085/heartbeat",
                "QTS_ALERT_INTERVAL_SECONDS": "5",
            }
        )
        return env

    # ------------------------------------------------------------------ loops
    def _discard_tunnel(self) -> None:
        tunnel = self.tunnel
        self.tunnel = None
        if tunnel is not None:
            with suppress(Exception):
                tunnel.terminate()
            with suppress(Exception):
                tunnel.wait(timeout=5)

    async def _ensure_tunnel(self) -> None:
        if self.tunnel is not None and self.tunnel.poll() is None:
            # A live SSH process and a listening port do not prove the S3
            # operation is usable. Prove it at the application level before
            # reusing the tunnel; a failure discards it so the next attempt
            # rebuilds a fresh one instead of reusing an unusable path.
            try:
                _probe_bucket(self.context["s3_config"])
                return
            except Exception as exc:
                _append_jsonl(
                    self.supervisor_journal,
                    {
                        "kind": "tunnel_discarded",
                        "ts_ms": int(time.time() * 1000),
                        "reason": "APPLICATION_PROBE_FAILED",
                        "error": _sanitize_diagnostic(str(exc)),
                    },
                )
                self._discard_tunnel()
        else:
            self._discard_tunnel()
        self.tunnel, local_port = _start_tunnel(
            self.context["ssh_destination"],
            self.context["remote_port"],
            host_key_alias=str(self.context.get("host_key_alias", "") or ""),
        )
        self.context["local_port"] = local_port
        self.context["s3_config"] = S3Config(
            endpoint_url=f"http://127.0.0.1:{local_port}",
            bucket=self.context["bucket"],
            prefix=f"phase6/{self.run_id}",
            access_key=self.context["access_key"],
            secret_key=self.context["secret_key"],
            region="us-east-1",
        )
        try:
            _create_bucket(self.context["s3_config"])
            _probe_bucket(self.context["s3_config"])
        except Exception:
            self._discard_tunnel()
            raise
        _append_jsonl(
            self.supervisor_journal,
            {
                "kind": "tunnel",
                "ts_ms": int(time.time() * 1000),
                "local_port": local_port,
                "remote_port": self.context["remote_port"],
                "strict_host_key": True,
            },
        )

    async def _dr_loop(self) -> None:
        while not self.dr_stop.is_set():
            retry_seconds = self.args.dr_cycle_seconds
            try:
                await self._ensure_tunnel()
                assert self.feeder is not None
                self.feeder.s3_config = self.context["s3_config"]
                record = await self.feeder.cycle()
                if record.get("status") != "succeeded":
                    # Retry soon so a transient object-store failure does not
                    # create a >RPO gap between successful backups. Repeated
                    # failures still expire the evidence and block the runtime.
                    retry_seconds = min(30, self.args.dr_cycle_seconds)
            except Exception as exc:
                retry_seconds = min(30, self.args.dr_cycle_seconds)
                self.feeder.state["failures"] += 1
                _append_jsonl(
                    self.dr_journal_path,
                    {
                        "kind": "feeder_error",
                        "ts_ms": int(time.time() * 1000),
                        "error": f"{type(exc).__name__}: {exc}"[-1200:],
                    },
                )
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.dr_stop.wait(), timeout=retry_seconds)

    async def _allocate_book(self) -> None:
        import httpx

        password = self.context["operator_password"]
        base = f"http://127.0.0.1:{self.args.control_port}"
        async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
            response = await client.post(
                "/v1/auth/login",
                json={"username": self.args.operator_username, "password": password},
            )
            response.raise_for_status()
            csrf = response.json()["csrf_token"]
            headers = {"x-csrf-token": csrf}
            book = self.context["book_id"]
            commands = [
                ("alloc-usdt", "USDT", self.args.book_usdt),
                ("alloc-btc", "BTC", self.args.book_btc),
            ]
            for suffix, asset, amount in commands:
                command_id = f"{self.run_id}-{suffix}"
                response = await client.post(
                    "/v1/trading/allocations",
                    headers=headers,
                    json={
                        "command_id": command_id,
                        "account_id": self.args.account_id,
                        "book_id": book,
                        "asset": asset,
                        "amount": amount,
                        "reason": "phase6 sandbox strategy budget",
                    },
                )
                response.raise_for_status()
                self.context.setdefault("allocations", []).append(
                    {
                        "command_id": command_id,
                        "asset": asset,
                        "status": response.json().get("status"),
                    }
                )
            deadline = time.time() + 120
            pending = {item["command_id"] for item in self.context["allocations"]}
            while pending and time.time() < deadline:
                for command_id in list(pending):
                    response = await client.get(
                        f"/v1/commands/{command_id}",
                        params={"account_id": self.args.account_id},
                    )
                    if response.status_code == 200 and response.json().get("status") == "CONFIRMED":
                        pending.discard(command_id)
                if pending:
                    await asyncio.sleep(2)
            if pending:
                raise RuntimeError("ALLOCATION_NOT_CONFIRMED:" + ",".join(sorted(pending)))

    async def _window_loop(self, duration_seconds: float) -> None:
        deadline = time.monotonic() + duration_seconds
        containers = {
            "control": self.context["control_container"],
            "execution": self.context["execution_container"],
            "postgres": self.context["postgres_container"],
        }
        while time.monotonic() < deadline:
            restarts = {}
            for name, container in containers.items():
                try:
                    restarts[name] = _restart_count(container)
                except Exception as exc:
                    restarts[name] = f"error:{exc}"
            _append_jsonl(
                self.supervisor_journal,
                {
                    "kind": "stack",
                    "ts_ms": int(time.time() * 1000),
                    "restart_counts": restarts,
                    "dr": self.feeder.state if self.feeder else None,
                    "observation_bytes": (
                        self.observation_path.stat().st_size
                        if self.observation_path.exists()
                        else 0
                    ),
                },
            )
            if any(isinstance(value, int) and value > 0 for value in restarts.values()):
                self.aborted_reason = "CONTAINER_RESTART_DETECTED"
            await asyncio.sleep(min(60.0, max(1.0, deadline - time.monotonic())))

    # ------------------------------------------------------------------ verdict
    def _summarize(self) -> dict[str, Any]:
        samples = 0
        ready_samples = 0
        transitions = []
        marker_start_ms = None
        marker_end_ms = None
        marker_start_mono_ms = None
        marker_end_mono_ms = None
        first_sample_ms = None
        last_sample_ms = None
        ready_reasons: dict[str, int] = {}
        reason_streak: dict[str, int] = {}
        reason_max_streak: dict[str, int] = {}
        reason_start_ms: dict[str, int] = {}
        reason_max_duration_ms: dict[str, int] = {}
        active_reasons: set[str] = set()
        unresolved: set[str] = set()
        unresolved_streak: dict[str, int] = {}
        unresolved_max_streak: dict[str, int] = {}
        unresolved_max_duration_ms: dict[str, int] = {}
        repeated_sends: set[str] = set()
        coverage_seen = False
        coverage_unproven = False
        execution_counts: list[int] = []
        gap_max = 0
        metrics_samples = 0
        metrics_missing = 0
        coverage_missing_recorded_at_ms = 0
        metrics_ts: list[int] = []
        last_metrics: dict[str, Any] | None = None
        last_metrics_codes: set[str] = set()
        last_sample_had_metrics = False
        if self.observation_path.exists():
            for line in self.observation_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("kind") == "transition":
                    transitions.append(row)
                if row.get("kind") == "marker":
                    if "samples" in row:
                        marker_end_ms = row.get("ts_ms")
                        if isinstance(row.get("monotonic_ms"), int):
                            marker_end_mono_ms = row["monotonic_ms"]
                    elif marker_start_ms is None:
                        marker_start_ms = row.get("ts_ms")
                        if isinstance(row.get("monotonic_ms"), int):
                            marker_start_mono_ms = row["monotonic_ms"]
                    continue
                if row.get("kind") != "sample":
                    continue
                samples += 1
                if first_sample_ms is None:
                    first_sample_ms = row.get("ts_ms")
                last_sample_ms = row.get("ts_ms")
                if row.get("ready"):
                    ready_samples += 1
                body = (row.get("health") or {}).get("body") or {}
                reasons = {str(reason) for reason in (body.get("reasons") or [])}
                sample_ts = int(row.get("ts_ms") or 0)
                for reason in reasons:
                    ready_reasons[reason] = ready_reasons.get(reason, 0) + 1
                    reason_streak[reason] = reason_streak.get(reason, 0) + 1
                    reason_max_streak[reason] = max(
                        reason_max_streak.get(reason, 0), reason_streak[reason]
                    )
                    start = reason_start_ms.setdefault(reason, sample_ts)
                    reason_max_duration_ms[reason] = max(
                        reason_max_duration_ms.get(reason, 0),
                        max(0, sample_ts - start),
                    )
                for reason in set(reason_streak) - reasons:
                    reason_streak[reason] = 0
                    reason_start_ms.pop(reason, None)
                active_reasons = reasons
                metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
                has_metrics = bool(metrics) and bool(metrics.get("evidence"))
                if has_metrics:
                    metrics_samples += 1
                    metrics_ts.append(sample_ts)
                    last_metrics = row
                    last_sample_had_metrics = True
                    sample_codes = {str(code) for code in (metrics.get("unresolved") or [])}
                    last_metrics_codes = set(sample_codes)
                    for code in sample_codes:
                        unresolved.add(code)
                        unresolved_streak[code] = unresolved_streak.get(code, 0) + 1
                        unresolved_max_streak[code] = max(
                            unresolved_max_streak.get(code, 0), unresolved_streak[code]
                        )
                        for entry in metrics.get("unresolved_entries") or []:
                            if str(entry.get("code")) != code:
                                continue
                            at_ms = entry.get("at_ms")
                            if isinstance(at_ms, int):
                                unresolved_max_duration_ms[code] = max(
                                    unresolved_max_duration_ms.get(code, 0),
                                    max(0, sample_ts - at_ms),
                                )
                    for code in set(unresolved_streak) - sample_codes:
                        unresolved_streak[code] = 0
                    evidence = metrics.get("evidence") or {}
                    for command_id in (evidence.get("send_outcomes") or {}).get(
                        "repeated_send_commands"
                    ) or []:
                        repeated_sends.add(str(command_id))
                    coverage = metrics.get("coverage") or {}
                    if coverage.get("contiguous") is True:
                        coverage_seen = True
                    if not isinstance(coverage.get("recorded_at_ms"), int):
                        coverage_missing_recorded_at_ms += 1
                    counts = evidence.get("executions") or {}
                    if isinstance(counts.get("count"), int):
                        execution_counts.append(counts["count"])
                else:
                    # A sample without a full metrics body is an evidence gap:
                    # it must never look like "no unresolved" or "still covered".
                    metrics_missing += 1
                    last_sample_had_metrics = False
                if "PRIVATE_STREAM_COVERAGE_UNPROVEN" in (body.get("reasons") or []):
                    coverage_unproven = True
                gap = row.get("sample_gap_ms")
                if isinstance(gap, int):
                    gap_max = max(gap_max, gap)
        monotonic = all(
            later >= earlier for earlier, later in zip(execution_counts, execution_counts[1:])
        )
        dr_successes = self.feeder.state["successes"] if self.feeder else 0
        window_start_ms = marker_start_ms or first_sample_ms or self.start_ms
        window_end_ms = marker_end_ms or last_sample_ms or int(time.time() * 1000)
        # The observer's monotonic clock is the authority for the actual
        # elapsed duration; the wall-clock markers can slew by seconds over
        # 24h. Fall back to the wall markers only when monotonic is absent.
        if marker_start_mono_ms is not None and marker_end_mono_ms is not None:
            observed_duration_seconds = max(0, (marker_end_mono_ms - marker_start_mono_ms) // 1000)
        else:
            observed_duration_seconds = max(0, (int(window_end_ms) - int(window_start_ms)) // 1000)
        # The RPO is bound to the observed window: a fresh backup at or before
        # the start, every interval inside the window, and a still-fresh last
        # backup at the end. Backups after the window never cover a past gap.
        rpo = summarize_rpo(
            self.backup_dir / "backup-journal.jsonl",
            300,
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
        )
        duration_seconds = observed_duration_seconds
        sample_seconds = float(getattr(getattr(self, "args", None), "sample_seconds", 30.0) or 30.0)
        expected_samples = max(2, int(duration_seconds / max(1.0, sample_seconds)))
        min_samples = max(2, int(expected_samples * 0.9))
        sample_gap_limit_ms = max(180_000, int(sample_seconds * 3 * 1000))
        # A bounded external recovery may close readiness for a while; the bound
        # is expressed in real elapsed time, not sample count. Venue/proxy
        # outages may legitimately last many minutes; other readiness faults
        # must clear within two minutes. All reasons must be clear at the end.
        network_reasons = {
            "PRIVATE_STREAM_ALIGNING",
            "PRIVATE_STREAM_RECOVERING",
            "PRIVATE_STREAM_UNHEALTHY",
            "PRIVATE_STREAM_DISCONNECTED",
            "PRIVATE_STREAM_STALE_DETECTED",
            "PRIVATE_STREAM_ERROR",
            "PRIVATE_STREAM_START_FAILED",
            "STRATEGY_OR_MARKET_DATA_FAILED",
            "RECONCILIATION_UNAVAILABLE",
            "RECOVERY_SNAPSHOT_UNSTABLE",
            "ACCOUNT_SNAPSHOT_STALE",
            "ACCOUNT_NOT_RECONCILED",
            "VALUATION_INCOMPLETE",
            "VALUATION_STALE_OR_MISSING",
            "STRATEGY_ACCOUNT_NOT_READY",
            "RECOVERY_GENERATION_OR_OVERFLOW",
            # Reconciliation facts can appear as readiness reasons (for
            # example UNBOOKED_EXECUTIONS while a fill waits for the next
            # reconcile). They share the 30-minute reconciliation recovery
            # bound; the strict RPO gate remains separate.
            "BALANCE_MISMATCH",
            "UNBOOKED_EXECUTIONS",
            "UNKNOWN_EXECUTION",
            "FILL_OBSERVATION_MISMATCH",
            "TERMINAL_EXECUTION_MISMATCH",
            "INCOMPLETE_SNAPSHOT",
            "STALE_SNAPSHOT",
            "UNKNOWN_ORDER",
            "UNKNOWN_COMMAND",
        }

        def reason_bound_ms(reason: str) -> int:
            name = str(reason).split(":", 1)[0].strip()
            if name in network_reasons or name.startswith("PRIVATE_RECOVERY_"):
                return 1_800_000
            return 120_000

        # Reconciliation facts can legitimately take one or more reconcile
        # cycles to settle after a venue outage; a sample count is not a time
        # bound. Balance-affecting recovery codes get the same 30-minute
        # network/reconciliation recovery bound as the reasons above; all other
        # unresolved codes must clear within two minutes.
        reconciliation_unresolved = {
            "BALANCE_MISMATCH",
            "UNBOOKED_EXECUTIONS",
            "UNKNOWN_EXECUTION",
            "FILL_OBSERVATION_MISMATCH",
            "TERMINAL_EXECUTION_MISMATCH",
            "INCOMPLETE_SNAPSHOT",
            "STALE_SNAPSHOT",
            "UNKNOWN_ORDER",
            "UNKNOWN_COMMAND",
            "RECOVERY_GENERATION_OR_OVERFLOW",
            "RECOVERY_SNAPSHOT_UNSTABLE",
        }

        def unresolved_bound_ms(code: str) -> int:
            return 1_800_000 if code in reconciliation_unresolved else 120_000

        metrics_min = min_samples
        metrics_gap_max = (
            max(later - earlier for earlier, later in zip(metrics_ts, metrics_ts[1:]))
            if len(metrics_ts) > 1
            else 0
        )
        # The tail between the last evidenced sample and the end of the window
        # is a metrics gap as well; a window whose evidence stops half way
        # must never look continuously evidenced.
        if metrics_ts:
            metrics_gap_max = max(
                metrics_gap_max,
                max(0, window_end_ms - metrics_ts[-1]),
            )
        else:
            metrics_gap_max = max(metrics_gap_max, max(0, window_end_ms - window_start_ms))
        last_coverage: dict[str, Any] = {}
        if last_metrics is not None:
            last_coverage = (last_metrics.get("metrics") or {}).get("coverage") or {}
        last_metrics_ts = int(last_metrics.get("ts_ms") or 0) if last_metrics else 0
        coverage_recorded_ms = int(last_coverage.get("recorded_at_ms") or 0)
        coverage_fresh_ms = coverage_recorded_ms
        # The private-stream coverage summary is refreshed by the reconcile
        # loop, so its age at the end of a healthy window is uniformly
        # distributed over one reconcile interval. The freshness bound must
        # tolerate a skipped reconcile plus the sampling slack; a fixed 300s
        # bound would reject a healthy window.
        reconcile_seconds = int(
            getattr(getattr(self, "args", None), "reconcile_seconds", 300) or 300
        )
        coverage_fresh_limit_ms = max(300_000, 2 * reconcile_seconds * 1000 + sample_gap_limit_ms)
        coverage_fresh_at_end = bool(
            last_coverage.get("contiguous") is True
            and coverage_fresh_ms > 0
            and 0 <= window_end_ms - coverage_fresh_ms <= coverage_fresh_limit_ms
            and last_metrics_ts > 0
            and 0 <= window_end_ms - last_metrics_ts <= sample_gap_limit_ms
        )
        unexplained = {
            reason: reason_max_duration_ms.get(reason, 0)
            for reason in ready_reasons
            if not reason.startswith("STRATEGY_WARMUP")
            and reason_max_duration_ms.get(reason, 0) > reason_bound_ms(reason)
        }
        criteria = {
            "duration_at_least_24h": duration_seconds >= 24 * 3600,
            "samples_present": samples > 0,
            "samples_cover_window": samples >= min_samples,
            "sample_gap_within_bound": gap_max <= sample_gap_limit_ms,
            "no_private_stream_coverage_unproven": not coverage_unproven,
            "coverage_proof_recorded": coverage_seen,
            "metrics_samples_present": metrics_samples >= metrics_min,
            "metrics_gap_within_bound": metrics_gap_max <= sample_gap_limit_ms,
            "coverage_fresh_at_end": coverage_fresh_at_end,
            # A transient difference that clears on the next reconciliation is
            # explained (for example venue balances settling just after a fill).
            # Persistent or accumulating unresolved codes still fail the window.
            "no_persistent_unresolved": all(
                duration <= unresolved_bound_ms(code)
                for code, duration in unresolved_max_duration_ms.items()
            ),
            "unresolved_cleared_at_end": (
                last_metrics is not None and last_sample_had_metrics and not last_metrics_codes
            ),
            "no_repeated_sends": not repeated_sends,
            "execution_count_monotonic": monotonic,
            "dr_backups_fresh": bool(rpo.get("rpo_satisfied")),
            "dr_cycles_succeeded": dr_successes > 0,
            "no_container_restart": not self.aborted_reason.startswith("CONTAINER_"),
            "no_supervisor_abort": not self.aborted_reason,
            "no_supervisor_error": not getattr(self, "abort_error", ""),
            "no_persistent_reasons": all(
                duration <= reason_bound_ms(reason)
                for reason, duration in reason_max_duration_ms.items()
            ),
            "reasons_cleared_at_end": not active_reasons,
        }
        summary = {
            "run_id": self.run_id,
            "project": self.project,
            "guard": getattr(self, "guard_metadata", None),
            "supervisor_start_ms": self.start_ms,
            "started_at_ms": window_start_ms,
            "ended_at_ms": window_end_ms,
            "duration_seconds": duration_seconds,
            "samples": samples,
            "metrics_samples": metrics_samples,
            "metrics_missing": metrics_missing,
            "metrics_gap_max_ms": metrics_gap_max,
            "coverage_fresh_limit_ms": coverage_fresh_limit_ms,
            "coverage_missing_recorded_at_ms": coverage_missing_recorded_at_ms,
            "expected_samples": expected_samples,
            "min_samples": min_samples,
            "sample_gap_limit_ms": sample_gap_limit_ms,
            "ready_samples": ready_samples,
            "ready_ratio": (ready_samples / samples) if samples else 0,
            "sample_gap_max_ms": gap_max,
            "transitions": transitions,
            "ready_reasons": ready_reasons,
            "reason_max_streak": dict(sorted(reason_max_streak.items())),
            "reason_max_duration_ms": dict(sorted(reason_max_duration_ms.items())),
            "unexplained_reasons": unexplained,
            "unresolved_seen": sorted(unresolved),
            "unresolved_max_streak": dict(sorted(unresolved_max_streak.items())),
            "unresolved_max_duration_ms": dict(sorted(unresolved_max_duration_ms.items())),
            "repeated_send_commands": sorted(repeated_sends),
            "coverage_proof_recorded": coverage_seen,
            "execution_counts": execution_counts,
            "dr": {
                "cycles": self.feeder.state["cycles"] if self.feeder else 0,
                "successes": dr_successes,
                "failures": self.feeder.state["failures"] if self.feeder else 0,
                "rpo": rpo,
                "evidence_path": str(self.dr_evidence_path),
                "journal_path": str(self.dr_journal_path),
            },
            "observation_path": str(self.observation_path),
            "aborted_reason": self.aborted_reason,
            "criteria": criteria,
            "passed": all(criteria.values()),
        }
        context = getattr(self, "context", None) or {}
        if context.get("segment") is not None:
            summary["segment"] = context["segment"]
        return summary

    # -------------------------------------------------------------------- run
    async def _run_inner(self) -> int:
        env = self._load_env()
        self.start_ms = int(time.time() * 1000)
        if self.resume_from:
            self._prepare_resume(env)
        # The shared Docker daemon may host other tasks; never reuse a fixed
        # port. Zero means "pick a free ephemeral port" and record the actual
        # value in the window evidence.
        for name in ("pg_port", "control_port", "alert_port"):
            if getattr(self.args, name) == 0:
                setattr(self.args, name, _free_port())
        preflight = await self._preflight(env, resume=bool(self.resume_from))
        _write_json(self.evidence_dir / "preflight.json", preflight)
        secrets_info = (
            self._resume_secrets() if self.resume_from else self._prepare_secrets(env)
        )
        self.context["operator_password"] = secrets_info["operator_password"]

        host_key_alias = str(self.context.get("host_key_alias", "") or "")
        remote = _remote_probe(
            self.args.remote_host,
            self.args.ssh_user,
            self.args.expected_hostname,
            host_key_alias=host_key_alias,
        )
        self.context["remote_probe"] = remote
        self.context["ssh_destination"] = remote["destination"]
        self.context["remote_port"] = int(remote["port"])
        self.context["access_key"], self.context["secret_key"] = _remote_credentials(
            remote["destination"], host_key_alias=host_key_alias
        )
        self.context["bucket"] = f"qts-p6-sandbox-{self.run_id.lower()}"
        self.context["dr_evidence_path"] = self.dr_evidence_path

        compose_env = self._compose_env(self.args, secrets_info)
        compose_env["QTS_PG_PORT"] = str(self.args.pg_port)
        compose_env["QTS_CONTROL_PORT"] = str(self.args.control_port)
        compose_env["QTS_ALERT_SINK_PORT"] = str(self.args.alert_port)

        def compose(*argv: str, timeout: float = 600) -> str:
            return _checked(
                ["docker", "compose", "-p", self.project, *argv],
                timeout=timeout,
                env=compose_env,
            )

        try:
            compose("config", "--quiet", timeout=120)
            up_flags = ["--no-build"] if self.args.no_build else []
            if not self.args.no_build:
                compose("build", timeout=900)
            else:
                _checked(["docker", "image", "inspect", self.args.image], timeout=30)

            def up_service(service: str, verify_timeout: float = 180.0) -> str:
                """Start a service and verify the container even if the CLI hangs.

                The shared Docker daemon can complete create/start while the
                compose CLI never returns (observed with concurrent tasks). A
                short timeout followed by an authoritative container check keeps
                the run honest without waiting forever.
                """
                try:
                    compose("up", "-d", *up_flags, service, timeout=45)
                except subprocess.TimeoutExpired:
                    pass
                deadline = time.time() + verify_timeout
                while time.time() < deadline:
                    try:
                        return _container_id(self.project, service)
                    except Exception:
                        time.sleep(2)
                raise RuntimeError(f"COMPOSE_SERVICE_NOT_STARTED:{service}")

            image_info = _checked(
                ["docker", "image", "inspect", "--format", "{{.Id}} {{.Created}}", self.args.image],
                timeout=30,
            ).strip()
            self.context["image"] = image_info
            up_service("postgres", verify_timeout=180)
            up_service("alert-sink", verify_timeout=180)
            postgres_container = _container_id(self.project, "postgres")
            deadline = time.time() + 120
            while time.time() < deadline:
                if (
                    _run(
                        [
                            "docker",
                            "exec",
                            postgres_container,
                            "pg_isready",
                            "-U",
                            self.args.pg_user,
                        ]
                    ).returncode
                    == 0
                ):
                    break
                await asyncio.sleep(2)
            self.context["postgres_container"] = postgres_container
            self.context["source_dsn"] = (
                f"postgresql://{self.args.pg_user}:{quote(secrets_info['pg_password'], safe='')}"
                f"@127.0.0.1:{self.args.pg_port}/{self.args.database}"
            )
            self.context["control_container"] = up_service("control", verify_timeout=180)
            control_url = f"http://127.0.0.1:{self.args.control_port}"
            control_live = _wait_http(f"{control_url}/health/live", timeout=180, expect=200)
            if control_live.get("status_code") != 200:
                raise RuntimeError("CONTROL_NOT_LIVE:" + json.dumps(control_live))
            # Wait for the schema the DR feeder dumps.
            _wait_http(f"{control_url}/health/ready", timeout=180, expect=200)
            if self.resume_from:
                self._resume_db_check(preflight, secrets_info)

            await self._ensure_tunnel()
            self.context.update(
                {
                    "dr_journal_path": self.dr_journal_path,
                    "backup_dir": self.backup_dir,
                    "restore_dir": self.restore_dir,
                    "database": self.args.database,
                }
            )
            self.feeder = DrFeeder(self.args, self.context)
            first_dr = await self.feeder.cycle()
            if first_dr.get("status") != "succeeded" or first_dr.get("verified") is not True:
                raise RuntimeError("INITIAL_DR_CYCLE_FAILED:" + str(first_dr.get("error")))
            # A counted window needs at least three fresh consecutive verified
            # off-host backup/readback/restore cycles inside the 300 s RPO.
            for _ready_attempt in range(2):
                ready_record = await self.feeder.cycle()
                if (
                    ready_record.get("status") != "succeeded"
                    or ready_record.get("verified") is not True
                ):
                    raise RuntimeError("DR_READINESS_NOT_REACHED:" + str(ready_record.get("error")))
            # Keep RPO evidence fresh while execution startup/allocation/alignment
            # may take minutes; starting the feeder only after readiness created a
            # deadlock when the first evidence expired before readiness.
            self._dr_task = asyncio.create_task(self._dr_loop())
            self.context["execution_container"] = up_service("execution", verify_timeout=300)
            # The strategy starts as soon as the execution runtime is ready and
            # may emit its first closed-bar signal immediately. Queue the book
            # allocation before waiting for readiness so resume_queued applies
            # it before the market loop can place an order.
            self.context["book_id"] = f"book:{self.args.strategy_module}"
            if self.resume_from:
                _append_jsonl(
                    self.supervisor_journal,
                    {
                        "kind": "resume_allocation_skipped",
                        "book_id": self.context["book_id"],
                        "ts_ms": int(time.time() * 1000),
                    },
                )
            else:
                await self._allocate_book()
            # Run the (blocking) readiness poll in a worker thread so the DR
            # feeder task keeps producing fresh evidence while the control's
            # readiness may need a reconcile to clear startup reasons.
            ready = await asyncio.to_thread(
                _wait_http, f"{control_url}/health/trading", 600, expect=200
            )
            if ready.get("status_code") != 200:
                raise RuntimeError("TRADING_NOT_READY:" + json.dumps(ready)[:800])
            if self.resume_from:
                self._resume_lease_check()

            self.observer = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "observe_trading_runtime.py"),
                    "--base-url",
                    control_url,
                    "--username",
                    self.args.operator_username,
                    "--password-file",
                    secrets_info["operator_password_file"],
                    "--account-id",
                    self.args.account_id,
                    "--interval",
                    str(self.args.sample_seconds),
                    "--duration",
                    str(self.args.duration_hours * 3600),
                    "--out",
                    str(self.observation_path),
                    "--marker",
                    f"window:{self.run_id}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if self.resume_from:
                self._write_segment_metadata(preflight, secrets_info)
            try:
                await self._window_loop(self.args.duration_hours * 3600)
            finally:
                self.dr_stop.set()
                if self._dr_task is not None:
                    self._dr_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._dr_task
            # Close the race between the last periodic check and the summary:
            # a restart in the final minute still invalidates continuity.
            for container in (
                self.context.get("control_container"),
                self.context.get("execution_container"),
                self.context.get("postgres_container"),
            ):
                try:
                    if container and _restart_count(container) > 0:
                        self.aborted_reason = "CONTAINER_RESTART_DETECTED"
                except Exception:
                    self.aborted_reason = "CONTAINER_STATE_UNVERIFIABLE"
            if self.observer is not None:
                await asyncio.to_thread(self.observer.wait, 120)
            # Preserve raw container logs before any teardown; they are part of
            # the reviewable window evidence (startup, reconnect, venue errors).
            log_dir = self.evidence_dir / "container-logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            for name, container in (
                ("control", self.context.get("control_container")),
                ("execution", self.context.get("execution_container")),
                ("postgres", self.context.get("postgres_container")),
            ):
                if not container:
                    continue
                try:
                    result = _run(["docker", "logs", container], timeout=120)
                    (log_dir / f"{name}.log").write_text(
                        (result.stdout or "") + (result.stderr or ""), encoding="utf-8"
                    )
                except Exception as exc:
                    (log_dir / f"{name}.log").write_text(
                        "LOG_CAPTURE_FAILED: " + f"{type(exc).__name__}: {exc}" + "\n",
                        encoding="utf-8",
                    )
            self.end_ms = int(time.time() * 1000)
        except Exception as exc:
            self.aborted_reason = f"SUPERVISOR_ERROR:{type(exc).__name__}"
            self.abort_error = f"{type(exc).__name__}: {exc}"[-1500:]
            _append_jsonl(
                self.supervisor_journal,
                {
                    "kind": "abort",
                    "ts_ms": int(time.time() * 1000),
                    "error": self.abort_error,
                },
            )
        finally:
            self.dr_stop.set()
            if self._dr_task is not None and not self._dr_task.done():
                self._dr_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._dr_task
            if self.observer is not None and self.observer.poll() is None:
                with suppress(Exception):
                    self.observer.terminate()
            if self.tunnel is not None:
                with suppress(Exception):
                    self.tunnel.terminate()
        summary = self._summarize()
        summary["preflight"] = preflight
        summary["remote"] = {
            "hostname": self.context.get("remote_probe", {}).get("hostname"),
            "destination": self.context.get("ssh_destination"),
            "remote_port": self.context.get("remote_port"),
            "strict_host_key": True,
        }
        summary["allocations"] = self.context.get("allocations", [])
        summary["image"] = self.context.get("image")
        summary["ports"] = {
            "postgres": self.args.pg_port,
            "control": self.args.control_port,
            "alert_sink": self.args.alert_port,
        }
        if getattr(self, "abort_error", ""):
            summary["error"] = self.abort_error
        _write_json(self.window_path, summary)
        _write_json(self.state_path, {"status": "finished", "window": summary})
        print(json.dumps({"run_id": self.run_id, "passed": summary["passed"]}))
        return 0 if summary["passed"] else 1

    def _stop_stack_best_effort(self) -> None:
        """Stop this window's task containers after the logs are preserved.

        Containers are stopped (not removed) so their state stays inspectable.
        The caller still holds the startup guard here; if the stop fails, the
        surviving container is exactly what the next owner's orphan probe
        rejects.
        """

        project = getattr(self, "project", None)
        journal = getattr(self, "supervisor_journal", None)
        if not project:
            return

        def record(payload: dict) -> None:
            if journal is None:
                return
            with suppress(Exception):
                _append_jsonl(journal, {"kind": "teardown", "ts_ms": int(time.time() * 1000), **payload})

        try:
            # _checked returns the command's stdout as a string.
            listed = _checked(
                [
                    "docker",
                    "ps",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={project}",
                ],
                timeout=60,
            )
            ids = [line.strip() for line in (listed or "").splitlines() if line.strip()]
            if not ids:
                return
            _checked(["docker", "stop", "--time", "15", *ids], timeout=300)
            record({"status": "stopped", "containers": len(ids)})
        except Exception as exc:  # noqa: BLE001 - teardown is best effort, never masks the verdict
            record({"status": "failed", "error": f"{type(exc).__name__}: {exc}"[-500:]})

    async def run(self) -> int:
        try:
            try:
                return await self._run_inner()
            except Exception as exc:
                self.aborted_reason = f"SUPERVISOR_ERROR:{type(exc).__name__}"
                _append_jsonl(
                    self.supervisor_journal,
                    {
                        "kind": "abort",
                        "ts_ms": int(time.time() * 1000),
                        "error": f"{type(exc).__name__}: {exc}"[-1500:],
                    },
                )
                summary = self._summarize()
                summary["error"] = f"{type(exc).__name__}: {exc}"[-1500:]
                summary["preflight"] = locals().get("preflight")
                _write_json(self.window_path, summary)
                print(
                    json.dumps(
                        {"run_id": self.run_id, "passed": False, "error": summary["error"][:300]}
                    )
                )
                return 1
        finally:
            # The startup guard is still held by main(): safe cleanup is inside
            # the guarded section. A crash skips this, and the next owner's
            # orphan probe fails closed.
            self._stop_stack_best_effort()


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase-6 continuous sandbox supervisor")
    parser.add_argument("--project-prefix", default="qts_p6_sandbox")
    parser.add_argument("--no-build", action="store_true", help="use the existing image")
    parser.add_argument("--image", default="qts-runtime:local")
    parser.add_argument("--evidence-root", default="artifacts/foundation/phase6/windows")
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--sample-seconds", type=float, default=30.0)
    parser.add_argument("--dr-cycle-seconds", type=int, default=120)
    parser.add_argument("--account-id", default="binance_spot")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT,USDCUSDT")
    parser.add_argument("--instruments", default='{"BTCUSDT":["BTC","USDT"]}')
    parser.add_argument("--strategy-module", default="trader.strategies.fire_test")
    parser.add_argument(
        "--strategy-config",
        default='{"max_signals":0,"interval_seconds":600,"order_size":"0.0001","start_with":"BUY"}',
    )
    parser.add_argument("--deployment-id", default="phase6-sandbox")
    parser.add_argument("--market-interval", default="1m")
    parser.add_argument("--market-poll-seconds", default="2")
    parser.add_argument("--reconcile-seconds", type=int, default=300)
    parser.add_argument("--lookback-ms", type=int, default=300000)
    parser.add_argument("--runtime-lookback-ms", type=int, default=3600000)
    parser.add_argument("--coverage-lookback-ms", type=int, default=86400000)
    parser.add_argument("--book-usdt", default="500")
    parser.add_argument("--book-btc", default="0.01")
    parser.add_argument("--database", default="trading")
    parser.add_argument("--pg-user", default="trader")
    parser.add_argument("--pg-port", type=int, default=0, help="0 selects a free port")
    parser.add_argument("--control-port", type=int, default=0, help="0 selects a free port")
    parser.add_argument("--alert-port", type=int, default=0, help="0 selects a free port")
    parser.add_argument("--operator-username", default="owner")
    parser.add_argument(
        "--container-proxy",
        default="",
        help="proxy for the sandbox containers; empty means direct egress",
    )
    parser.add_argument(
        "--preflight-proxy",
        default="",
        help="explicit egress for the host-side preflight; empty inherits BINANCE_PROXY_URL",
    )
    parser.add_argument(
        "--remote-host",
        default=None,
        help=(
            "designated off-host address; overrides $QTS_OFFHOST_HOST. "
            "There is no default: the window fails closed when it is not configured."
        ),
    )
    parser.add_argument("--ssh-user", default="alex")
    parser.add_argument("--expected-hostname", default="my-server")
    parser.add_argument(
        "--host-key-alias",
        default=None,
        help=(
            "known_hosts identity used for the connection (ssh HostKeyAlias); "
            "overrides $QTS_OFFHOST_HOST_KEY_ALIAS. Host-key checking stays enabled."
        ),
    )
    parser.add_argument(
        "--resume-from-run",
        default="",
        help="continue the original DB/volumes/secrets from this run id",
    )
    parser.add_argument("--segment-index", type=int, default=0)
    parser.add_argument(
        "--guard-probe",
        action="store_true",
        help=(
            "diagnostic: acquire the startup guard and run the orphan probe, "
            "then exit before any Demo/Compose/DR side effect"
        ),
    )
    args = parser.parse_args()
    if args.runtime_lookback_ms < args.lookback_ms:
        parser.error("--runtime-lookback-ms must be >= --lookback-ms")
    if args.coverage_lookback_ms < args.runtime_lookback_ms:
        parser.error("--coverage-lookback-ms must be >= --runtime-lookback-ms")
    if not args.guard_probe:
        # The guard probe is a local diagnostic that exits before any remote
        # side effect, so it needs no off-host address.  Every real entry
        # resolves the target here, before the guard is taken and before any
        # Demo/Compose/DR side effect: an unconfigured target fails closed
        # instead of guessing an address.
        try:
            args.remote_host = resolve_remote_host(args.remote_host)
        except RemoteTargetNotConfigured as exc:
            print(
                json.dumps({"error": "REMOTE_HOST_NOT_CONFIGURED", "detail": str(exc)}),
                file=sys.stderr,
            )
            raise SystemExit(2) from None
        args.host_key_alias = resolve_host_key_alias(args.host_key_alias)

    run_id = _new_run_id()
    guard = StartupGuard(None, run_id=run_id, entry="scripts/sandbox_supervisor.py")
    try:
        guard.acquire()
    except GuardHeldError as exc:
        print(json.dumps({"error": "STARTUP_GUARD_HELD", "detail": str(exc)[:1500]}))
        raise SystemExit(GuardHeldError.exit_code) from None
    except OrphanDetectedError as exc:
        print(json.dumps({"error": "STARTUP_ORPHAN_DETECTED", "detail": str(exc)[:1500]}))
        raise SystemExit(OrphanDetectedError.exit_code) from None
    except OrphanCheckUnavailable as exc:
        print(json.dumps({"error": "STARTUP_ORPHAN_CHECK_UNAVAILABLE", "detail": str(exc)[:1500]}))
        raise SystemExit(OrphanCheckUnavailable.exit_code) from None

    try:
        if args.guard_probe:
            print(json.dumps({"guard": "acquired", **guard.summary()}, sort_keys=True))
            # Keep the lock held long enough for a concurrent real-entry
            # contender to be deterministically rejected in tests.
            time.sleep(2)
            return
        supervisor = SandboxSupervisor(args, run_id=run_id, guard=guard)
        raise SystemExit(asyncio.run(supervisor.run()))
    finally:
        guard.release()


if __name__ == "__main__":
    main()
