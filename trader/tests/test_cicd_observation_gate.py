"""Release-gate regression tests for ``scripts/summarize_observation.py``.

The historical summary recorded ``ready_ratio`` but never failed on it, and
never gated reconciliation age, outbox backlog, final unbooked fills or
duplicate/resend/overwrite evidence.  These tests pin the explicit thresholds
and the strict release-evidence binding used by the Demo release gate.

The strict gate does not trust declared flags: the binding must carry the
inspected container facts, a real timestamp, no collector error and the
runtime identity, and every sample must have been served by that same
runtime.  A hand-written binding that only sets five booleans is rejected.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest
from scripts import observe_trading_runtime
from scripts.summarize_observation import main

RELEASE_ID = "rel-2026-11-04-1"
COMMIT = "c6fe450260eeb59d46c9caee591fb1625510b443"
DIGEST = "sha256:" + "a" * 64
IMAGE = "ghcr.io/example/qts@" + DIGEST
# 2026-11-04T00:00:00Z
BASE_MS = 1_793_750_400_000


def _runtime(ts_ms: int) -> dict:
    return {
        "release_id": RELEASE_ID,
        "commit": COMMIT,
        "image_digest": DIGEST,
        "environment": "demo",
        "instance_owner": "exec-1",
        "lease_token": "7",
        "observed_at_ms": ts_ms,
    }


def _samples(count: int = 61, **metric_overrides):
    records = []
    for minute in range(count):
        ts_ms = BASE_MS + minute * 60_000
        records.append(
            {
                "ts_ms": ts_ms,
                "health": {"status_code": 200, "body": {"ready": True}},
                "metrics": {
                    "reconciliation_age_ms": 5_000,
                    "outbox_backlog": 0,
                    "unbooked_fills": 0,
                    "unknown_commands": 0,
                    "balance_anomalies": 0,
                    "unresolved": [],
                    **metric_overrides,
                },
                "runtime": _runtime(ts_ms),
            }
        )
    return records


def _markers():
    return [
        {"ts_ms": BASE_MS + 120_000, "marker": "runtime_restart_start"},
        {"ts_ms": BASE_MS + 150_000, "marker": "runtime_restart_end"},
        {"ts_ms": BASE_MS + 180_000, "marker": "active_reconnect"},
    ]


def _run(tmp_path, monkeypatch, records, extra=()):
    source = tmp_path / "observation.jsonl"
    output = tmp_path / "summary.json"
    source.write_text(
        "\n".join(item if isinstance(item, str) else json.dumps(item) for item in records),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "summary",
            "--input",
            str(source),
            "--min-hours",
            "1",
            "--json-out",
            str(output),
            *extra,
        ],
    )
    code = main()
    return code, json.loads(output.read_text(encoding="utf-8"))


def _container(service: str) -> dict:
    return {
        "name": f"{service}-1",
        "present": True,
        "running": True,
        "health": "healthy",
        "image": IMAGE,
        "image_id": "sha256:image",
        "labels": {
            "com.qts.release.id": RELEASE_ID,
            "com.qts.release.commit": COMMIT,
            "com.qts.environment": "demo",
        },
    }


def _release_payload(generated_at: str = "2026-11-05T00:01:00Z", **overrides):
    release = {
        "schema_version": 1,
        "release_id": RELEASE_ID,
        "commit": COMMIT,
        "image_digest": DIGEST,
        "environment": "demo",
        "generated_at": generated_at,
        "runtime_inspected": True,
        "errors": [],
        "checks": {
            "all_running": True,
            "all_healthy": True,
            "labels_match": True,
            "image_digest_match": True,
            "trading_ready": True,
        },
        "containers": {"control": _container("control"), "execution": _container("execution")},
        "health": {
            "live": {"status": 200},
            "ready": {"status": 200},
            "trading": {"status": 200, "body": {"ready": True}},
        },
        "runtime": {
            "release_id": RELEASE_ID,
            "commit": COMMIT,
            "image_digest": DIGEST,
            "environment": "demo",
            "instance_owner": "exec-1",
            "lease_token": "7",
        },
    }
    release.update(overrides)
    return release


def _write_release(tmp_path, **overrides):
    path = tmp_path / "release.json"
    path.write_text(json.dumps(_release_payload(**overrides)), encoding="utf-8")
    return path


def test_ready_ratio_below_threshold_blocks(tmp_path, monkeypatch):
    records = _samples(61)
    for sample in records[30:]:
        sample["health"]["body"]["ready"] = False
    code, report = _run(tmp_path, monkeypatch, records + _markers())
    assert code == 1
    assert report["ready_ratio"] < 0.6
    assert "READY_RATIO_BELOW_THRESHOLD" in report["blockers"]


def test_ready_ratio_at_threshold_passes(tmp_path, monkeypatch):
    records = _samples(100)
    for sample in records[59:]:
        sample["health"]["body"]["ready"] = False
    # 60 ready samples out of 100; the final sample must also be ready.
    records[-1]["health"]["body"]["ready"] = True
    code, report = _run(tmp_path, monkeypatch, records + _markers())
    assert report["ready_ratio"] == 0.6
    assert "READY_RATIO_BELOW_THRESHOLD" not in report["blockers"]


def test_missing_reconciliation_age_at_the_end_blocks(tmp_path, monkeypatch):
    records = _samples(61)
    records[-1]["metrics"]["reconciliation_age_ms"] = None
    code, report = _run(tmp_path, monkeypatch, records + _markers())
    assert code == 1
    assert "RECONCILIATION_AGE_UNKNOWN" in report["blockers"]


def test_reconciliation_age_above_threshold_blocks(tmp_path, monkeypatch):
    records = _samples(61)
    records[10]["metrics"]["reconciliation_age_ms"] = 600_001
    code, report = _run(tmp_path, monkeypatch, records + _markers())
    assert code == 1
    assert "RECONCILIATION_AGE_EXCEEDED" in report["blockers"]


def test_final_outbox_backlog_blocks_but_transient_backlog_is_recorded(tmp_path, monkeypatch):
    records = _samples(61)
    records[5]["metrics"]["outbox_backlog"] = 3
    code, report = _run(tmp_path, monkeypatch, records + _markers())
    assert code == 0
    assert report["max_outbox_backlog"] == 3
    assert report["final_outbox_backlog"] == 0

    records[-1]["metrics"]["outbox_backlog"] = 1
    code, report = _run(tmp_path, monkeypatch, records + _markers())
    assert code == 1
    assert "OUTBOX_BACKLOG_AT_END" in report["blockers"]


def test_final_unbooked_fill_blocks_but_resolved_fill_does_not(tmp_path, monkeypatch):
    records = _samples(61)
    records[7]["metrics"]["unbooked_fills"] = 1
    code, report = _run(tmp_path, monkeypatch, records + _markers())
    assert code == 0
    assert report["max_unbooked_fills"] == 1
    assert report["final_unbooked_fills"] == 0

    records[-1]["metrics"]["unbooked_fills"] = 2
    code, report = _run(tmp_path, monkeypatch, records + _markers())
    assert code == 1
    assert "UNBOOKED_FILLS_AT_END" in report["blockers"]


def test_duplicate_resend_and_overwrite_evidence_blocks(tmp_path, monkeypatch):
    records = _samples(61)
    records[12]["metrics"]["duplicate_fills"] = 1
    records[13]["metrics"]["resend_attempts"] = 2
    records[14]["metrics"]["overwrite_detected"] = True
    code, report = _run(tmp_path, monkeypatch, records + _markers())
    assert code == 1
    assert "DUPLICATE_OR_RESEND_EVIDENCE" in report["blockers"]
    assert report["duplicate_or_resend_samples"] >= 3


def test_numeric_string_counters_are_normalized(tmp_path, monkeypatch):
    records = _samples(61)
    records[5]["metrics"]["duplicate_fills"] = "0"
    code, report = _run(tmp_path, monkeypatch, records + _markers())
    assert code == 0
    assert report["duplicate_or_resend_samples"] == 0

    records[5]["metrics"]["duplicate_fills"] = "1"
    code, report = _run(tmp_path, monkeypatch, records + _markers())
    assert code == 1
    assert "DUPLICATE_OR_RESEND_EVIDENCE" in report["blockers"]


def test_strict_release_mode_requires_bound_evidence(tmp_path, monkeypatch):
    code, report = _run(
        tmp_path,
        monkeypatch,
        _samples(1441) + _markers(),
        ("--strict-release",),
    )
    assert code == 1
    assert "RELEASE_EVIDENCE_MISSING" in report["blockers"]


@pytest.mark.parametrize(
    "override, blocker",
    [
        ({"commit": "0" * 40}, "RELEASE_COMMIT_MISMATCH"),
        ({"image_digest": "sha256:" + "b" * 64}, "RELEASE_DIGEST_MISMATCH"),
        ({"environment": "testnet"}, "RELEASE_ENVIRONMENT_NOT_DEMO"),
    ],
)
def test_strict_release_rejects_wrong_binding(tmp_path, monkeypatch, override, blocker):
    release = _write_release(tmp_path, **override)
    code, report = _run(
        tmp_path,
        monkeypatch,
        _samples(1441) + _markers(),
        (
            "--strict-release",
            "--release-evidence",
            str(release),
            "--expected-commit",
            COMMIT,
            "--expected-digest",
            DIGEST,
        ),
    )
    assert code == 1
    assert blocker in report["blockers"]


def test_strict_release_accepts_matching_binding(tmp_path, monkeypatch):
    release = _write_release(tmp_path)
    code, report = _run(
        tmp_path,
        monkeypatch,
        _samples(1441) + _markers(),
        (
            "--strict-release",
            "--release-evidence",
            str(release),
            "--expected-commit",
            COMMIT,
            "--expected-digest",
            DIGEST,
        ),
    )
    assert code == 0 and report["ok"], report["blockers"]
    assert report["release_binding"]["environment"] == "demo"


def test_invalid_threshold_is_rejected(tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _run(
            tmp_path,
            monkeypatch,
            _samples(61) + _markers(),
            ("--min-ready-ratio", "1.5"),
        )
    assert exc.value.code == 2


def test_strict_release_requires_a_full_24h_window(tmp_path, monkeypatch):
    release = _write_release(tmp_path)
    code, report = _run(
        tmp_path,
        monkeypatch,
        _samples(61) + _markers(),
        (
            "--min-hours",
            "1",
            "--strict-release",
            "--release-evidence",
            str(release),
            "--expected-commit",
            COMMIT,
            "--expected-digest",
            DIGEST,
        ),
    )
    assert code == 1
    assert "STRICT_WINDOW_SHORTER_THAN_24H" in report["blockers"]


def test_strict_min_hours_floor_cannot_be_lowered(tmp_path, monkeypatch):
    release = _write_release(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run(
            tmp_path,
            monkeypatch,
            _samples(61) + _markers(),
            (
                "--strict-release",
                "--strict-min-hours",
                "1",
                "--release-evidence",
                str(release),
            ),
        )
    assert exc.value.code == 2


def test_strict_release_requires_explicit_safety_counters(tmp_path, monkeypatch):
    release = _write_release(tmp_path)
    records = _samples(1441)
    for sample in records:
        for key in ("outbox_backlog", "unbooked_fills", "unknown_commands", "balance_anomalies"):
            sample["metrics"].pop(key)
    code, report = _run(
        tmp_path,
        monkeypatch,
        records + _markers(),
        (
            "--strict-release",
            "--release-evidence",
            str(release),
            "--expected-commit",
            COMMIT,
            "--expected-digest",
            DIGEST,
        ),
    )
    assert code == 1
    assert any(blocker.startswith("STRICT_METRICS_MISSING:") for blocker in report["blockers"])
    assert "outbox_backlog" in report["strict_metrics_missing"]


def test_strict_release_rejects_invalid_counter_types(tmp_path, monkeypatch):
    release = _write_release(tmp_path)
    records = _samples(1441)
    records[5]["metrics"]["unbooked_fills"] = "not-an-int"
    code, report = _run(
        tmp_path,
        monkeypatch,
        records + _markers(),
        (
            "--strict-release",
            "--release-evidence",
            str(release),
            "--expected-commit",
            COMMIT,
            "--expected-digest",
            DIGEST,
        ),
    )
    assert code == 1
    assert "STRICT_METRICS_INVALID:unbooked_fills" in report["blockers"]


def test_strict_release_rejects_a_self_declared_binding(tmp_path, monkeypatch):
    release = _write_release(tmp_path)
    payload = json.loads(release.read_text(encoding="utf-8"))
    payload.pop("runtime_inspected")
    payload.pop("checks")
    payload.pop("containers")
    release.write_text(json.dumps(payload), encoding="utf-8")
    code, report = _run(
        tmp_path,
        monkeypatch,
        _samples(1441) + _markers(),
        (
            "--strict-release",
            "--release-evidence",
            str(release),
            "--expected-commit",
            COMMIT,
            "--expected-digest",
            DIGEST,
        ),
    )
    assert code == 1
    assert "RELEASE_RUNTIME_BINDING_MISSING" in report["blockers"]
    assert "RELEASE_RUNTIME_CHECKS_MISSING" in report["blockers"]


def _strict_run(tmp_path, monkeypatch, records, release):
    return _run(
        tmp_path,
        monkeypatch,
        records,
        (
            "--strict-release",
            "--release-evidence",
            str(release),
            "--expected-commit",
            COMMIT,
            "--expected-digest",
            DIGEST,
        ),
    )


def test_strict_release_rejects_contradictory_container_facts(tmp_path, monkeypatch):
    payload = _release_payload()
    payload["containers"]["control"]["running"] = False
    payload["containers"]["execution"]["image"] = "wrong:latest"
    payload["errors"] = ["IMAGE_DIGEST_MISMATCH:execution"]
    payload["generated_at"] = "invalid-timestamp"
    release = tmp_path / "release.json"
    release.write_text(json.dumps(payload), encoding="utf-8")
    code, report = _strict_run(tmp_path, monkeypatch, _samples(1441) + _markers(), release)
    assert code == 1
    blockers = report["blockers"]
    assert "RELEASE_RUNTIME_ERROR_PRESENT:IMAGE_DIGEST_MISMATCH:execution" in blockers
    assert "RELEASE_RUNTIME_GENERATED_AT_INVALID" in blockers
    assert "RELEASE_RUNTIME_CONTAINER_NOT_RUNNING:control" in blockers
    assert "RELEASE_RUNTIME_CONTAINER_IMAGE_MISMATCH:execution" in blockers


def test_strict_release_rejects_samples_without_runtime_identity(tmp_path, monkeypatch):
    release = _write_release(tmp_path)
    records = _samples(1441)
    for sample in records:
        sample.pop("runtime")
    code, report = _strict_run(tmp_path, monkeypatch, records + _markers(), release)
    assert code == 1
    assert "SAMPLE_RUNTIME_IDENTITY_MISSING" in report["blockers"]


def test_strict_release_rejects_samples_from_another_release(tmp_path, monkeypatch):
    release = _write_release(tmp_path)
    records = _samples(1441)
    records[10]["runtime"] = dict(records[10]["runtime"], image_digest="sha256:" + "c" * 64)
    records[11]["runtime"] = dict(records[11]["runtime"], release_id="other-release")
    code, report = _strict_run(tmp_path, monkeypatch, records + _markers(), release)
    assert code == 1
    assert "SAMPLE_RUNTIME_IDENTITY_MISMATCH:image_digest" in report["blockers"]
    assert "SAMPLE_RUNTIME_IDENTITY_MISMATCH:release_id" in report["blockers"]


def test_strict_release_rejects_a_binding_from_another_window(tmp_path, monkeypatch):
    # The binding was collected long before the observed window.
    release = _write_release(tmp_path, generated_at="2026-11-01T00:00:00Z")
    code, report = _strict_run(tmp_path, monkeypatch, _samples(1441) + _markers(), release)
    assert code == 1
    assert "RELEASE_BINDING_WINDOW_MISMATCH" in report["blockers"]


def test_strict_release_rejects_an_unbound_historical_window(tmp_path, monkeypatch):
    # 1970 samples with a self-declared binding: no runtime identity, no
    # inspected facts, an invalid timestamp.
    payload = {
        "release_id": "parent-synthetic-only",
        "commit": COMMIT,
        "image_digest": DIGEST,
        "environment": "demo",
        "runtime_inspected": True,
        "generated_at": "invalid-timestamp",
        "containers": {
            "control": {"running": False, "image": "wrong:latest"},
            "execution": {"running": False, "image": "wrong:latest"},
        },
        "errors": ["IMAGE_DIGEST_MISMATCH:execution"],
        "checks": {
            name: True
            for name in (
                "all_running",
                "all_healthy",
                "labels_match",
                "image_digest_match",
                "trading_ready",
            )
        },
    }
    release = tmp_path / "release.json"
    release.write_text(json.dumps(payload), encoding="utf-8")
    records = [
        {
            "ts_ms": minute * 60_000,
            "health": {"status_code": 200, "body": {"ready": True}},
            "metrics": {
                "reconciliation_age_ms": 1_000,
                "outbox_backlog": 0,
                "unbooked_fills": 0,
                "unknown_commands": 0,
                "balance_anomalies": 0,
                "unresolved": [],
            },
        }
        for minute in range(1441)
    ]
    code, report = _strict_run(tmp_path, monkeypatch, records + _markers(), release)
    assert code == 1
    assert "RELEASE_RUNTIME_ERROR_PRESENT:IMAGE_DIGEST_MISMATCH:execution" in report["blockers"]
    assert "RELEASE_RUNTIME_GENERATED_AT_INVALID" in report["blockers"]
    assert "SAMPLE_RUNTIME_IDENTITY_MISSING" in report["blockers"]


class _Clock:
    def __init__(self) -> None:
        self.current = 1_800_000_000.0

    def time(self) -> float:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.current += seconds


class _Response:
    status_code = 200

    def __init__(self, body: dict) -> None:
        self.body = body

    def json(self) -> dict:
        return self.body

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return _Response({"csrf_token": "synthetic"})

    async def get(self, path, **kwargs):
        if path == "/health/trading":
            return _Response(
                {
                    "ready": True,
                    "release": {
                        "release_id": RELEASE_ID,
                        "commit": COMMIT,
                        "image_digest": DIGEST,
                        "environment": "demo",
                    },
                    "instance_owner": "exec-1",
                    "lease_token": "7",
                }
            )
        if path == "/v1/trading/metrics":
            return _Response(
                {
                    "reconciliation_age_ms": 1_000,
                    "outbox_backlog": 0,
                    "unbooked_fills": 0,
                    "unknown_commands": 0,
                    "balance_anomalies": 0,
                    "unresolved": [],
                }
            )
        return _Response(
            {
                "status": "NORMAL",
                "reasons": [],
                "generation": 1,
                "account_version": 1,
                "positions": [],
            }
        )


def test_observer_records_the_full_requested_duration(tmp_path):
    """A 24h request must produce a 24h window (F-P1)."""
    clock = _Clock()
    output = tmp_path / "observation.jsonl"
    argv = [
        "observer",
        "--password",
        "synthetic-no-secret",
        "--duration",
        "86400",
        "--interval",
        "60",
        "--out",
        str(output),
    ]
    with (
        patch.object(sys, "argv", argv),
        patch.object(observe_trading_runtime.time, "time", clock.time),
        patch.object(observe_trading_runtime.asyncio, "sleep", clock.sleep),
        patch.object(observe_trading_runtime.httpx, "AsyncClient", _Client),
        redirect_stdout(io.StringIO()),
    ):
        exit_code = asyncio.run(observe_trading_runtime.main())
    assert exit_code == 0
    samples = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    span_hours = (samples[-1]["ts_ms"] - samples[0]["ts_ms"]) / 3_600_000
    assert span_hours >= 24
    assert samples[-1]["runtime"]["release_id"] == RELEASE_ID
    assert samples[0]["runtime"]["observed_at_ms"] == samples[0]["ts_ms"]


def test_strict_release_requires_the_collector_error_list(tmp_path, monkeypatch):
    payload = _release_payload()
    payload.pop("errors")
    release = tmp_path / "release.json"
    release.write_text(json.dumps(payload), encoding="utf-8")
    code, report = _strict_run(tmp_path, monkeypatch, _samples(1441) + _markers(), release)
    assert code == 1
    assert "RELEASE_RUNTIME_ERRORS_MISSING" in report["blockers"]


def test_strict_release_anchors_the_binding_label_with_expected_release_id(tmp_path, monkeypatch):
    release = _write_release(tmp_path)
    code, report = _run(
        tmp_path,
        monkeypatch,
        _samples(1441) + _markers(),
        (
            "--strict-release",
            "--release-evidence",
            str(release),
            "--expected-release-id",
            "another-release",
            "--expected-commit",
            COMMIT,
            "--expected-digest",
            DIGEST,
        ),
    )
    assert code == 1
    assert "RELEASE_RUNTIME_LABEL_MISMATCH:control:release_id" in report["blockers"]
    assert "RELEASE_RUNTIME_IDENTITY_MISMATCH:release_id" in report["blockers"]


def _run_observer(tmp_path, clock, client_class, duration: float, interval: float):
    output = tmp_path / "observation.jsonl"
    argv = [
        "observer",
        "--password",
        "synthetic-no-secret",
        "--duration",
        str(duration),
        "--interval",
        str(interval),
        "--out",
        str(output),
    ]
    with (
        patch.object(sys, "argv", argv),
        patch.object(observe_trading_runtime.time, "time", clock.time),
        patch.object(observe_trading_runtime.asyncio, "sleep", clock.sleep),
        patch.object(observe_trading_runtime.httpx, "AsyncClient", client_class),
        redirect_stdout(io.StringIO()),
    ):
        exit_code = asyncio.run(observe_trading_runtime.main())
    samples = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    return exit_code, samples


def test_observer_window_starts_after_client_initialization(tmp_path):
    """Client setup time must not shorten the recorded window (parent F-P1)."""

    class SlowInitClient(_Client):
        async def __aenter__(self):
            await clock.sleep(2.0)
            return self

    clock = _Clock()
    exit_code, samples = _run_observer(tmp_path, clock, SlowInitClient, 86_400, 60)
    assert exit_code == 0
    span_hours = (samples[-1]["ts_ms"] - samples[0]["ts_ms"]) / 3_600_000
    assert span_hours >= 24


def test_observer_window_covers_request_latency(tmp_path):
    class SlowRequestClient(_Client):
        async def post(self, *args, **kwargs):
            await clock.sleep(1.0)
            return await super().post(*args, **kwargs)

        async def get(self, path, **kwargs):
            await clock.sleep(1.0)
            return await super().get(path, **kwargs)

    clock = _Clock()
    exit_code, samples = _run_observer(tmp_path, clock, SlowRequestClient, 120, 60)
    assert exit_code == 0
    span_seconds = (samples[-1]["ts_ms"] - samples[0]["ts_ms"]) / 1000
    assert span_seconds >= 120


def test_observer_handles_an_interval_that_does_not_divide_the_duration(tmp_path):
    clock = _Clock()
    exit_code, samples = _run_observer(tmp_path, clock, _Client, 100, 30)
    assert exit_code == 0
    span_seconds = (samples[-1]["ts_ms"] - samples[0]["ts_ms"]) / 1000
    assert span_seconds == 100
