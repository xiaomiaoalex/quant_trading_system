"""Independent counterexamples for the parent-review fixes (F-P1..F-P8).

Pre-classified in ``ci/test_manifest.json`` (offline group) so a fresh
independent tester can add tests without editing the manifest.  Expectations
are derived from ``docs/INTERFACE_CONTRACTS.md`` 13.17/13.18 and
``docs/CI_CD_RELEASE_RUNBOOK.md`` before reading any implementation; no
engineer or prior-tester conclusion is reused as an expected value.

Every test is offline: no socket is opened.  The only subprocess is the
read-only ``docker compose config`` render required by F-P6.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from scripts.collect_runtime_release_binding import (
    APP_SERVICES,
    EXPECTED_SERVICES,
    LABEL_ENVIRONMENT,
    LABEL_RELEASE_COMMIT,
    LABEL_RELEASE_ID,
    build_binding,
)
from scripts.deploy_gate import evaluate as evaluate_deploy_gate
from scripts.release_manifest import build_manifest
from scripts.summarize_observation import main as summarize_main
from scripts.verify_release_provenance import evaluate as evaluate_provenance

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-demo.yml"
EVIDENCE_WORKFLOW = ROOT / ".github" / "workflows" / "demo-evidence.yml"
RELEASE_BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "release-build.yml"

RELEASE_ID = "parentfix-rel-0001"
COMMIT = "c6fe450260eeb59d46c9caee591fb1625510b443"
DIGEST = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
IMAGE = "ghcr.io/example/qts@" + DIGEST
EXTERNAL_WEBHOOK = "https://alerts.example.com/parentfix"
EXTERNAL_HEARTBEAT = "https://alerts.example.com/parentfix/heartbeat"

INTERVAL_MS = 120_000
# 2026-11-04T00:00:00Z.  Samples and the binding must share a real window:
# a 24.1h window has 725 samples, so the last one sits at +724*INTERVAL_MS
# and the binding is generated one minute after the window ends.
OBSERVATION_START_MS = 1_793_750_400_000
WINDOW_LAST_SAMPLE_MS = OBSERVATION_START_MS + 724 * INTERVAL_MS
BINDING_GENERATED_AT_MS = WINDOW_LAST_SAMPLE_MS + 60_000


def _iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Shared input builders (hand-computed expectations, not derived from code).
# --------------------------------------------------------------------------- #


def _strict_metrics() -> dict:
    return {
        "reconciliation_age_ms": 1_000,
        "outbox_backlog": 0,
        "unbooked_fills": 0,
        "unknown_commands": 0,
        "balance_anomalies": 0,
        "unresolved": [],
    }


def _write_observation(
    path: Path,
    *,
    hours: float,
    strict_metrics: bool = True,
    mutate=None,
    ready: bool = True,
) -> Path:
    count = int(hours * 3_600_000 / INTERVAL_MS) + 2
    lines = []
    for index in range(count):
        sample = {
            "ts_ms": OBSERVATION_START_MS + index * INTERVAL_MS,
            "health": {"status": 200, "body": {"ready": ready}},
            "runtime": {
                "release_id": RELEASE_ID,
                "commit": COMMIT,
                "image_digest": DIGEST,
                "environment": "demo",
                "observed_at_ms": OBSERVATION_START_MS + index * INTERVAL_MS,
            },
        }
        if strict_metrics:
            sample["metrics"] = _strict_metrics()
        if mutate is not None:
            mutate(sample)
        lines.append(json.dumps(sample))
    lines.append(json.dumps({"ts_ms": OBSERVATION_START_MS + 1_000, "marker": "runtime_restart"}))
    lines.append(
        json.dumps({"ts_ms": OBSERVATION_START_MS + 2_000, "marker": "runtime_restart_end"})
    )
    lines.append(json.dumps({"ts_ms": OBSERVATION_START_MS + 3_000, "marker": "active_reconnect"}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _app_container_facts() -> dict:
    """Actual per-container facts for every release-bearing app service."""
    return {
        service: {
            "name": f"{service}-1",
            "present": True,
            "running": True,
            "health": "healthy",
            "image": IMAGE,
            "image_id": "image-id-" + service,
            "labels": {
                LABEL_RELEASE_ID: RELEASE_ID,
                LABEL_RELEASE_COMMIT: COMMIT,
                LABEL_ENVIRONMENT: "demo",
            },
        }
        for service in APP_SERVICES
    }


def _runtime_identity() -> dict:
    """The identity the Control runtime reported for the bound window."""
    return {
        "release_id": RELEASE_ID,
        "commit": COMMIT,
        "image_digest": DIGEST,
        "environment": "demo",
        "instance_owner": "worker-a",
        "lease_token": "7",
    }


def _valid_binding(generated_at_ms: int | None = None) -> dict:
    return {
        "schema_version": 1,
        "runtime_inspected": True,
        "errors": [],
        "generated_at": _iso_utc(generated_at_ms or BINDING_GENERATED_AT_MS),
        "compose_project": "qts_demo_parentfix",
        "environment": "demo",
        "release_id": RELEASE_ID,
        "commit": COMMIT,
        "image_digest": DIGEST,
        "containers": _app_container_facts(),
        "health": {
            "live": {"status": 200},
            "ready": {"status": 200},
            "trading": {"status": 200, "body": {"ready": True}},
        },
        "runtime": _runtime_identity(),
        "checks": {
            "all_running": True,
            "all_healthy": True,
            "labels_match": True,
            "image_digest_match": True,
            "trading_ready": True,
        },
    }


def _run_summarizer(monkeypatch, tmp_path, argv):
    summary_path = tmp_path / "summary.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["summarize_observation.py", *argv, "--json-out", str(summary_path)],
    )
    exit_code = summarize_main()
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    return exit_code, payload


def _strict_argv(tmp_path, observation: Path, binding: Path, *, min_hours: str = "24"):
    return [
        "--input",
        str(observation),
        "--min-hours",
        min_hours,
        "--strict-release",
        "--release-evidence",
        str(binding),
        "--expected-commit",
        COMMIT,
        "--expected-digest",
        DIGEST,
    ]


def _write_binding(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "release-binding.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _healthy_containers() -> dict:
    containers = {}
    for service in EXPECTED_SERVICES:
        if service in APP_SERVICES:
            containers[service] = dict(_app_container_facts()[service])
        else:
            containers[service] = {
                "name": f"{service}-1",
                "present": True,
                "running": True,
                "health": "healthy",
                "image": f"registry.example/infrastructure@{DIGEST}",
                "image_id": "image-id-" + service,
                "labels": {},
            }
    return containers


def _healthy_control_health() -> dict:
    return {
        "live": {"status": 200},
        "ready": {"status": 200},
        "trading": {"status": 200, "body": {"ready": True}},
    }


def _clean_facts() -> dict:
    return {
        "new_risk_blocked": True,
        "unknown_commands": 0,
        "queued_commands": 0,
        "sending_commands": 0,
        "outbox_pending": 0,
        "unbooked_fills": 0,
        "lease": {"owner": "worker-a", "fencing_token": "7", "valid": True},
        "runtime": {"owner": "worker-a", "token": "7", "ready": True},
    }


def _provenance_run() -> dict:
    return {
        "id": "4242",
        "repository": {"full_name": "example/quant_trading_system"},
        "path": ".github/workflows/release-build.yml",
        "event": "workflow_run",
        "head_branch": "main",
        "status": "completed",
        "conclusion": "success",
        "head_sha": COMMIT,
    }


def _provenance_manifest() -> dict:
    return {
        "commit": COMMIT,
        "image_digest": IMAGE,
        "release_id": RELEASE_ID,
        "environment": "demo",
    }


def _provenance_kwargs(**overrides):
    kwargs = {
        "run": _provenance_run(),
        "manifest": _provenance_manifest(),
        "repository": "example/quant_trading_system",
        "run_id": "4242",
        "commit": COMMIT,
        "digest": DIGEST,
        "release_id": RELEASE_ID,
    }
    kwargs.update(overrides)
    return kwargs


def _preflight_evidence() -> dict:
    artifact = {"present": True, "kind": "file", "sha256": "0" * 64, "path": "evidence"}
    return {
        "junit": [dict(artifact)],
        "coverage": dict(artifact),
        "frontend": dict(artifact),
        "sbom": dict(artifact),
        "scan": dict(artifact),
        "startup_smoke": dict(artifact),
    }


def _preflight_manifest(smoke_passed: bool = True) -> dict:
    return {
        "commit": COMMIT,
        "image_digest": DIGEST,
        "release_id": RELEASE_ID,
        "environment": "demo",
        "dependencies": {
            "ci_constraints": {"present": True, "kind": "file", "sha256": "0" * 64},
            "runtime_constraints": {"present": True, "kind": "file", "sha256": "0" * 64},
        },
        "evidence": _preflight_evidence(),
        "startup_smoke_summary": {
            "passed": smoke_passed,
            "image": IMAGE,
            "image_digest_ok": True,
        },
    }


def _preflight_env() -> dict:
    return {
        "BINANCE_ENV": "demo",
        "POSTGRES_PASSWORD": "parentfix-strong-password",
        "QTS_ALERT_SIGNING_SECRET": "parentfix-alert-secret",
        "QTS_AUTH_REQUIRED": "1",
        "QTS_PASSWORD_HASH": "parentfix-hash",
        "QTS_USERNAME": "owner",
        "QTS_ACCOUNT_ID": "binance_spot_demo_parentfix",
        "QTS_SYMBOLS": "BTCUSDT,ETHUSDT",
        "QTS_INSTRUMENTS": json.dumps({"BTCUSDT": ["BTC", "USDT"], "ETHUSDT": ["ETH", "USDT"]}),
        "QTS_STRATEGY_MODULE": "trader.strategies.parentfix",
        "QTS_STRATEGY_WARMUP_EVENTS": "200",
        "QTS_EXTERNAL_ALERT_WEBHOOK_URL": EXTERNAL_WEBHOOK,
        "QTS_EXTERNAL_ALERT_HEARTBEAT_URL": EXTERNAL_HEARTBEAT,
        "QTS_DR_REQUIRED": "1",
        "QTS_BACKUP_RPO_SECONDS": "300",
        "QTS_BACKUP_OFF_HOST_ATTESTED": "1",
        "QTS_BACKUP_ATTESTED_BY": "operator",
        "QTS_RELEASE_ID": RELEASE_ID,
        "QTS_RELEASE_COMMIT": COMMIT,
        "QTS_RELEASE_IMAGE": IMAGE,
        "QTS_EXPECTED_DIGEST": DIGEST,
        "QTS_SCHEMA_VERSION": "3",
        "QTS_PREVIOUS_IMAGE_DIGEST": DIGEST,
    }


def _smoke_file(tmp_path: Path, **overrides) -> Path:
    payload = {"passed": True, "image": IMAGE, "image_digest_ok": True, "health": {}}
    payload.update(overrides)
    path = tmp_path / "compose-smoke.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _manifest_args(tmp_path: Path, **overrides) -> SimpleNamespace:
    junit = tmp_path / "junit.xml"
    junit.write_text("<testsuite/>", encoding="utf-8")
    coverage = tmp_path / "coverage.xml"
    coverage.write_text("<coverage/>", encoding="utf-8")
    frontend = tmp_path / "dist"
    frontend.mkdir(exist_ok=True)
    (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
    sbom = tmp_path / "sbom.cdx.json"
    sbom.write_text("{}", encoding="utf-8")
    scan = tmp_path / "trivy.sarif"
    scan.write_text("{}", encoding="utf-8")
    values = {
        "release_id": RELEASE_ID,
        "commit": COMMIT,
        "ref": "refs/heads/main",
        "workflow": "release-build",
        "workflow_run": "4242",
        "image_digest": IMAGE,
        "tree_clean": True,
        "schema_version": 3,
        "environment": "demo",
        "ci_constraints": str(ROOT / "trader" / "requirements-ci.constraints.txt"),
        "runtime_constraints": str(ROOT / "trader" / "requirements-runtime.constraints.txt"),
        "junit": [str(junit)],
        "coverage": str(coverage),
        "frontend": str(frontend),
        "sbom": str(sbom),
        "scan": str(scan),
        "startup_smoke": overrides.get("startup_smoke") or str(_smoke_file(tmp_path)),
        "allow_missing_evidence": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# --------------------------------------------------------------------------- #
# F-P1: strict observation window must be at least 24 hours.
# --------------------------------------------------------------------------- #


def test_strict_window_shorter_than_24h_blocks_release(monkeypatch, tmp_path):
    observation = _write_observation(tmp_path / "obs.jsonl", hours=1.0)
    binding = _write_binding(tmp_path, _valid_binding())
    exit_code, summary = _run_summarizer(
        monkeypatch,
        tmp_path,
        _strict_argv(tmp_path, observation, binding, min_hours="1"),
    )
    assert exit_code == 1
    assert summary["effective_min_hours"] == 24
    assert "STRICT_WINDOW_SHORTER_THAN_24H" in summary["blockers"]
    assert "WINDOW_SHORTER_THAN_MIN" in summary["blockers"]


def test_strict_min_hours_below_24_is_a_usage_error(monkeypatch, tmp_path, capsys):
    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1)
    binding = _write_binding(tmp_path, _valid_binding())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "summarize_observation.py",
            "--input",
            str(observation),
            "--strict-release",
            "--strict-min-hours",
            "12",
            "--release-evidence",
            str(binding),
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        summarize_main()
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert "--strict-min-hours" in stderr
    assert "24" in stderr


def test_strict_full_24h_window_with_runtime_binding_passes(monkeypatch, tmp_path):
    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1)
    binding = _write_binding(tmp_path, _valid_binding())
    exit_code, summary = _run_summarizer(
        monkeypatch,
        tmp_path,
        _strict_argv(tmp_path, observation, binding),
    )
    assert exit_code == 0, summary["blockers"]
    assert summary["ok"] is True
    assert summary["blockers"] == []
    assert summary["duration_hours"] >= 24


# --------------------------------------------------------------------------- #
# F-P2: strict mode must reject hand-declared release bindings and the
# collector must fail with stable error codes on bad runtime facts.
# --------------------------------------------------------------------------- #


def test_strict_rejects_binding_without_runtime_inspected_flag(monkeypatch, tmp_path):
    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1)
    payload = _valid_binding()
    del payload["runtime_inspected"]
    binding = _write_binding(tmp_path, payload)
    exit_code, summary = _run_summarizer(
        monkeypatch, tmp_path, _strict_argv(tmp_path, observation, binding)
    )
    assert exit_code == 1
    assert "RELEASE_RUNTIME_BINDING_MISSING" in summary["blockers"]


def test_strict_rejects_binding_without_runtime_checks(monkeypatch, tmp_path):
    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1)
    payload = _valid_binding()
    del payload["checks"]
    binding = _write_binding(tmp_path, payload)
    exit_code, summary = _run_summarizer(
        monkeypatch, tmp_path, _strict_argv(tmp_path, observation, binding)
    )
    assert exit_code == 1
    assert "RELEASE_RUNTIME_CHECKS_MISSING" in summary["blockers"]


def test_strict_rejects_binding_without_containers(monkeypatch, tmp_path):
    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1)
    payload = _valid_binding()
    del payload["containers"]
    binding = _write_binding(tmp_path, payload)
    exit_code, summary = _run_summarizer(
        monkeypatch, tmp_path, _strict_argv(tmp_path, observation, binding)
    )
    assert exit_code == 1
    assert "RELEASE_RUNTIME_CONTAINERS_MISSING" in summary["blockers"]


def test_strict_rejects_binding_with_failed_runtime_check(monkeypatch, tmp_path):
    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1)
    payload = _valid_binding()
    payload["checks"]["all_healthy"] = False
    binding = _write_binding(tmp_path, payload)
    exit_code, summary = _run_summarizer(
        monkeypatch, tmp_path, _strict_argv(tmp_path, observation, binding)
    )
    assert exit_code == 1
    assert "RELEASE_RUNTIME_CHECK_FAILED:all_healthy" in summary["blockers"]


def test_strict_rejects_binding_mismatched_release_identity(monkeypatch, tmp_path):
    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1)
    payload = _valid_binding()
    payload["commit"] = "f" * 40
    payload["image_digest"] = "sha256:" + "f" * 64
    payload["environment"] = "production"
    payload["release_id"] = ""
    binding = _write_binding(tmp_path, payload)
    exit_code, summary = _run_summarizer(
        monkeypatch, tmp_path, _strict_argv(tmp_path, observation, binding)
    )
    assert exit_code == 1
    blockers = summary["blockers"]
    assert "RELEASE_COMMIT_MISMATCH" in blockers
    assert "RELEASE_DIGEST_MISMATCH" in blockers
    assert "RELEASE_ENVIRONMENT_NOT_DEMO" in blockers
    assert "RELEASE_ID_MISSING" in blockers


def test_strict_requires_release_evidence_at_all(monkeypatch, tmp_path):
    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1)
    exit_code, summary = _run_summarizer(
        monkeypatch,
        tmp_path,
        ["--input", str(observation), "--strict-release"],
    )
    assert exit_code == 1
    assert "RELEASE_EVIDENCE_MISSING" in summary["blockers"]


def test_strict_rejects_sample_runtime_identity_mismatch(monkeypatch, tmp_path):
    def rebind(sample):
        sample["runtime"]["release_id"] = "other-release"

    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1, mutate=rebind)
    binding = _write_binding(tmp_path, _valid_binding())
    exit_code, summary = _run_summarizer(
        monkeypatch, tmp_path, _strict_argv(tmp_path, observation, binding)
    )
    assert exit_code == 1
    assert "SAMPLE_RUNTIME_IDENTITY_MISMATCH:release_id" in summary["blockers"]


def test_strict_rejects_missing_sample_runtime_identity(monkeypatch, tmp_path):
    def strip_identity(sample):
        del sample["runtime"]

    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1, mutate=strip_identity)
    binding = _write_binding(tmp_path, _valid_binding())
    exit_code, summary = _run_summarizer(
        monkeypatch, tmp_path, _strict_argv(tmp_path, observation, binding)
    )
    assert exit_code == 1
    assert "SAMPLE_RUNTIME_IDENTITY_MISSING" in summary["blockers"]


def test_strict_rejects_binding_generated_outside_the_window(monkeypatch, tmp_path):
    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1)
    # Ten minutes before the first sample: the binding cannot describe this
    # window (it would be a binding of an earlier release).
    stale = _valid_binding(generated_at_ms=OBSERVATION_START_MS - 600_000)
    binding = _write_binding(tmp_path, stale)
    exit_code, summary = _run_summarizer(
        monkeypatch, tmp_path, _strict_argv(tmp_path, observation, binding)
    )
    assert exit_code == 1
    assert "RELEASE_BINDING_WINDOW_MISMATCH" in summary["blockers"]


def _binding_containers(**mutations) -> dict:
    containers = {}
    for service in EXPECTED_SERVICES:
        if service in APP_SERVICES:
            item = dict(_app_container_facts()[service])
        else:
            item = {
                "name": f"{service}-1",
                "present": True,
                "running": True,
                "health": "healthy",
                "image": f"registry.example/infrastructure@{DIGEST}",
                "image_id": "image-id-" + service,
                "labels": {},
            }
        item.update(mutations.get(service, {}))
        containers[service] = item
    return containers


def _binding_health(**overrides) -> dict:
    health = {
        "live": {"status": 200},
        "ready": {"status": 200},
        "trading": {"status": 200, "body": {"ready": True}},
    }
    health.update(overrides)
    return health


def test_build_binding_reports_missing_container():
    containers = _binding_containers()
    del containers["control"]
    binding = build_binding(
        project="p",
        containers=containers,
        health=_binding_health(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
        runtime=_runtime_identity(),
    )
    assert "CONTAINER_MISSING:control" in binding["errors"]
    assert binding["checks"]["all_healthy"] is False


def test_build_binding_reports_not_running_container():
    containers = _binding_containers(execution={"running": False, "present": False})
    binding = build_binding(
        project="p",
        containers=containers,
        health=_binding_health(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
        runtime=_runtime_identity(),
    )
    assert "CONTAINER_NOT_RUNNING:execution" in binding["errors"]
    assert binding["checks"]["all_running"] is False


def test_build_binding_reports_unhealthy_container():
    containers = _binding_containers(execution={"health": "starting"})
    binding = build_binding(
        project="p",
        containers=containers,
        health=_binding_health(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
        runtime=_runtime_identity(),
    )
    assert "CONTAINER_NOT_HEALTHY:execution:starting" in binding["errors"]


def test_build_binding_reports_label_mismatch():
    containers = _binding_containers(
        control={
            "labels": {
                LABEL_RELEASE_ID: "other-release",
                LABEL_RELEASE_COMMIT: COMMIT,
                LABEL_ENVIRONMENT: "demo",
            }
        }
    )
    binding = build_binding(
        project="p",
        containers=containers,
        health=_binding_health(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
        runtime=_runtime_identity(),
    )
    assert "LABEL_RELEASE_ID_MISMATCH:control" in binding["errors"]
    assert binding["checks"]["labels_match"] is False


def test_build_binding_reports_digest_mismatch():
    containers = _binding_containers(execution={"image": "ghcr.io/example/qts:latest"})
    binding = build_binding(
        project="p",
        containers=containers,
        health=_binding_health(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
        runtime=_runtime_identity(),
    )
    assert "IMAGE_DIGEST_MISMATCH:execution" in binding["errors"]
    assert binding["checks"]["image_digest_match"] is False


def test_build_binding_reports_unready_trading_health():
    health = _binding_health(
        trading={"status": 503, "body": {"ready": False, "reasons": ["RECONCILING"]}}
    )
    binding = build_binding(
        project="p",
        containers=_binding_containers(),
        health=health,
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )
    assert "HEALTH_TRADING_NOT_READY" in binding["errors"]
    assert binding["checks"]["trading_ready"] is False


def test_build_binding_positive_produces_inspected_binding():
    binding = build_binding(
        project="p",
        containers=_binding_containers(),
        health=_binding_health(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
        runtime=_runtime_identity(),
    )
    assert binding["errors"] == []
    assert binding["runtime_inspected"] is True
    assert all(binding["checks"].values()), binding["checks"]
    assert binding["environment"] == "demo"
    assert binding["runtime"]["instance_owner"] == "worker-a"
    assert binding["runtime"]["lease_token"] == "7"


def test_build_binding_requires_runtime_identity():
    binding = build_binding(
        project="p",
        containers=_binding_containers(),
        health=_binding_health(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )
    assert "RUNTIME_IDENTITY_MISSING" in binding["errors"]
    assert binding["checks"]["trading_ready"] is True  # containers/health were fine

    identity = _runtime_identity()
    identity["instance_owner"] = ""
    binding = build_binding(
        project="p",
        containers=_binding_containers(),
        health=_binding_health(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
        runtime=identity,
    )
    assert "RUNTIME_IDENTITY_MISSING:instance_owner" in binding["errors"]


def test_build_binding_reports_runtime_identity_mismatch():
    identity = _runtime_identity()
    identity["release_id"] = "other-release"
    binding = build_binding(
        project="p",
        containers=_binding_containers(),
        health=_binding_health(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
        runtime=identity,
    )
    assert "RUNTIME_IDENTITY_MISMATCH:release_id" in binding["errors"]


# --------------------------------------------------------------------------- #
# F-P3: deploy gate pre/post counterexamples plus shared concurrency group.
# --------------------------------------------------------------------------- #


def test_pre_gate_blocks_unclosed_new_risk():
    facts = _clean_facts()
    facts["new_risk_blocked"] = False
    errors = evaluate_deploy_gate(
        mode="pre",
        containers=_healthy_containers(),
        health=_healthy_control_health(),
        facts=facts,
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )
    assert "NEW_RISK_NOT_BLOCKED" in errors


def test_gate_blocks_unknown_commands():
    facts = _clean_facts()
    facts["unknown_commands"] = 2
    errors = evaluate_deploy_gate(
        mode="pre",
        containers=_healthy_containers(),
        health=_healthy_control_health(),
        facts=facts,
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )
    assert "UNKNOWN_COMMANDS_PRESENT" in errors


def test_gate_blocks_outbox_backlog():
    facts = _clean_facts()
    facts["outbox_pending"] = 1
    errors = evaluate_deploy_gate(
        mode="pre",
        containers=_healthy_containers(),
        health=_healthy_control_health(),
        facts=facts,
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )
    assert "OUTBOX_PENDING" in errors


def test_gate_blocks_invalid_lease():
    facts = _clean_facts()
    facts["lease"]["valid"] = False
    errors = evaluate_deploy_gate(
        mode="pre",
        containers=_healthy_containers(),
        health=_healthy_control_health(),
        facts=facts,
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )
    assert "EXECUTION_LEASE_INVALID" in errors


def test_gate_blocks_owner_mismatch():
    facts = _clean_facts()
    facts["runtime"]["owner"] = "worker-b"
    errors = evaluate_deploy_gate(
        mode="pre",
        containers=_healthy_containers(),
        health=_healthy_control_health(),
        facts=facts,
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )
    assert "EXECUTION_OWNER_NOT_ALIGNED" in errors


def test_gate_blocks_fencing_mismatch():
    facts = _clean_facts()
    facts["runtime"]["token"] = "8"
    errors = evaluate_deploy_gate(
        mode="pre",
        containers=_healthy_containers(),
        health=_healthy_control_health(),
        facts=facts,
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )
    assert "EXECUTION_FENCING_NOT_ALIGNED" in errors


def test_post_gate_blocks_unbooked_fills():
    facts = _clean_facts()
    facts["unbooked_fills"] = 3
    kwargs = {
        "containers": _healthy_containers(),
        "health": _healthy_control_health(),
        "facts": facts,
        "expected_release_id": RELEASE_ID,
        "expected_commit": COMMIT,
        "expected_digest": DIGEST,
    }
    # The pre gate rejects unbooked fills as well: a release that cannot even
    # account for its own executions must not be promoted.
    assert "UNBOOKED_FILLS_PRESENT" in evaluate_deploy_gate(mode="pre", **kwargs)
    assert "UNBOOKED_FILLS_PRESENT" in evaluate_deploy_gate(mode="post", **kwargs)


def test_gate_requires_every_convergence_counter_fail_closed():
    base = {
        "containers": _healthy_containers(),
        "health": _healthy_control_health(),
        "expected_release_id": RELEASE_ID,
        "expected_commit": COMMIT,
        "expected_digest": DIGEST,
    }
    for key in ("unknown_commands", "queued_commands", "sending_commands", "outbox_pending"):
        facts = _clean_facts()
        del facts[key]
        errors = evaluate_deploy_gate(mode="pre", facts=facts, **base)
        assert f"FACTS_INVALID:{key}" in errors

    facts = _clean_facts()
    facts["queued_commands"] = 2
    errors = evaluate_deploy_gate(mode="post", facts=facts, **base)
    assert "COMMANDS_NOT_CONVERGED:QUEUED" in errors


def test_post_gate_blocks_unhealthy_container():
    containers = _healthy_containers()
    containers["execution"]["health"] = "unhealthy"
    errors = evaluate_deploy_gate(
        mode="post",
        containers=containers,
        health=_healthy_control_health(),
        facts=_clean_facts(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )
    assert "CONTAINER_NOT_HEALTHY:execution:unhealthy" in errors


def test_post_gate_blocks_label_mismatch():
    containers = _healthy_containers()
    containers["control"]["labels"][LABEL_RELEASE_ID] = "stale-release"
    errors = evaluate_deploy_gate(
        mode="post",
        containers=containers,
        health=_healthy_control_health(),
        facts=_clean_facts(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )
    assert "LABEL_RELEASE_ID_MISMATCH:control" in errors


def test_post_gate_blocks_digest_mismatch():
    containers = _healthy_containers()
    containers["execution"]["image"] = "ghcr.io/example/qts:latest"
    errors = evaluate_deploy_gate(
        mode="post",
        containers=containers,
        health=_healthy_control_health(),
        facts=_clean_facts(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )
    assert "IMAGE_DIGEST_MISMATCH:execution" in errors


def test_post_gate_blocks_trading_not_ready():
    health = _healthy_control_health()
    health["trading"] = {"status": 503, "body": {"ready": False, "reasons": ["DB_DEGRADED"]}}
    errors = evaluate_deploy_gate(
        mode="post",
        containers=_healthy_containers(),
        health=health,
        facts=_clean_facts(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )
    assert any(code.startswith("HEALTH_TRADING_NOT_READY") for code in errors)


def test_post_gate_accepts_intentional_new_risk_block_503():
    health = _healthy_control_health()
    health["trading"] = {"status": 503, "body": {"ready": False, "reasons": ["NEW_RISK_BLOCKED"]}}
    errors = evaluate_deploy_gate(
        mode="post",
        containers=_healthy_containers(),
        health=health,
        facts=_clean_facts(),
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )
    assert errors == []


def test_pre_and_post_gates_pass_on_clean_inputs():
    kwargs = {
        "containers": _healthy_containers(),
        "health": _healthy_control_health(),
        "facts": _clean_facts(),
        "expected_release_id": RELEASE_ID,
        "expected_commit": COMMIT,
        "expected_digest": DIGEST,
    }
    assert evaluate_deploy_gate(mode="pre", **kwargs) == []
    assert evaluate_deploy_gate(mode="post", **kwargs) == []


def test_deploy_and_evidence_workflows_share_demo_concurrency_group():
    deploy = yaml.safe_load(DEPLOY_WORKFLOW.read_text(encoding="utf-8"))
    evidence = yaml.safe_load(EVIDENCE_WORKFLOW.read_text(encoding="utf-8"))
    deploy_group = deploy["concurrency"]["group"]
    evidence_group = evidence["concurrency"]["group"]
    assert deploy_group == evidence_group
    assert deploy_group.startswith("qts-demo-")


# --------------------------------------------------------------------------- #
# F-P4: release-build must prove the published digest actually starts.
# --------------------------------------------------------------------------- #


def _build_job():
    workflow = yaml.safe_load(RELEASE_BUILD_WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["build-image"]


def _step_with_fragment(steps, fragment):
    matches = [
        step
        for step in steps
        if fragment in str(step.get("run", "")) or fragment in str(step.get("uses", ""))
    ]
    assert len(matches) == 1, fragment
    return matches[0]


def test_release_build_runs_digest_pinned_startup_smoke_on_publish():
    steps = _build_job()["steps"]
    smoke = _step_with_fragment(steps, "compose_smoke.py")
    assert "--require-digest" in smoke["run"]
    assert '--image "${IMAGE_NAME}@${{ steps.build.outputs.digest }}"' in smoke["run"]
    assert smoke["if"] == "env.PUBLISH == 'true'"
    manifest = _step_with_fragment(steps, "release_manifest.py")
    assert "--startup-smoke" in manifest["run"]
    assert manifest["if"] == "env.PUBLISH == 'true'"
    assert steps.index(manifest) > steps.index(smoke)


def test_release_build_pr_path_never_claims_release_startup():
    job = _build_job()
    assert "pull_request" in job["if"]
    steps = job["steps"]
    build = _step_with_fragment(steps, "docker/build-push-action")
    assert build["with"]["push"] == "${{ env.PUBLISH == 'true' }}"
    publish_only_steps = [
        _step_with_fragment(steps, "compose_smoke.py"),
        _step_with_fragment(steps, "release_manifest.py"),
        _step_with_fragment(steps, "docker/login-action"),
        _step_with_fragment(steps, "actions/download-artifact"),
    ]
    for step in publish_only_steps:
        assert step.get("if") == "env.PUBLISH == 'true'", step.get("name")


def test_release_build_upload_requires_evidence_files():
    steps = _build_job()["steps"]
    uploads = [
        step
        for step in steps
        if step.get("uses", "").startswith("actions/upload-artifact")
        and (step.get("with") or {}).get("name") == "release-evidence"
    ]
    assert len(uploads) == 1
    upload = uploads[0]
    assert upload["if"] == "env.PUBLISH == 'true'"
    assert upload["with"]["if-no-files-found"] == "error"
    # One bundle root: the manifest sits at the artifact top level and the
    # startup smoke summary under reports/compose-smoke/, so the reviewer
    # downloads a single artifact with a stable internal layout.
    assert upload["with"]["path"] == "reports/"
    manifest = _step_with_fragment(steps, "release_manifest.py")
    assert "--startup-smoke reports/compose-smoke/compose-smoke.json" in manifest["run"]
    assert "--output reports/release-manifest.json" in manifest["run"]


# --------------------------------------------------------------------------- #
# F-P5: provenance gate for protected workflows, plus static step ordering.
# --------------------------------------------------------------------------- #


def test_provenance_rejects_wrong_event():
    run = _provenance_run()
    run["event"] = "push"
    assert evaluate_provenance(**_provenance_kwargs(run=run)) == ["PROVENANCE_EVENT_MISMATCH"]


def test_provenance_rejects_wrong_workflow_path():
    run = _provenance_run()
    run["path"] = ".github/workflows/other.yml"
    assert evaluate_provenance(**_provenance_kwargs(run=run)) == ["PROVENANCE_WORKFLOW_MISMATCH"]


def test_provenance_rejects_non_main_branch():
    run = _provenance_run()
    run["head_branch"] = "feature/other"
    assert evaluate_provenance(**_provenance_kwargs(run=run)) == ["PROVENANCE_BRANCH_MISMATCH"]


def test_provenance_rejects_incomplete_or_unsuccessful_run():
    run = _provenance_run()
    run["status"] = "in_progress"
    assert evaluate_provenance(**_provenance_kwargs(run=run)) == ["PROVENANCE_RUN_NOT_COMPLETED"]
    run = _provenance_run()
    run["conclusion"] = "failure"
    assert evaluate_provenance(**_provenance_kwargs(run=run)) == ["PROVENANCE_RUN_NOT_SUCCESSFUL"]


def test_provenance_rejects_head_sha_mismatch():
    run = _provenance_run()
    run["head_sha"] = "f" * 40
    assert evaluate_provenance(**_provenance_kwargs(run=run)) == ["PROVENANCE_HEAD_SHA_MISMATCH"]


def test_provenance_rejects_manifest_binding_mismatch():
    manifest = _provenance_manifest()
    manifest["commit"] = "f" * 40
    manifest["image_digest"] = "ghcr.io/example/qts@sha256:" + "f" * 64
    manifest["release_id"] = "other-release"
    manifest["environment"] = "production"
    errors = evaluate_provenance(**_provenance_kwargs(manifest=manifest))
    assert "MANIFEST_COMMIT_MISMATCH" in errors
    assert "MANIFEST_DIGEST_MISMATCH" in errors
    assert "MANIFEST_RELEASE_ID_MISMATCH" in errors
    assert "MANIFEST_ENVIRONMENT_NOT_DEMO" in errors


def test_provenance_rejects_invalid_expected_commit_or_digest():
    errors = evaluate_provenance(**_provenance_kwargs(commit="not-a-commit"))
    assert "EXPECTED_COMMIT_INVALID" in errors
    errors = evaluate_provenance(**_provenance_kwargs(digest="sha256:xyz"))
    assert "EXPECTED_DIGEST_INVALID" in errors


def test_provenance_rejects_run_id_or_repository_mismatch():
    errors = evaluate_provenance(**_provenance_kwargs(run_id="9999"))
    assert "PROVENANCE_RUN_ID_MISMATCH" in errors
    errors = evaluate_provenance(**_provenance_kwargs(repository="other/repo"))
    assert "PROVENANCE_REPOSITORY_MISMATCH" in errors


def test_provenance_accepts_valid_main_release_run():
    assert evaluate_provenance(**_provenance_kwargs()) == []


def test_protected_workflows_gate_provenance_before_trusted_checkout():
    for workflow_path in (DEPLOY_WORKFLOW, EVIDENCE_WORKFLOW):
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        for job_name, job in workflow["jobs"].items():
            steps = job.get("steps") or []
            trusted = [
                index
                for index, step in enumerate(steps)
                if str((step.get("with") or {}).get("ref", "")) == "${{ inputs.commit }}"
            ]
            if not trusted:
                continue
            provenance = [
                index
                for index, step in enumerate(steps)
                if "verify_release_provenance.py" in str(step.get("run", ""))
            ]
            assert len(provenance) == 1, job_name
            assert provenance[0] < min(trusted), job_name
            for index, step in enumerate(steps[: provenance[0]]):
                assert "python scripts/" not in str(step.get("run", "")), (job_name, index)


# --------------------------------------------------------------------------- #
# F-P6: the demo overlay must wire control/execution alerting to the external
# endpoints, and preflight must reject a missing external heartbeat.
# --------------------------------------------------------------------------- #


def test_merged_demo_compose_routes_alerting_to_external_endpoints(tmp_path):
    env_file = tmp_path / "demo.env"
    env_file.write_text(
        "\n".join(
            [
                "QTS_COMPOSE_PROJECT=qts_parentfix_render",
                f"QTS_RELEASE_IMAGE={IMAGE}",
                f"QTS_RELEASE_ID={RELEASE_ID}",
                f"QTS_RELEASE_COMMIT={COMMIT}",
                f"QTS_POSTGRES_IMAGE=postgres:16@{DIGEST}",
                f"QTS_ALERT_IMAGE=python:3.12-slim@{DIGEST}",
                f"QTS_EXTERNAL_ALERT_WEBHOOK_URL={EXTERNAL_WEBHOOK}",
                f"QTS_EXTERNAL_ALERT_HEARTBEAT_URL={EXTERNAL_HEARTBEAT}",
                "POSTGRES_PASSWORD=parentfix-strong-password",
                "QTS_PASSWORD_HASH=parentfix-hash",
                "QTS_ALERT_SIGNING_SECRET=parentfix-secret",
                "",
            ]
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "compose.yml",
            "-f",
            "deploy/compose.demo.yml",
            "--env-file",
            str(env_file),
            "-p",
            "qts_parentfix_render",
            "config",
            "--format",
            "json",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    for service in ("control", "execution"):
        service_env = config["services"][service]["environment"]
        assert service_env["QTS_ALERT_WEBHOOK_URL"] == EXTERNAL_WEBHOOK
        assert service_env["QTS_ALERT_HEARTBEAT_URL"] == EXTERNAL_HEARTBEAT
        assert "alert-sink" not in service_env["QTS_ALERT_WEBHOOK_URL"]
        assert "alert-sink" not in service_env["QTS_ALERT_HEARTBEAT_URL"]


def test_preflight_rejects_missing_external_heartbeat():
    from scripts.deploy_preflight import evaluate as evaluate_preflight

    env = _preflight_env()
    del env["QTS_EXTERNAL_ALERT_HEARTBEAT_URL"]
    report = evaluate_preflight(env, host_evidence=False)
    assert not report.ok
    assert any(
        issue.code == "EXTERNAL_ALERT_HEARTBEAT_MISSING"
        and issue.key == "QTS_EXTERNAL_ALERT_HEARTBEAT_URL"
        for issue in report.errors
    )


def test_preflight_rejects_in_cluster_or_insecure_heartbeat():
    from scripts.deploy_preflight import evaluate as evaluate_preflight

    for bad_url in ("http://alert-sink:8085/heartbeat", "http://alerts.example.com/hb"):
        env = _preflight_env()
        env["QTS_EXTERNAL_ALERT_HEARTBEAT_URL"] = bad_url
        report = evaluate_preflight(env, host_evidence=False)
        assert any(
            issue.code == "EXTERNAL_ALERT_HEARTBEAT_MISSING" for issue in report.errors
        ), bad_url


# --------------------------------------------------------------------------- #
# F-P7: strict mode must treat missing/invalid safety metrics as blockers;
# compatibility mode keeps the historical behaviour.
# --------------------------------------------------------------------------- #


def test_strict_blocks_missing_outbox_backlog_metric(monkeypatch, tmp_path):
    def drop_backlog(sample):
        if "metrics" in sample:
            del sample["metrics"]["outbox_backlog"]

    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1, mutate=drop_backlog)
    binding = _write_binding(tmp_path, _valid_binding())
    exit_code, summary = _run_summarizer(
        monkeypatch, tmp_path, _strict_argv(tmp_path, observation, binding)
    )
    assert exit_code == 1
    assert "STRICT_METRICS_MISSING:outbox_backlog" in summary["blockers"]


def test_strict_blocks_all_missing_metrics_when_samples_have_no_metrics(monkeypatch, tmp_path):
    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1, strict_metrics=False)
    binding = _write_binding(tmp_path, _valid_binding())
    exit_code, summary = _run_summarizer(
        monkeypatch, tmp_path, _strict_argv(tmp_path, observation, binding)
    )
    assert exit_code == 1
    missing = [code for code in summary["blockers"] if code.startswith("STRICT_METRICS_MISSING:")]
    assert missing, summary["blockers"]
    for key in (
        "outbox_backlog",
        "unbooked_fills",
        "unknown_commands",
        "balance_anomalies",
        "reconciliation_age_ms",
    ):
        assert key in missing[0]
    assert summary["strict_metrics_checked"]


def test_strict_blocks_invalid_metric_types(monkeypatch, tmp_path):
    def invalidate(sample):
        metrics = sample.get("metrics")
        if isinstance(metrics, dict):
            metrics["outbox_backlog"] = "0"
            metrics["balance_anomalies"] = True

    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1, mutate=invalidate)
    binding = _write_binding(tmp_path, _valid_binding())
    exit_code, summary = _run_summarizer(
        monkeypatch, tmp_path, _strict_argv(tmp_path, observation, binding)
    )
    assert exit_code == 1
    invalid = [code for code in summary["blockers"] if code.startswith("STRICT_METRICS_INVALID:")]
    assert invalid, summary["blockers"]
    assert "outbox_backlog" in invalid[0]
    assert "balance_anomalies" in invalid[0]


def test_compat_mode_keeps_historical_behavior_without_metrics(monkeypatch, tmp_path):
    observation = _write_observation(tmp_path / "obs.jsonl", hours=24.1, strict_metrics=False)
    exit_code, summary = _run_summarizer(
        monkeypatch,
        tmp_path,
        ["--input", str(observation), "--min-hours", "24"],
    )
    assert exit_code == 0, summary["blockers"]
    assert summary["ok"] is True
    assert summary["strict_metrics_checked"] == []
    assert summary["strict_release"] is False


# --------------------------------------------------------------------------- #
# F-P8: the release manifest must fail closed on missing/failed evidence and
# preflight must re-check it.
# --------------------------------------------------------------------------- #


def test_manifest_requires_every_evidence_artifact(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        build_manifest(_manifest_args(tmp_path, coverage=""))
    assert str(excinfo.value).startswith("RELEASE_EVIDENCE_MISSING:")
    assert "coverage" in str(excinfo.value)

    with pytest.raises(SystemExit) as excinfo:
        build_manifest(_manifest_args(tmp_path, sbom=str(tmp_path / "missing.json")))
    assert "sbom" in str(excinfo.value)

    with pytest.raises(SystemExit) as excinfo:
        build_manifest(_manifest_args(tmp_path, junit=[]))
    assert "junit" in str(excinfo.value)


def test_manifest_requires_dependencies_constraints(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        build_manifest(_manifest_args(tmp_path, ci_constraints=str(tmp_path / "nope.txt")))
    assert "ci_constraints" in str(excinfo.value)


def test_manifest_rejects_failed_startup_smoke(tmp_path):
    smoke = _smoke_file(tmp_path, passed=False)
    with pytest.raises(SystemExit) as excinfo:
        build_manifest(_manifest_args(tmp_path, startup_smoke=str(smoke)))
    assert "RELEASE_STARTUP_SMOKE_NOT_PASSED" in str(excinfo.value)


def test_manifest_rejects_unverified_or_mismatched_smoke_digest(tmp_path):
    smoke = _smoke_file(tmp_path, image_digest_ok=False)
    with pytest.raises(SystemExit) as excinfo:
        build_manifest(_manifest_args(tmp_path, startup_smoke=str(smoke)))
    assert "RELEASE_STARTUP_SMOKE_DIGEST_NOT_VERIFIED" in str(excinfo.value)

    smoke = _smoke_file(tmp_path, image="ghcr.io/example/qts@sha256:" + "f" * 64)
    with pytest.raises(SystemExit) as excinfo:
        build_manifest(_manifest_args(tmp_path, startup_smoke=str(smoke)))
    assert "RELEASE_STARTUP_SMOKE_DIGEST_MISMATCH" in str(excinfo.value)


def test_manifest_positive_binds_startup_smoke_summary(tmp_path):
    manifest = build_manifest(_manifest_args(tmp_path))
    assert manifest["image_digest"] == DIGEST
    assert manifest["commit"] == COMMIT
    assert manifest["startup_smoke_summary"]["passed"] is True
    assert manifest["startup_smoke_summary"]["image_digest_ok"] is True
    for key, artifact in manifest["evidence"].items():
        if key == "junit":
            assert all(item["present"] for item in artifact), key
        else:
            assert artifact is not None and artifact["present"], key


def test_preflight_reports_manifest_evidence_missing():
    from scripts.deploy_preflight import evaluate as evaluate_preflight

    manifest = _preflight_manifest()
    del manifest["evidence"]["coverage"]
    report = evaluate_preflight(_preflight_env(), release_manifest=manifest, host_evidence=False)
    assert not report.ok
    codes = [issue.code for issue in report.errors]
    assert "RELEASE_MANIFEST_EVIDENCE_MISSING:coverage" in codes


def test_preflight_reports_manifest_startup_smoke_failed():
    from scripts.deploy_preflight import evaluate as evaluate_preflight

    manifest = _preflight_manifest(smoke_passed=False)
    report = evaluate_preflight(_preflight_env(), release_manifest=manifest, host_evidence=False)
    codes = [issue.code for issue in report.errors]
    assert "RELEASE_MANIFEST_STARTUP_SMOKE_FAILED" in codes


def test_preflight_accepts_complete_manifest_with_host_evidence_deferred():
    from scripts.deploy_preflight import evaluate as evaluate_preflight

    report = evaluate_preflight(
        _preflight_env(), release_manifest=_preflight_manifest(), host_evidence=False
    )
    assert report.ok, [issue.code for issue in report.errors]
    assert report.host_evidence_evaluated is False
    assert any(issue.code == "HOST_EVIDENCE_DEFERRED" for issue in report.warnings)


# --------------------------------------------------------------------------- #
# Regression: the manifest must uniquely classify every current test file.
# --------------------------------------------------------------------------- #


def test_manifest_uniquely_classifies_every_test_file():
    manifest = json.loads((ROOT / "ci" / "test_manifest.json").read_text(encoding="utf-8"))
    classified: dict[str, str] = {}
    for group, names in manifest["groups"].items():
        for name in names:
            assert name not in classified, f"{name} double-classified: {group}"
            classified[name] = group
    on_disk = {path.name for path in (ROOT / "trader" / "tests").glob("test_*.py")}
    assert on_disk == set(classified), (
        f"unclassified: {sorted(on_disk - set(classified))}; "
        f"stale: {sorted(set(classified) - on_disk)}"
    )
    assert len(on_disk) >= 256
