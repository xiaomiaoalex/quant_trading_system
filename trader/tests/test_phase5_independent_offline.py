"""Phase-5 independent acceptance tests - offline half.

Expectation source (G2): the parent work order
``artifacts/foundation/phase5/parent-phase5-work-order.md``, the frozen
acceptance table in ``docs/FOUNDATION_REFACTOR_PHASE0.md`` 5.1-5.11 and the
runtime contract in ``docs/INTERFACE_CONTRACTS.md`` 13.16. No expectation in
this file is derived from the engineer's green counts or from the behaviour of
the code under test; where the shipped helper ``scripts/check_credential_isolation.py``
is exercised it is a cross-check, never the source of truth.

Covered here: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6 (cadence/manifest/receipt logic)
and 5.9, plus fault-injection proof that these checks are not vacuous.

This file never touches PostgreSQL, Docker, an existing runtime or the network
beyond a loopback receiver started by the test itself.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "compose.yml"
SCRIPTS_DIR = ROOT / "scripts"

# Contract 13.16 "进程凭据边界": these four keys must never appear in Control.
CONTROL_FORBIDDEN_KEYS = (
    "BINANCE_API_KEY",
    "BINANCE_SECRET_KEY",
    "BINANCE_API_KEY_FILE",
    "BINANCE_SECRET_KEY_FILE",
)
# Frozen acceptance 5.1.
REQUIRED_COMPOSE_SERVICES = ("postgres", "control", "execution", "research", "history")
# Work order: reuse the existing new-stack entry points, not the legacy API/OMS.
EXPECTED_ENTRYPOINTS = {
    "control": "trader.runtime.control_main",
    "execution": "trader.runtime.trading_worker_main",
    "research": "trader.runtime.research_worker_main",
    "history": "trader.runtime.history_worker_main",
}

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
NOW_MS = 1_763_000_000_000

BLOCKER_REASONS = [
    "UNKNOWN_COMMAND_PENDING",
    "BALANCE_MISMATCH",
    "EXECUTION_INSTANCE_CONFLICT",
]
EXPECTED_ALERT_CODES = {"UNKNOWN_COMMAND", "BALANCE_MISMATCH", "EXECUTION_LEASE_LOST"}


# --------------------------------------------------------------------------- #
# Independent helpers (hand-written here, not imported from the implementation)
# --------------------------------------------------------------------------- #


def _load_compose_document() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8")) or {}


def _service_environment(service: dict) -> dict:
    environment = service.get("environment") or {}
    if isinstance(environment, dict):
        return {str(key): value for key, value in environment.items()}
    resolved: dict[str, str] = {}
    for item in environment:
        if isinstance(item, str) and "=" in item:
            key, value = item.split("=", 1)
            resolved[key] = value
    return resolved


def _control_credential_violations(control_service: dict) -> list[str]:
    """Independent re-implementation of the Control credential boundary."""
    return sorted(set(_service_environment(control_service)) & set(CONTROL_FORBIDDEN_KEYS))


def _valid_dr_evidence() -> dict:
    """A hand-built, contract-shaped evidence that must satisfy the DR gate."""
    return {
        "schema_version": 1,
        "backup": {
            # 60s old: inside the 300s RPO window.
            "completed_at_ms": NOW_MS - 60_000,
            "cadence_seconds": 300,
            "sha256": DIGEST_A,
            "object_key": "qts/backups/qts-drill.dump",
            "target": {
                "kind": "s3_compatible",
                "endpoint_host": "backup.example.internal",
                "bucket": "qts-backups",
                "off_host": True,
                "identity_verified": True,
                "attested_by": "operator-name",
            },
        },
        "restore": {
            # 30s old: inside the 7-day restore-evidence window.
            "completed_at_ms": NOW_MS - 30_000,
            "rto_seconds": 600,
            "backup_sha256": DIGEST_A,
            "verified": True,
            "source_database": {"host": "db-a", "database": "trading"},
            "target_database": {"host": "db-b", "database": "qts_restore_task"},
            "checks": {"facts_match": True, "invariants_ok": True},
        },
    }


def _journal(records: list[dict]) -> str:
    return "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)


def _succeeded_backup(completed_at_ms: int) -> dict:
    return {
        "schema_version": 1,
        "run_id": f"run-{completed_at_ms}",
        "status": "succeeded",
        "completed_at_ms": completed_at_ms,
        "cadence_seconds": 300,
        "backup": {"completed_at_ms": completed_at_ms, "sha256": DIGEST_A},
    }


def _load_alert_receiver_module():
    """Import the rehearsal receiver by path; it is a script, not a package."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "qts_independent_alert_receiver", SCRIPTS_DIR / "alert_receiver.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Receiver:
    """A real loopback HTTP receiver owned by this test process."""

    def __init__(self, handler_class):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        return False

    @property
    def webhook_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/webhook"

    @property
    def heartbeat_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/heartbeat"


def _import_scripts_checker():
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import importlib

        return importlib.import_module("check_credential_isolation")
    finally:
        sys.path.remove(str(SCRIPTS_DIR))


# --------------------------------------------------------------------------- #
# 5.1 - Compose topology
# --------------------------------------------------------------------------- #


def test_5_1_compose_covers_the_five_required_services():
    services = _load_compose_document().get("services") or {}
    missing = [name for name in REQUIRED_COMPOSE_SERVICES if name not in services]
    assert missing == [], f"compose.yml is missing required services: {missing}"


def test_5_1_core_services_reuse_the_new_stack_entry_points():
    services = _load_compose_document().get("services") or {}
    for name, module in EXPECTED_ENTRYPOINTS.items():
        command = services.get(name, {}).get("command") or []
        rendered = " ".join(str(part) for part in command)
        assert module in rendered, f"service {name} does not start {module}: {rendered!r}"


def test_5_1_no_fixed_container_name_and_a_parameterised_project():
    document = _load_compose_document()
    services = document.get("services") or {}
    fixed = {
        name: service["container_name"]
        for name, service in services.items()
        if isinstance(service, dict) and "container_name" in service
    }
    # Architecture 8.4: drills must isolate resources, so names, ports, volumes
    # and the project name stay parameterised.
    assert fixed == {}, f"fixed container_name collides across projects: {fixed}"
    assert "${" in str(document.get("name", "")), "compose project name is not parameterised"


# --------------------------------------------------------------------------- #
# 5.2 - Control holds no exchange write credentials, and fails closed
# --------------------------------------------------------------------------- #


def test_5_2_control_environment_declares_no_exchange_credential_key():
    services = _load_compose_document().get("services") or {}
    violations = _control_credential_violations(services.get("control") or {})
    assert violations == [], f"Control declares exchange credential keys: {violations}"


def test_5_2_control_mounts_no_exchange_secret_and_no_broad_env_file():
    services = _load_compose_document().get("services") or {}
    control = services.get("control") or {}
    secrets = control.get("secrets") or []
    assert list(secrets) == [], "Control must not receive exchange Docker secrets"
    env_file = control.get("env_file") or []
    if isinstance(env_file, str):
        env_file = [env_file]
    shared = [item for item in env_file if str(item).strip("./") == "env"]
    assert shared == [], "Control must not share a broad .env with Execution"


async def test_5_2_control_startup_refuses_when_a_credential_key_is_present(monkeypatch):
    from trader.runtime import control_main
    from trader.runtime.credentials import (
        CONTROL_EXCHANGE_CREDENTIALS_PRESENT,
        CredentialBoundaryError,
    )

    for key in CONTROL_FORBIDDEN_KEYS:
        monkeypatch.setenv(key, "independent-test-placeholder")
        with pytest.raises(CredentialBoundaryError) as caught:
            async with control_main.lifespan(control_main.app):
                pass
        assert caught.value.reason_code == CONTROL_EXCHANGE_CREDENTIALS_PRESENT, key
        monkeypatch.delenv(key)


def test_5_2_a_clean_control_environment_is_accepted():
    from trader.runtime.credentials import control_credential_violations

    clean = {
        "POSTGRES_HOST": "postgres",
        "QTS_ACCOUNT_ID": "binance_spot",
        "QTS_DR_REQUIRED": "1",
    }
    assert control_credential_violations(clean) == []


def test_5_2_mutation_a_leaked_control_key_is_detected(tmp_path):
    """T7: the credential check must fail on a mutated copy, not pass vacuously."""
    document = _load_compose_document()
    document["services"]["control"]["environment"]["BINANCE_API_KEY"] = "mutated-placeholder"
    mutated = tmp_path / "compose-leaked.yml"
    mutated.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")

    leaked = yaml.safe_load(mutated.read_text(encoding="utf-8"))["services"]["control"]
    assert _control_credential_violations(leaked) == ["BINANCE_API_KEY"]

    # Cross-check: the shipped operator script must reach the same verdict.
    checker = _import_scripts_checker()
    report = checker.check_compose(mutated)
    assert report["ok"] is False
    assert any("CONTROL_HAS_EXCHANGE_CREDENTIALS" in item for item in report["violations"])


def test_5_2_mutation_the_real_compose_file_passes_the_same_checker():
    """T7 companion: the checker must stay silent for the unmutated file."""
    checker = _import_scripts_checker()
    report = checker.check_compose(COMPOSE_PATH)
    assert report["violations"] == [], f"real compose.yml reports violations: {report}"


def test_5_2_compose_never_inlines_a_credential_value():
    rendered = COMPOSE_PATH.read_text(encoding="utf-8")
    for key in CONTROL_FORBIDDEN_KEYS:
        for match in re.finditer(rf"^\s*-?\s*{key}\s*[:=]\s*(?P<value>\S.*)$", rendered, re.M):
            value = match.group("value").strip().strip("\"'")
            # A configuration reference is fine; a literal value is not.
            assert value.startswith(
                ("${", "/run/secrets", "/var")
            ), f"possible literal credential for {key}: {value!r}"


# --------------------------------------------------------------------------- #
# 5.3 - Execution credentials: refusal reason codes and exit code 2
# --------------------------------------------------------------------------- #


def _credential_file(tmp_path: Path, name: str, content: str) -> str:
    target = tmp_path / name
    target.write_text(content, encoding="utf-8")
    return str(target)


def test_5_3_missing_credentials_produce_the_contract_reason_code(tmp_path):
    from trader.runtime.credentials import BINANCE_CREDENTIALS_MISSING, execution_credential_reason

    cases = {
        "no reference at all": {"BINANCE_ENV": "demo"},
        "empty key file": {
            "BINANCE_ENV": "demo",
            "BINANCE_API_KEY_FILE": _credential_file(tmp_path, "empty.txt", "   \n"),
            "BINANCE_SECRET_KEY_FILE": _credential_file(tmp_path, "secret.txt", "dummy-secret"),
        },
        "only the key is supplied": {
            "BINANCE_ENV": "demo",
            "BINANCE_API_KEY_FILE": _credential_file(tmp_path, "key.txt", "dummy-key"),
        },
        "empty direct value": {"BINANCE_ENV": "demo", "BINANCE_API_KEY": ""},
    }
    for label, environ in cases.items():
        assert execution_credential_reason(environ) == BINANCE_CREDENTIALS_MISSING, label


def test_5_3_unreadable_credential_file_produces_the_unreadable_code(tmp_path):
    from trader.runtime.credentials import (
        BINANCE_CREDENTIALS_UNREADABLE,
        execution_credential_reason,
    )

    directory = tmp_path / "as-a-directory"
    directory.mkdir()
    environ = {
        "BINANCE_ENV": "demo",
        "BINANCE_API_KEY_FILE": str(directory),
        "BINANCE_SECRET_KEY_FILE": str(tmp_path / "does-not-exist.txt"),
    }
    assert execution_credential_reason(environ) == BINANCE_CREDENTIALS_UNREADABLE


def test_5_3_non_sandbox_venue_environment_is_refused_before_credentials(tmp_path):
    from trader.runtime.credentials import BINANCE_ENV_NOT_SANDBOX, execution_credential_reason

    usable = {
        "BINANCE_API_KEY_FILE": _credential_file(tmp_path, "k.txt", "dummy-key"),
        "BINANCE_SECRET_KEY_FILE": _credential_file(tmp_path, "s.txt", "dummy-secret"),
    }
    for environment in ("production", "mainnet", "PRODUCTION", "live"):
        assert (
            execution_credential_reason({"BINANCE_ENV": environment, **usable})
            == BINANCE_ENV_NOT_SANDBOX
        ), environment


def test_5_3_positive_control_usable_sandbox_credentials_are_accepted(tmp_path):
    from trader.runtime.credentials import require_execution_credentials

    credentials = require_execution_credentials(
        {
            "BINANCE_ENV": "testnet",
            "BINANCE_API_KEY_FILE": _credential_file(tmp_path, "k.txt", "dummy-key"),
            "BINANCE_SECRET_KEY_FILE": _credential_file(tmp_path, "s.txt", "dummy-secret"),
        }
    )
    assert credentials.environment == "testnet"
    assert credentials.api_key == "dummy-key"
    assert credentials.secret_key == "dummy-secret"


def _run_execution_main(environ: dict[str, str]) -> subprocess.CompletedProcess:
    """Start the real Execution entry point; it must refuse before any IO."""
    child_env = dict(os.environ)
    for key in CONTROL_FORBIDDEN_KEYS:
        child_env.pop(key, None)
    child_env.update(environ)
    existing_pythonpath = child_env.get("PYTHONPATH", "")
    child_env["PYTHONPATH"] = (
        f"{ROOT}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(ROOT)
    )
    return subprocess.run(
        [sys.executable, "-m", "trader.runtime.trading_worker_main"],
        cwd=str(ROOT),
        env=child_env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def test_5_3_missing_credentials_exit_code_2_with_reason_and_no_secret(tmp_path):
    key_marker = "independent-key-marker-7Q2"
    secret_marker = "independent-secret-marker-7Q2"
    key_path = _credential_file(tmp_path, "key.txt", f"{key_marker}\n{secret_marker}\n")
    completed = _run_execution_main(
        {
            "BINANCE_ENV": "demo",
            "BINANCE_API_KEY_FILE": key_path,
            # No secret reference at all -> BINANCE_CREDENTIALS_MISSING (not
            # UNREADABLE, which is what a broken *_FILE path would produce).
        }
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 2, (
        f"Execution must refuse to start with exit code 2, got {completed.returncode}: "
        f"{output[-800:]}"
    )
    assert "EXECUTION_STARTUP_REFUSED reason=BINANCE_CREDENTIALS_MISSING" in output
    # Contract 13.16: a refusal log carries the reason code and never a value.
    assert key_marker not in output
    assert secret_marker not in output


def test_5_3_non_sandbox_environment_exit_code_2_with_its_own_reason():
    """Mutation companion: the observed reason code tracks the actual cause."""
    completed = _run_execution_main({"BINANCE_ENV": "production"})
    output = completed.stdout + completed.stderr
    assert completed.returncode == 2, f"got {completed.returncode}: {output[-800:]}"
    assert "EXECUTION_STARTUP_REFUSED reason=BINANCE_ENV_NOT_SANDBOX" in output
    assert "BINANCE_CREDENTIALS_MISSING" not in output


# --------------------------------------------------------------------------- #
# 5.4 - live / ready / trading are three distinct answers
# --------------------------------------------------------------------------- #


def _app_client(provider=None) -> TestClient:
    from trader.api.trading_app import create_trading_app

    return TestClient(create_trading_app(readiness_provider=provider))


def test_5_4_live_carries_no_reasons_and_stays_200_when_the_others_fail(monkeypatch):
    from trader.api.models.schemas import ComponentHealth
    from trader.api.routes import health as health_routes

    monkeypatch.setattr(
        health_routes,
        "_check_storage_health",
        lambda: ComponentHealth(status="unhealthy", message="storage down"),
    )
    with _app_client() as client:
        live = client.get("/health/live")
        assert live.status_code == 200
        assert live.json()["status"] == "ok"
        # Contract 13.16: liveness never carries reasons.
        assert "reasons" not in live.json()

        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["status"] == "degraded"
        assert ready.json()["reasons"] == ["STORAGE_UNAVAILABLE"]

        trading = client.get("/health/trading")
        assert trading.status_code == 503
        assert trading.json()["reasons"] == ["TRADING_RUNTIME_NOT_CONFIGURED"]


def test_5_4_trading_not_ready_is_503_with_reasons_and_never_degrades_live():
    class Blocked:
        async def check(self):
            return {
                "ready": False,
                "reasons": ["BALANCE_MISMATCH", "DR_EVIDENCE_MISSING"],
                "account_id": "acct",
                "db_available": True,
            }

    with _app_client(Blocked()) as client:
        trading = client.get("/health/trading")
        assert trading.status_code == 503
        body = trading.json()
        assert body["status"] == "blocked"
        assert body["ready"] is False
        assert set(body["reasons"]) == {"BALANCE_MISMATCH", "DR_EVIDENCE_MISSING"}
        # A blocked trading gate never degrades liveness or API readiness.
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200


def test_5_4_trading_ready_is_200_with_an_empty_reason_list():
    class Ready:
        async def check(self):
            return {"ready": True, "reasons": [], "account_id": "acct"}

    with _app_client(Ready()) as client:
        trading = client.get("/health/trading")
        assert trading.status_code == 200
        assert trading.json()["status"] == "ok"
        assert trading.json()["reasons"] == []


def test_5_4_a_failing_readiness_probe_is_a_503_not_a_500():
    class Broken:
        async def check(self):
            raise RuntimeError("dependency detail that must not crash the process")

    with _app_client(Broken()) as client:
        trading = client.get("/health/trading")
        assert trading.status_code == 503
        assert trading.json()["ready"] is False
        assert trading.json()["reasons"]
        assert client.get("/health/live").status_code == 200


def test_5_4_a_probe_claiming_ready_never_has_its_reasons_dropped():
    """Contract table: the 200 success shape is ready=true with reasons []."""

    class Inconsistent:
        async def check(self):
            return {"ready": True, "reasons": ["BALANCE_MISMATCH"], "account_id": "acct"}

    with _app_client(Inconsistent()) as client:
        body = client.get("/health/trading").json()
        assert body["reasons"], "reasons must never be silently dropped"


# --------------------------------------------------------------------------- #
# 5.5 - a real receiving service observes every blocker
# --------------------------------------------------------------------------- #


async def _blocked_readiness() -> dict:
    return {
        "ready": False,
        "reasons": list(BLOCKER_REASONS),
        "account_id": "independent_acct",
        "instance_owner": "instance-a",
    }


def _monitor(webhook_url: str, heartbeat_url: str, **transport_kwargs):
    from trader.services.alerting import AlertMonitor, WebhookAlertTransport

    return AlertMonitor(
        source="independent-control",
        account_id="independent_acct",
        readiness=_blocked_readiness,
        transport=WebhookAlertTransport(
            webhook_url,
            heartbeat_url=heartbeat_url,
            max_attempts=2,
            backoff_seconds=0.0,
            **transport_kwargs,
        ),
        interval_seconds=1.0,
        clock=lambda: int(time.time() * 1000),
    )


async def test_5_5_real_receiver_acknowledges_every_blocker_exactly_once(tmp_path):
    receiver_module = _load_alert_receiver_module()
    journal = tmp_path / "alerts.jsonl"
    store = receiver_module.AlertStore(str(journal), "")
    handler = receiver_module.build_handler(store, 30.0)

    with _Receiver(handler) as server:
        monitor = _monitor(server.webhook_url, server.heartbeat_url)
        first = await monitor.scan_once()
        second = await monitor.scan_once()
        await monitor.close()
        status = store.status(30.0)

    recorded = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    events = [item for item in recorded if item.get("kind") == "event"]
    heartbeats = [item for item in recorded if item.get("kind") == "heartbeat"]

    # Every required blocker family reaches the receiver.
    assert {item["code"] for item in events} == EXPECTED_ALERT_CODES
    assert {item["severity"] for item in events} == {"HIGH", "CRITICAL"}
    # Dedup by event_id: the second scan must not create duplicates.
    assert len(events) == 3, f"expected 3 deduplicated events, got {len(events)}"
    assert status["events_received"] == 3
    assert status["codes"] == sorted(EXPECTED_ALERT_CODES)
    # External heartbeat reachability is observable at the receiver.
    assert len(heartbeats) == 2
    assert status["heartbeats_received"] == 2
    assert status["last_heartbeat_at_ms"] is not None
    assert status["heartbeat_age_seconds"] is not None
    # Only acknowledged receipts count as delivered.
    assert [item["delivered"] for item in first["alerts"]] == [True, True, True]
    assert first["heartbeat"]["delivered"] is True
    assert sorted(first["delivered_event_ids"]) == sorted(second["delivered_event_ids"])
    assert second["pending_event_ids"] == []


async def test_5_5_a_200_response_without_ack_never_counts_as_delivered():
    class NoAckHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # noqa: A002
            return

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.dumps({"ack": False, "alert_id": "none"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with _Receiver(NoAckHandler) as server:
        monitor = _monitor(server.webhook_url, server.heartbeat_url)
        scan = await monitor.scan_once()
        await monitor.close()

    assert [item["delivered"] for item in scan["alerts"]] == [False, False, False]
    assert {item["error"] for item in scan["alerts"]} == {"ALERT_ACK_MISSING"}
    assert scan["delivered_event_ids"] == []
    assert len(scan["pending_event_ids"]) == 3


async def test_5_5_a_failing_transport_stays_pending_and_is_retried():
    class FailingHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        attempts = 0

        def log_message(self, *args):  # noqa: A002
            return

        def do_POST(self):  # noqa: N802
            type(self).attempts += 1
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = b'{"ack": false}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with _Receiver(FailingHandler) as server:
        monitor = _monitor(server.webhook_url, server.heartbeat_url)
        scan = await monitor.scan_once()
        await monitor.close()

    assert [item["delivered"] for item in scan["alerts"]] == [False, False, False]
    assert {item["status_code"] for item in scan["alerts"]} == {500}
    assert scan["delivered_event_ids"] == []
    assert len(scan["pending_event_ids"]) == 3
    # Three alerts plus one heartbeat, two attempts each.
    assert FailingHandler.attempts >= 8, f"retry did not happen: {FailingHandler.attempts}"


async def test_5_5_heartbeat_without_a_configured_url_is_not_delivered():
    from trader.services.alerting import WebhookAlertTransport

    transport = WebhookAlertTransport(
        "http://127.0.0.1:9/webhook", heartbeat_url="", max_attempts=1, backoff_seconds=0.0
    )
    result = await transport.send_heartbeat({"heartbeat_id": "hb-1", "source": "control"})
    await transport.close()
    assert result.delivered is False
    assert result.error == "ALERT_HEARTBEAT_URL_NOT_CONFIGURED"


async def test_5_5_a_signature_mismatch_is_refused_by_the_receiver(tmp_path):
    receiver_module = _load_alert_receiver_module()
    journal = tmp_path / "signed.jsonl"
    store = receiver_module.AlertStore(str(journal), "shared-independent-secret")
    handler = receiver_module.build_handler(store, 30.0)

    with _Receiver(handler) as server:
        good = _monitor(
            server.webhook_url, server.heartbeat_url, signing_secret="shared-independent-secret"
        )
        bad = _monitor(server.webhook_url, server.heartbeat_url, signing_secret="wrong-secret")
        good_scan = await good.scan_once()
        bad_scan = await bad.scan_once()
        await good.close()
        await bad.close()

    assert [item["delivered"] for item in good_scan["alerts"]] == [True, True, True]
    assert [item["delivered"] for item in bad_scan["alerts"]] == [False, False, False]
    assert {item["status_code"] for item in bad_scan["alerts"]} == {401}


# --------------------------------------------------------------------------- #
# 5.6 - cadence / manifest discipline (the off-host receipt itself stays blocked)
# --------------------------------------------------------------------------- #


def test_5_6_a_cadence_above_the_rpo_is_refused_at_configuration_time(tmp_path):
    from trader.ops.backup import BackupConfig, BackupError

    def config(interval: int) -> BackupConfig:
        return BackupConfig(
            dsn="postgresql://trader:pwd@127.0.0.1:5432/trading",
            output_dir=tmp_path,
            interval_seconds=interval,
        )

    with pytest.raises(BackupError, match="BACKUP_INTERVAL_EXCEEDS_RPO"):
        config(301)
    with pytest.raises(BackupError, match="BACKUP_INTERVAL_INVALID"):
        config(0)
    assert config(300).interval_seconds == 300


def test_5_6_rpo_needs_at_least_two_real_completions(tmp_path):
    from trader.ops.backup import summarize_rpo

    journal = tmp_path / "backup-journal.jsonl"
    base = NOW_MS

    journal.write_text(_journal([_succeeded_backup(base)]), encoding="utf-8")
    single = summarize_rpo(journal, 300)
    assert single["successful_backups"] == 1
    assert single["gaps_seconds"] == []
    assert single["rpo_satisfied"] is False, "a single backup cannot prove a cadence"

    journal.write_text(
        _journal([_succeeded_backup(base), _succeeded_backup(base + 400_000)]), encoding="utf-8"
    )
    slow = summarize_rpo(journal, 300)
    assert slow["max_gap_seconds"] == 400
    assert slow["rpo_satisfied"] is False

    journal.write_text(
        _journal(
            [
                _succeeded_backup(base),
                _succeeded_backup(base + 120_000),
                _succeeded_backup(base + 240_000),
            ]
        ),
        encoding="utf-8",
    )
    ok = summarize_rpo(journal, 300)
    assert ok["gaps_seconds"] == [120, 120]
    assert ok["max_gap_seconds"] == 120
    assert ok["rpo_satisfied"] is True


def test_5_6_failed_runs_are_not_counted_as_rpo_completions(tmp_path):
    from trader.ops.backup import summarize_rpo

    journal = tmp_path / "backup-journal.jsonl"
    journal.write_text(
        _journal(
            [
                _succeeded_backup(NOW_MS),
                {
                    "schema_version": 1,
                    "run_id": "run-failed",
                    "status": "failed",
                    "reason": "S3_READBACK_DIGEST_MISMATCH",
                    "completed_at_ms": NOW_MS + 60_000,
                },
                _succeeded_backup(NOW_MS + 120_000),
            ]
        ),
        encoding="utf-8",
    )
    summary = summarize_rpo(journal, 300)
    assert summary["successful_backups"] == 2
    assert summary["gaps_seconds"] == [120]


def test_5_6_a_failed_backup_manifest_is_rejected(tmp_path):
    from trader.ops.backup import BackupError, load_manifest

    manifest = tmp_path / "latest-backup.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "status": "failed", "reason": "PG_DUMP_EMPTY"}),
        encoding="utf-8",
    )
    with pytest.raises(BackupError, match="BACKUP_MANIFEST_NOT_SUCCESSFUL"):
        load_manifest(manifest)
    with pytest.raises(BackupError, match="BACKUP_MANIFEST_MISSING"):
        load_manifest(tmp_path / "absent.json")


async def test_5_6_an_unconfigured_object_target_fails_closed(tmp_path):
    from trader.ops.backup import BackupConfig, backup_once

    config = BackupConfig(
        dsn="postgresql://trader:pwd@127.0.0.1:5432/trading",
        output_dir=tmp_path / "out",
        interval_seconds=300,
        s3=None,
    )
    record = await backup_once(config, now_ms=NOW_MS)
    assert record["status"] == "failed"
    assert record["reason"] == "S3_TARGET_NOT_CONFIGURED"
    # The failure is persisted, not swallowed: a later run cannot read a success.
    assert (tmp_path / "out" / "latest-backup.json").is_file()
    assert (tmp_path / "out" / "backup-journal.jsonl").is_file()


def test_5_6_a_local_emulator_target_is_never_labelled_off_host(tmp_path):
    from trader.ops.backup import BackupConfig
    from trader.ops.s3 import S3Config

    config = BackupConfig(
        dsn="postgresql://trader:pwd@127.0.0.1:5432/trading",
        output_dir=tmp_path,
        interval_seconds=300,
        s3=S3Config(
            endpoint_url="http://127.0.0.1:9000",
            bucket="qts-phase5-drill",
            prefix="emulator/backups",
            access_key="placeholder",
            secret_key="placeholder",
        ),
        target_kind="local_emulator",
        off_host_attested=False,
        attested_by="tester",
    )
    identity = config.target_identity()
    assert identity["kind"] == "local_emulator"
    assert identity["off_host"] is False
    assert identity["identity_verified"] is False
    assert identity["endpoint_host"] == "127.0.0.1"


# --------------------------------------------------------------------------- #
# 5.9 - insufficient DR evidence keeps the system non-READY
# --------------------------------------------------------------------------- #


def _no_backup(evidence: dict) -> None:
    evidence.pop("backup")


def _no_restore(evidence: dict) -> None:
    evidence.pop("restore")


DR_MUTATIONS: list[tuple[str, object, str]] = [
    ("schema_version", lambda e: e.update(schema_version=2), "DR_EVIDENCE_INVALID"),
    ("backup_missing", _no_backup, "DR_BACKUP_EVIDENCE_MISSING"),
    (
        "backup_ts_zero",
        lambda e: e["backup"].update(completed_at_ms=0),
        "DR_BACKUP_EVIDENCE_MISSING",
    ),
    (
        "backup_stale",
        lambda e: e["backup"].update(completed_at_ms=NOW_MS - 400_000),
        "DR_BACKUP_STALE",
    ),
    (
        "cadence_over_rpo",
        lambda e: e["backup"].update(cadence_seconds=301),
        "DR_BACKUP_CADENCE_EXCEEDS_RPO",
    ),
    ("digest_shape", lambda e: e["backup"].update(sha256="abc"), "DR_BACKUP_EVIDENCE_MISSING"),
    (
        "off_host_false",
        lambda e: e["backup"]["target"].update(off_host=False),
        "DR_TARGET_NOT_VERIFIED_OFF_HOST",
    ),
    (
        "local_emulator_kind",
        lambda e: e["backup"]["target"].update(kind="local_emulator"),
        "DR_TARGET_NOT_VERIFIED_OFF_HOST",
    ),
    (
        "loopback_endpoint",
        lambda e: e["backup"]["target"].update(endpoint_host="127.0.0.1"),
        "DR_TARGET_NOT_VERIFIED_OFF_HOST",
    ),
    (
        "attested_by_empty",
        lambda e: e["backup"]["target"].update(attested_by=""),
        "DR_TARGET_NOT_VERIFIED_OFF_HOST",
    ),
    (
        "identity_unverified",
        lambda e: e["backup"]["target"].update(identity_verified=False),
        "DR_TARGET_NOT_VERIFIED_OFF_HOST",
    ),
    ("restore_missing", _no_restore, "DR_RESTORE_EVIDENCE_MISSING"),
    (
        "restore_unverified",
        lambda e: e["restore"].update(verified=False),
        "DR_RESTORE_UNVERIFIED",
    ),
    ("rto_exceeded", lambda e: e["restore"].update(rto_seconds=3601), "DR_RTO_EXCEEDED"),
    ("rto_missing", lambda e: e["restore"].pop("rto_seconds"), "DR_RTO_EXCEEDED"),
    (
        "digest_mismatch",
        lambda e: e["restore"].update(backup_sha256=DIGEST_B),
        "DR_RESTORE_BACKUP_MISMATCH",
    ),
    (
        "restore_stale",
        lambda e: e["restore"].update(completed_at_ms=NOW_MS - 8 * 86_400_000),
        "DR_RESTORE_EVIDENCE_STALE",
    ),
    (
        "same_database",
        lambda e: e["restore"].update(target_database={"host": "db-a", "database": "trading"}),
        "DR_RESTORE_TARGET_NOT_SEPARATE",
    ),
    (
        "target_identity_blank",
        lambda e: e["restore"].update(target_database={"host": "", "database": ""}),
        "DR_RESTORE_EVIDENCE_MISSING",
    ),
]


@pytest.mark.parametrize(
    ("label", "mutate", "expected_reason"),
    DR_MUTATIONS,
    ids=[item[0] for item in DR_MUTATIONS],
)
def test_5_9_every_insufficient_evidence_shape_keeps_the_gate_closed(
    label, mutate, expected_reason
):
    from trader.ops.dr_evidence import evaluate_dr_evidence

    evidence = _valid_dr_evidence()
    mutate(evidence)
    result = evaluate_dr_evidence(evidence, now_ms=NOW_MS)
    assert result.ready is False, f"{label} must not be READY"
    assert expected_reason in result.reasons, f"{label}: {result.reasons}"


def test_5_9_a_non_object_evidence_is_invalid():
    from trader.ops.dr_evidence import DR_EVIDENCE_INVALID, evaluate_dr_evidence

    for payload in ([], "evidence", 7, None):
        result = evaluate_dr_evidence(payload, now_ms=NOW_MS)
        assert result.ready is False
        assert DR_EVIDENCE_INVALID in result.reasons


def test_5_9_a_complete_evidence_passes_the_gate():
    from trader.ops.dr_evidence import evaluate_dr_evidence

    result = evaluate_dr_evidence(_valid_dr_evidence(), now_ms=NOW_MS)
    assert result.ready is True
    assert result.reasons == ()
    assert result.backup_age_seconds == 60
    assert result.restore_age_seconds == 30
    assert result.rto_seconds == 600


def test_5_9_missing_or_unparseable_evidence_files_are_refused(tmp_path):
    from trader.ops.dr_evidence import (
        DR_EVIDENCE_INVALID,
        DR_EVIDENCE_MISSING,
        evaluate_dr_evidence_file,
    )

    missing = evaluate_dr_evidence_file(tmp_path / "absent.json", now_ms=NOW_MS)
    assert missing.ready is False
    assert missing.reasons == (DR_EVIDENCE_MISSING,)

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert evaluate_dr_evidence_file(broken, now_ms=NOW_MS).reasons == (DR_EVIDENCE_INVALID,)


def test_5_9_the_gate_is_opt_in_but_never_silent_when_required(tmp_path):
    from trader.ops.dr_evidence import DR_EVIDENCE_PATH_NOT_CONFIGURED, dr_gate_from_env

    assert dr_gate_from_env({}) is None

    required = dr_gate_from_env({"QTS_DR_REQUIRED": "1"})
    assert required is not None
    assert required().reasons == (DR_EVIDENCE_PATH_NOT_CONFIGURED,)

    # This gate evaluates with wall-clock time, so the positive-control file
    # must be fresh relative to now rather than the frozen NOW_MS used above.
    live_evidence = _valid_dr_evidence()
    live_now = int(time.time() * 1000)
    live_evidence["backup"]["completed_at_ms"] = live_now - 60_000
    live_evidence["restore"]["completed_at_ms"] = live_now - 30_000
    path = tmp_path / "dr-evidence.json"
    path.write_text(json.dumps(live_evidence), encoding="utf-8")
    with_path = dr_gate_from_env({"QTS_DR_REQUIRED": "1", "QTS_DR_EVIDENCE_PATH": str(path)})
    assert with_path().ready is True


def test_5_9_mutation_emulator_evidence_is_rejected_by_the_gate():
    """T7: the DR gate must reject a local-emulator target, not accept it."""
    from trader.ops.dr_evidence import DR_TARGET_NOT_VERIFIED_OFF_HOST, evaluate_dr_evidence

    emulator = _valid_dr_evidence()
    emulator["backup"]["target"].update(
        {
            "kind": "local_emulator",
            "endpoint_host": "127.0.0.1",
            "off_host": False,
            "identity_verified": False,
        }
    )
    result = evaluate_dr_evidence(emulator, now_ms=NOW_MS)
    assert result.ready is False
    assert DR_TARGET_NOT_VERIFIED_OFF_HOST in result.reasons
    assert result.details["target_kind"] == "local_emulator"


def test_5_9_an_unverified_restore_is_visible_to_the_gate():
    from trader.ops.restore import verify_evidence_for_gate

    evidence = _valid_dr_evidence()
    evidence["restore"]["verified"] = False
    evidence["restore"]["checks"] = {"facts_match": False, "invariants_ok": True}
    verdict = verify_evidence_for_gate(evidence, now_ms=NOW_MS)
    assert verdict["ready"] is False
    assert "DR_RESTORE_UNVERIFIED" in verdict["reasons"]


async def test_5_9_restore_refuses_a_target_that_is_the_source_database(tmp_path):
    """A restore drill must never write back over the database it backs up."""
    from trader.ops.restore import RestoreConfig, RestoreError, restore_and_verify

    manifest = tmp_path / "latest-backup.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "succeeded",
                "backup": {
                    "sha256": DIGEST_A,
                    "object_key": "qts/backups/qts-drill.dump",
                    "completed_at_ms": NOW_MS,
                    "cadence_seconds": 300,
                    "source_database": {"host": "db-a", "database": "trading"},
                },
            }
        ),
        encoding="utf-8",
    )
    config = RestoreConfig(
        dsn="postgresql://trader:pwd@db-a:5432/trading",
        manifest_path=manifest,
        target_database="trading",
        output_dir=tmp_path,
    )
    with pytest.raises(RestoreError, match="RESTORE_TARGET_SAME_AS_SOURCE"):
        await restore_and_verify(config)
