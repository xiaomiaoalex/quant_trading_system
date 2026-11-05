"""Independent round-2 counterexamples for the parent-review fixes.

Authored by the fresh independent tester for parent round 2 (F-P1, F-P2, F-P3
offline logic, F-P5, F-P8), before reading the implementation (design first:
``artifacts/cicd/parentfix2-tester-r2/test-design.md``).  Pre-classified in
``ci/test_manifest.json`` (offline group).

Every test drives the real entry points (``observe_trading_runtime.main``,
``summarize_observation.main``, ``deploy_gate.evaluate``/``run_gate``) with
fake clocks and fake HTTP only; no network, no Docker, no secrets.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import io
import json
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import (  # noqa: E402
    deploy_gate,
    observe_trading_runtime,
    release_binding,
    summarize_observation,
)
from scripts.collect_runtime_release_binding import (  # noqa: E402
    APP_SERVICES,
    EXPECTED_SERVICES,
    LABEL_ENVIRONMENT,
    LABEL_RELEASE_COMMIT,
    LABEL_RELEASE_ID,
    build_binding,
    runtime_identity_from_health,
)

WORKFLOWS = ROOT / ".github" / "workflows"
DEPLOY_WORKFLOW = "deploy-demo.yml"
EVIDENCE_WORKFLOW = "demo-evidence.yml"
RELEASE_BUILD_WORKFLOW = "release-build.yml"

COMMIT = "c6fe450260eeb59d46c9caee591fb1625510b443"
DIGEST = "sha256:" + "a" * 64
IMAGE = "ghcr.io/example/qts@" + DIGEST
RELEASE_ID = "parentfix2-tester-r2"
T0 = 1_800_000_000.0
DAY_MS = 86_400_000

METRICS = {
    "reconciliation_age_ms": 1000,
    "outbox_backlog": 0,
    "unbooked_fills": 0,
    "unknown_commands": 0,
    "balance_anomalies": 0,
    "unresolved": [],
}


# --------------------------------------------------------------------------
# Fakes: clock and HTTP (no network contact at any point)
# --------------------------------------------------------------------------
class FakeClock:
    def __init__(self, start: float = T0) -> None:
        self.current = start

    def time(self) -> float:
        return self.current

    def monotonic(self) -> float:
        return self.current

    async def asleep(self, seconds: float) -> None:
        self.current += seconds


class FakeResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self.body = body

    def json(self) -> object:
        return self.body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP_{self.status_code}")


def _identity_release_body() -> dict:
    return {
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


class FakeTradingClient:
    """httpx.AsyncClient stand-in answering the observer endpoints."""

    def __init__(self, **kwargs) -> None:
        self.base_url = kwargs.get("base_url")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return FakeResponse(200, {"csrf_token": "synthetic"})

    async def get(self, path, **kwargs):
        if path == "/health/trading":
            return FakeResponse(200, _identity_release_body())
        if path == "/v1/trading/metrics":
            return FakeResponse(200, METRICS)
        if path.startswith("/v1/trading/accounts/"):
            return FakeResponse(
                200,
                {
                    "status": "NORMAL",
                    "reasons": [],
                    "generation": 1,
                    "account_version": 1,
                    "positions": [],
                },
            )
        return FakeResponse(200, {})


def _latency_client(clock: FakeClock, *, init_seconds: float = 0.0, request_seconds: float = 0.0):
    """Client whose setup and every request consume fake-clock time."""

    class LatencyClient(FakeTradingClient):
        async def __aenter__(self):
            clock.current += init_seconds
            return self

        async def post(self, *args, **kwargs):
            clock.current += request_seconds
            return await super().post(*args, **kwargs)

        async def get(self, path, **kwargs):
            clock.current += request_seconds
            return await super().get(path, **kwargs)

    return LatencyClient


def _run_observer(
    tmp_path: Path, clock: FakeClock, client_factory, *, duration: str, interval: str
) -> tuple[int, list[dict]]:
    out = tmp_path / "observation.jsonl"
    argv = [
        "observer",
        "--password",
        "synthetic-no-secret",
        "--duration",
        duration,
        "--interval",
        interval,
        "--out",
        str(out),
    ]
    with (
        patch.object(sys, "argv", argv),
        patch.object(observe_trading_runtime.time, "time", clock.time),
        patch.object(observe_trading_runtime.asyncio, "sleep", clock.asleep),
        patch.object(observe_trading_runtime.httpx, "AsyncClient", client_factory),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        exit_code = asyncio.run(observe_trading_runtime.main())
    samples = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    return exit_code, samples


# --------------------------------------------------------------------------
# F-P2 / baseline builders (hand-computed valid binding and window)
# --------------------------------------------------------------------------
def _valid_containers() -> dict:
    containers: dict = {}
    for service in EXPECTED_SERVICES:
        item = {
            "name": f"qts-demo-{service}-1",
            "present": True,
            "running": True,
            "health": "healthy",
            "image": "postgres:16",
            "image_id": "",
            "labels": {},
        }
        if service in APP_SERVICES:
            item["image"] = IMAGE
            item["image_id"] = "sha256:" + "c" * 64
            item["labels"] = {
                LABEL_RELEASE_ID: RELEASE_ID,
                LABEL_RELEASE_COMMIT: COMMIT,
                LABEL_ENVIRONMENT: "demo",
            }
        containers[service] = item
    return containers


def _valid_health() -> dict:
    return {
        "live": {"status": 200, "body": {}},
        "ready": {"status": 200, "body": {}},
        "trading": {"status": 200, "body": _identity_release_body()},
    }


def _iso(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def valid_binding() -> dict:
    """Build the binding exactly the way the real collector builds it."""
    health = _valid_health()
    binding = build_binding(
        project="qts-demo",
        containers=_valid_containers(),
        health=health,
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
        runtime=runtime_identity_from_health(health),
    )
    # Anchor the collector timestamp inside the synthetic window by hand; the
    # real collector stamps "now", which would not overlap a synthetic window.
    binding["generated_at"] = _iso(T0 + 43_200)
    return binding


def _sample_runtime(binding_runtime: dict, ts_ms: int) -> dict:
    runtime = {field: binding_runtime[field] for field in release_binding.IDENTITY_FIELDS}
    runtime["instance_owner"] = binding_runtime["instance_owner"]
    runtime["lease_token"] = binding_runtime.get("lease_token", "")
    runtime["observed_at_ms"] = ts_ms
    return runtime


def make_sample(binding_runtime: dict, ts_ms: int, *, ready: bool = True) -> dict:
    return {
        "ts_ms": ts_ms,
        "health": {"status_code": 200 if ready else 503, "body": {"ready": ready}},
        "metrics": dict(METRICS),
        "runtime": _sample_runtime(binding_runtime, ts_ms),
    }


def valid_window_samples(binding_runtime: dict, count: int = 1441, step_s: int = 60) -> list[dict]:
    """count samples at `step_s` spacing starting at T0."""
    return [
        make_sample(binding_runtime, int(T0 * 1000) + index * step_s * 1000)
        for index in range(count)
    ]


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def run_summarize(input_path: Path, binding_path: Path, *extra: str) -> tuple[int, dict]:
    captured = io.StringIO()
    argv = [
        "summary",
        "--input",
        str(input_path),
        "--strict-release",
        "--release-evidence",
        str(binding_path),
        "--expected-commit",
        COMMIT,
        "--expected-digest",
        DIGEST,
        *extra,
    ]
    with patch.object(sys, "argv", argv), contextlib.redirect_stdout(captured):
        exit_code = summarize_observation.main()
    return exit_code, json.loads(captured.getvalue())


# --------------------------------------------------------------------------
# F-P1: the observer must cover the full requested duration
# --------------------------------------------------------------------------
class TestFp1ObservationWindow:
    def test_observer_covers_full_requested_window(self, tmp_path):
        clock = FakeClock()
        out = tmp_path / "observation.jsonl"
        argv = [
            "observer",
            "--password",
            "synthetic-no-secret",
            "--duration",
            "86400",
            "--interval",
            "60",
            "--out",
            str(out),
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(observe_trading_runtime.time, "time", clock.time),
            patch.object(observe_trading_runtime.asyncio, "sleep", clock.asleep),
            patch.object(observe_trading_runtime.httpx, "AsyncClient", FakeTradingClient),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exit_code = asyncio.run(observe_trading_runtime.main())

        assert exit_code == 0
        samples = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        # 24h at 60s => first sample + 1440 intervals => at least 1441 samples.
        assert len(samples) >= 1441
        span_ms = samples[-1]["ts_ms"] - samples[0]["ts_ms"]
        # Hand-computed floor: a 86400s request must record >= 24h.  The old
        # implementation recorded 23.9833...h (one interval short).
        assert span_ms >= DAY_MS, f"window too short: {span_ms / 3_600_000}h"
        assert samples[-1]["ts_ms"] >= int((T0 + 86400) * 1000)
        # Every sample must carry the runtime identity it was served by.
        for sample in samples:
            runtime = sample["runtime"]
            assert runtime["release_id"] == RELEASE_ID
            assert runtime["commit"] == COMMIT
            assert runtime["image_digest"] == DIGEST
            assert runtime["observed_at_ms"] == sample["ts_ms"]

    def test_strict_24h_floor_is_not_relaxed(self, tmp_path):
        runtime = valid_binding()["runtime"]
        # 1440 samples at 60s span exactly 23.983h - one interval short of 24h.
        short = valid_window_samples(runtime, count=1440)
        short_path = write_jsonl(tmp_path / "short.jsonl", short)
        binding_path = tmp_path / "binding.json"
        binding_path.write_text(json.dumps(valid_binding()), encoding="utf-8")
        exit_code, summary = run_summarize(short_path, binding_path)
        assert exit_code != 0
        assert "STRICT_WINDOW_SHORTER_THAN_24H" in summary["blockers"]
        assert summary["ok"] is False

    def test_strict_min_hours_cannot_be_lowered_below_24(self, tmp_path):
        runtime = valid_binding()["runtime"]
        path = write_jsonl(tmp_path / "w.jsonl", valid_window_samples(runtime, count=13))
        binding_path = tmp_path / "binding.json"
        binding_path.write_text(json.dumps(valid_binding()), encoding="utf-8")
        with pytest.raises(SystemExit) as excinfo:
            run_summarize(path, binding_path, "--strict-min-hours", "23.9")
        assert excinfo.value.code == 2

    def test_full_window_with_valid_binding_passes_strict(self, tmp_path):
        binding = valid_binding()
        runtime = binding["runtime"]
        samples = valid_window_samples(runtime, count=1441)
        # Both marker families are required inside the window: an active
        # reconnect and a completed runtime restart pair.
        samples.append({"ts_ms": int(T0 * 1000) + 300_000, "marker": "active_reconnect"})
        samples.append({"ts_ms": int(T0 * 1000) + 120_000, "marker": "runtime_restart"})
        samples.append({"ts_ms": int(T0 * 1000) + 180_000, "marker": "runtime_restart_end"})
        window = write_jsonl(tmp_path / "window.jsonl", samples)
        binding_path = tmp_path / "binding.json"
        binding_path.write_text(json.dumps(binding), encoding="utf-8")
        exit_code, summary = run_summarize(window, binding_path)
        assert (exit_code, summary["blockers"]) == (0, [])
        assert summary["duration_hours"] >= 24.0

    def test_client_initialization_time_does_not_shorten_window(self, tmp_path):
        """A slow AsyncClient setup must not eat the requested window.

        Hand computation: setup takes 2s, so the first recorded sample is at
        T0+2 and the deadline is T0+86402.  Samples at 60s spacing give
        1 + 86400/60 = 1441 samples and a span of exactly 86,400,000 ms.
        """
        clock = FakeClock()
        exit_code, samples = _run_observer(
            tmp_path,
            clock,
            _latency_client(clock, init_seconds=2.0),
            duration="86400",
            interval="60",
        )
        assert exit_code == 0
        assert len(samples) == 1441
        assert samples[0]["ts_ms"] == int((T0 + 2) * 1000)
        assert samples[-1]["ts_ms"] - samples[0]["ts_ms"] == DAY_MS

    def test_per_request_latency_does_not_shorten_window(self, tmp_path):
        """Per-request latency must not shrink the recorded span either.

        Hand computation (1s per request, login only on the first sample):
        first observed_at=T0, requests end T0+4, then each later round is
        3s of requests + 60s sleep.  observed_at runs 0, 64, 127, ...;
        sample 1372 is at T0+86374 and sleeps only the 26s left, so the
        last sample lands at T0+86403 => 1373 samples, span 86,403,000 ms
        (>= 24h, overshoot bounded by one request batch).
        """
        clock = FakeClock()
        exit_code, samples = _run_observer(
            tmp_path,
            clock,
            _latency_client(clock, request_seconds=1.0),
            duration="86400",
            interval="60",
        )
        assert exit_code == 0
        assert len(samples) == 1373
        span_ms = samples[-1]["ts_ms"] - samples[0]["ts_ms"]
        assert span_ms >= DAY_MS, f"window too short: {span_ms}ms"
        assert span_ms <= DAY_MS + 5_000, f"unbounded overshoot: {span_ms}ms"
        assert samples[-1]["ts_ms"] >= int((T0 + 86400) * 1000)

    def test_interval_not_dividing_duration_ends_exactly_on_deadline(self, tmp_path):
        """100s at 30s intervals: the last sleep is truncated to 10s.

        Hand computation: samples at 0, 30, 60, 90 and 100 => 5 samples,
        first-to-last span exactly 100,000 ms (no 130s overshoot).
        """
        clock = FakeClock()
        exit_code, samples = _run_observer(
            tmp_path,
            clock,
            _latency_client(clock),
            duration="100",
            interval="30",
        )
        assert exit_code == 0
        assert [sample["ts_ms"] for sample in samples] == [
            int(T0 * 1000),
            int((T0 + 30) * 1000),
            int((T0 + 60) * 1000),
            int((T0 + 90) * 1000),
            int((T0 + 100) * 1000),
        ]
        assert samples[-1]["ts_ms"] - samples[0]["ts_ms"] == 100_000
        for sample in samples:
            assert sample["runtime"]["observed_at_ms"] == sample["ts_ms"]


# --------------------------------------------------------------------------
# F-P2: the binding is validated against facts, not declared flags
# --------------------------------------------------------------------------
class TestFp2BindingFacts:
    def test_baseline_binding_is_consistent(self):
        binding = valid_binding()
        assert binding["errors"] == []
        assert all(binding["checks"].values())
        blockers = release_binding.validate_binding(
            binding,
            expected_release_id=RELEASE_ID,
            expected_commit=COMMIT,
            expected_digest=DIGEST,
        )
        assert blockers == []

    def test_parent_counterexample_is_rejected(self, tmp_path):
        """The parent's contradictory binding + unbound 1970 samples must fail."""
        records = [
            {
                "ts_ms": minute * 60_000,
                "health": {"status_code": 200, "body": {"ready": True}},
                "metrics": METRICS,
            }
            for minute in range(1441)
        ]
        records.extend(
            [
                {"ts_ms": 120_000, "marker": "runtime_restart_end"},
                {"ts_ms": 180_000, "marker": "active_reconnect"},
            ]
        )
        source = write_jsonl(tmp_path / "window.jsonl", records)
        binding_path = tmp_path / "binding.json"
        binding_path.write_text(
            json.dumps(
                {
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
                    "checks": {name: True for name in release_binding.REQUIRED_CHECKS},
                }
            ),
            encoding="utf-8",
        )
        exit_code, summary = run_summarize(source, binding_path)
        assert exit_code != 0
        assert summary["ok"] is False
        blockers = summary["blockers"]
        for expected in (
            "RELEASE_RUNTIME_ERROR_PRESENT:IMAGE_DIGEST_MISMATCH:execution",
            "RELEASE_RUNTIME_GENERATED_AT_INVALID",
            "RELEASE_RUNTIME_CONTAINER_NOT_RUNNING:control",
            "RELEASE_RUNTIME_CONTAINER_NOT_RUNNING:execution",
            "RELEASE_RUNTIME_CONTAINER_IMAGE_MISMATCH:control",
            "RELEASE_RUNTIME_CONTAINER_IMAGE_MISMATCH:execution",
            "RELEASE_RUNTIME_HEALTH_MISSING",
            "SAMPLE_RUNTIME_IDENTITY_MISSING",
        ):
            assert expected in blockers, f"missing blocker {expected}: {blockers}"

    @pytest.mark.parametrize(
        "mutate, expected",
        [
            # Collector errors: non-empty is fatal, a missing key cannot prove
            # "empty" and must fail closed too.
            (
                lambda b: b.update(errors=["IMAGE_DIGEST_MISMATCH:execution"]),
                "RELEASE_RUNTIME_ERROR_PRESENT:IMAGE_DIGEST_MISMATCH:execution",
            ),
            (lambda b: b.pop("errors"), "RELEASE_RUNTIME_ERRORS_MISSING"),
            # Checks are facts to be carried, not trusted.
            (lambda b: b.pop("checks"), "RELEASE_RUNTIME_CHECKS_MISSING"),
            (
                lambda b: b["checks"].update(all_running=False),
                "RELEASE_RUNTIME_CHECK_FAILED:all_running",
            ),
            # Timestamps must be real.
            (
                lambda b: b.update(generated_at="invalid-timestamp"),
                "RELEASE_RUNTIME_GENERATED_AT_INVALID",
            ),
            # Containers.
            (lambda b: b["containers"].pop("execution"), "RELEASE_RUNTIME_CONTAINERS_MISSING"),
            (
                lambda b: b["containers"]["control"].update(present=False),
                "RELEASE_RUNTIME_CONTAINER_NOT_PRESENT:control",
            ),
            (
                lambda b: b["containers"]["control"].update(running=False),
                "RELEASE_RUNTIME_CONTAINER_NOT_RUNNING:control",
            ),
            (
                lambda b: b["containers"]["control"].update(health="starting"),
                "RELEASE_RUNTIME_CONTAINER_UNHEALTHY:control:starting",
            ),
            (
                lambda b: b["containers"]["control"].update(
                    image="ghcr.io/example/qts@sha256:" + "b" * 64
                ),
                "RELEASE_RUNTIME_CONTAINER_IMAGE_MISMATCH:control",
            ),
            # Labels.
            (
                lambda b: b["containers"]["control"]["labels"].update(
                    {LABEL_RELEASE_ID: "other-rel"}
                ),
                "RELEASE_RUNTIME_LABEL_MISMATCH:control:release_id",
            ),
            (
                lambda b: b["containers"]["control"]["labels"].update(
                    {LABEL_RELEASE_COMMIT: "f" * 40}
                ),
                "RELEASE_RUNTIME_LABEL_MISMATCH:control:commit",
            ),
            (
                lambda b: b["containers"]["control"]["labels"].update(
                    {LABEL_ENVIRONMENT: "staging"}
                ),
                "RELEASE_RUNTIME_LABEL_MISMATCH:control:environment",
            ),
            # Health endpoints.
            (
                lambda b: b["health"]["live"].update(status=503),
                "RELEASE_RUNTIME_HEALTH_NOT_READY:live",
            ),
            (
                lambda b: b["health"]["ready"].update(status=503),
                "RELEASE_RUNTIME_HEALTH_NOT_READY:ready",
            ),
            (
                lambda b: b["health"]["trading"]["body"].update(ready=False),
                "RELEASE_RUNTIME_HEALTH_NOT_READY:trading",
            ),
            # Runtime identity block.
            (lambda b: b.pop("runtime"), "RELEASE_RUNTIME_IDENTITY_MISSING"),
            (
                lambda b: b["runtime"].update(release_id=""),
                "RELEASE_RUNTIME_IDENTITY_MISSING:release_id",
            ),
            (
                lambda b: b["runtime"].update(release_id="other-rel"),
                "RELEASE_RUNTIME_IDENTITY_MISMATCH:release_id",
            ),
            (
                lambda b: b["runtime"].update(commit="f" * 40),
                "RELEASE_RUNTIME_IDENTITY_MISMATCH:commit",
            ),
            (
                lambda b: b["runtime"].update(image_digest="sha256:" + "b" * 64),
                "RELEASE_RUNTIME_IDENTITY_MISMATCH:image_digest",
            ),
            (
                lambda b: b["runtime"].update(environment="staging"),
                "RELEASE_RUNTIME_IDENTITY_MISMATCH:environment",
            ),
            (lambda b: b["runtime"].update(instance_owner=""), "RELEASE_RUNTIME_OWNER_MISSING"),
            # The binding must actually be an inspection.
            (lambda b: b.update(runtime_inspected=False), "RELEASE_RUNTIME_BINDING_MISSING"),
        ],
    )
    def test_single_variable_binding_counterexamples(self, mutate, expected):
        payload = copy.deepcopy(valid_binding())
        mutate(payload)
        blockers = release_binding.validate_binding(
            payload,
            expected_release_id=RELEASE_ID,
            expected_commit=COMMIT,
            expected_digest=DIGEST,
        )
        assert blockers, f"expected {expected} to be blocked, got none"
        assert any(code.startswith(expected) for code in blockers), blockers

    @pytest.mark.parametrize(
        "mutate, expected",
        [
            (lambda s: s.pop("runtime"), "SAMPLE_RUNTIME_IDENTITY_MISSING"),
            (
                lambda s: s["runtime"].pop("release_id"),
                "SAMPLE_RUNTIME_IDENTITY_MISSING:release_id",
            ),
            (
                lambda s: s["runtime"].update(release_id="other-rel"),
                "SAMPLE_RUNTIME_IDENTITY_MISMATCH:release_id",
            ),
            (
                lambda s: s["runtime"].update(commit="f" * 40),
                "SAMPLE_RUNTIME_IDENTITY_MISMATCH:commit",
            ),
            (
                lambda s: s["runtime"].update(image_digest="sha256:" + "b" * 64),
                "SAMPLE_RUNTIME_IDENTITY_MISMATCH:image_digest",
            ),
            (
                lambda s: s["runtime"].update(environment="staging"),
                "SAMPLE_RUNTIME_IDENTITY_MISMATCH:environment",
            ),
            (lambda s: s["runtime"].pop("observed_at_ms"), "SAMPLE_RUNTIME_TIMESTAMP_MISSING"),
            (
                lambda s: s["runtime"].update(observed_at_ms=str(s["ts_ms"])),
                "SAMPLE_RUNTIME_TIMESTAMP_MISSING",
            ),
            (
                lambda s: s["runtime"].update(observed_at_ms=s["ts_ms"] - 10 * 60_000),
                "SAMPLE_RUNTIME_TIMESTAMP_MISMATCH",
            ),
        ],
    )
    def test_sample_identity_counterexamples(self, mutate, expected):
        runtime = valid_binding()["runtime"]
        sample = make_sample(runtime, int(T0 * 1000))
        mutate(sample)
        blockers = release_binding.validate_sample_identity(sample, binding_runtime=runtime)
        assert any(code.startswith(expected) for code in blockers), blockers

    def test_sample_identity_accepts_bound_sample(self):
        runtime = valid_binding()["runtime"]
        ts_ms = int(T0 * 1000)
        sample = make_sample(runtime, ts_ms)
        assert release_binding.validate_sample_identity(sample, binding_runtime=runtime) == []

    @pytest.mark.parametrize(
        "generated_at_ms, expected",
        [
            # 10 minutes before the window (beyond the 60s clock skew).
            (int(T0 * 1000) - 600_000, "RELEASE_BINDING_WINDOW_MISMATCH"),
            (int(T0 * 1000) + 7_200_000, None),  # inside the window
            # Exactly at the 1h post-window lag boundary is still allowed.
            (int((T0 + 86_400) * 1000) + 3_600_000, None),
            (int((T0 + 86_400) * 1000) + 3_600_001, "RELEASE_BINDING_WINDOW_MISMATCH"),
            (int((T0 + 86_400) * 1000) + 5 * 3_600_000, "RELEASE_BINDING_WINDOW_MISMATCH"),
        ],
    )
    def test_binding_window_anchor(self, generated_at_ms, expected):
        blockers = release_binding.validate_window_anchor(
            generated_at_ms=generated_at_ms,
            first_sample_ts_ms=int(T0 * 1000),
            last_sample_ts_ms=int((T0 + 86_400) * 1000),
        )
        if expected is None:
            assert blockers == []
        else:
            assert blockers == [expected]

    def test_window_mismatch_blocks_strict_summary(self, tmp_path):
        binding = valid_binding()
        binding["generated_at"] = _iso(T0 - 3_600)
        samples = valid_window_samples(binding["runtime"], count=13)
        window = write_jsonl(tmp_path / "window.jsonl", samples)
        binding_path = tmp_path / "binding.json"
        binding_path.write_text(json.dumps(binding), encoding="utf-8")
        exit_code, summary = run_summarize(window, binding_path)
        assert exit_code != 0
        assert "RELEASE_BINDING_WINDOW_MISMATCH" in summary["blockers"]


# --------------------------------------------------------------------------
# F-P3 (offline logic): psql rendering, convergence and bounded polling
# --------------------------------------------------------------------------
class TestFp3Parsing:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("t", True),
            ("f", False),
            ("true", True),
            ("false", False),
            ("1", True),
            ("0", False),
            ("yes", True),
            ("no", False),
            ("on", True),
            ("off", False),
            ("T", True),
            ("F", False),
            ("  t  ", True),
            ("", None),
            (None, None),
            ("maybe", None),
            ("TRUE", True),
        ],
    )
    def test_parse_boolean(self, value, expected):
        assert deploy_gate.parse_boolean(value) is expected

    @pytest.mark.parametrize(
        "value, expected",
        [("3", 3), (" 3 ", 3), ("0", 0), ("", None), (None, None), ("n/a", None)],
    )
    def test_parse_count(self, value, expected):
        assert deploy_gate.parse_count(value) is expected


def _healthy_containers() -> dict:
    return {
        service: {"present": True, "running": True, "health": "healthy"}
        for service in EXPECTED_SERVICES
    }


def _clean_facts() -> dict:
    return {
        "new_risk_blocked": True,
        "unknown_commands": 0,
        "queued_commands": 0,
        "sending_commands": 0,
        "outbox_pending": 0,
        "unbooked_fills": 0,
        "lease": {"owner": "exec-1", "fencing_token": "7", "valid": True},
        "runtime": {"owner": "exec-1", "token": "7", "ready": True},
    }


class TestFp3PreGateConvergence:
    def test_pre_rejects_queued_sending_and_unbooked(self):
        """Parent counterexample: pre must not ignore QUEUED/SENDING/unbooked."""
        facts = _clean_facts()
        facts.update(queued_commands=1, sending_commands=1, unbooked_fills=1)
        errors = deploy_gate.evaluate(
            mode="pre",
            containers=_healthy_containers(),
            health={"live": {"status": 200}, "ready": {"status": 200}},
            facts=facts,
            expected_release_id=RELEASE_ID,
            expected_commit=COMMIT,
            expected_digest=DIGEST,
        )
        assert set(errors) >= {
            "COMMANDS_NOT_CONVERGED:QUEUED",
            "COMMANDS_NOT_CONVERGED:SENDING",
            "UNBOOKED_FILLS_PRESENT",
        }

    def test_pre_converged_facts_pass(self):
        errors = deploy_gate.evaluate(
            mode="pre",
            containers=_healthy_containers(),
            health={"live": {"status": 200}, "ready": {"status": 200}},
            facts=_clean_facts(),
            expected_release_id=RELEASE_ID,
            expected_commit=COMMIT,
            expected_digest=DIGEST,
        )
        assert errors == []

    @pytest.mark.parametrize(
        "mutate, expected",
        [
            (lambda f: f.pop("queued_commands"), "FACTS_INVALID:queued_commands"),
            (lambda f: f.update(queued_commands=None), "FACTS_INVALID:queued_commands"),
            (lambda f: f.pop("sending_commands"), "FACTS_INVALID:sending_commands"),
            (lambda f: f.pop("unknown_commands"), "FACTS_INVALID:unknown_commands"),
            (lambda f: f.pop("outbox_pending"), "FACTS_INVALID:outbox_pending"),
            (lambda f: f.pop("unbooked_fills"), "FACTS_INVALID:unbooked_fills"),
            (lambda f: f.pop("lease"), "FACTS_INVALID:lease"),
            (lambda f: f.pop("runtime"), "FACTS_INVALID:runtime"),
            (lambda f: f.update(new_risk_blocked=None), "NEW_RISK_STATE_UNKNOWN"),
            (lambda f: f.update(new_risk_blocked=False), "NEW_RISK_NOT_BLOCKED"),
            (lambda f: f["lease"].update(valid=False), "EXECUTION_LEASE_INVALID"),
            (lambda f: f["runtime"].update(owner="other"), "EXECUTION_OWNER_NOT_ALIGNED"),
            (lambda f: f["runtime"].update(token="9"), "EXECUTION_FENCING_NOT_ALIGNED"),
        ],
    )
    def test_pre_fact_failures(self, mutate, expected):
        facts = _clean_facts()
        mutate(facts)
        errors = deploy_gate.evaluate(
            mode="pre",
            containers=_healthy_containers(),
            health={"live": {"status": 200}, "ready": {"status": 200}},
            facts=facts,
            expected_release_id=RELEASE_ID,
            expected_commit=COMMIT,
            expected_digest=DIGEST,
        )
        assert expected in errors, errors


def _gate_snapshot(health_status: str) -> dict:
    containers = {
        service: {"present": True, "running": True, "health": health_status}
        for service in EXPECTED_SERVICES
    }
    for service in APP_SERVICES:
        containers[service]["image"] = IMAGE
        containers[service]["labels"] = {
            LABEL_RELEASE_ID: RELEASE_ID,
            LABEL_RELEASE_COMMIT: COMMIT,
            LABEL_ENVIRONMENT: "demo",
        }
    return {
        "containers": containers,
        "health": {
            "live": {"status": 200},
            "ready": {"status": 200},
            "trading": {"status": 200, "body": {"ready": True}},
        },
        "facts": _clean_facts(),
    }


def _evaluate_post(snapshot: dict) -> list[str]:
    return deploy_gate.evaluate(
        mode="post",
        containers=snapshot["containers"],
        health=snapshot["health"],
        facts=snapshot["facts"],
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
    )


class TestFp3BoundedPostGatePolling:
    def test_post_gate_polls_until_healthy(self):
        clock = FakeClock(1000.0)
        sleeps: list[float] = []
        calls = {"count": 0}

        def collect_snapshot() -> dict:
            calls["count"] += 1
            return _gate_snapshot("starting" if calls["count"] <= 2 else "healthy")

        def sleep_fn(seconds: float) -> None:
            sleeps.append(seconds)
            clock.current += seconds

        snapshot, errors = deploy_gate.run_gate(
            mode="post",
            collect_snapshot=collect_snapshot,
            evaluate_snapshot=_evaluate_post,
            wait_timeout=60.0,
            poll_interval=15.0,
            sleep_fn=sleep_fn,
            clock=clock.monotonic,
        )
        assert errors == []
        assert snapshot["attempts"] == 3
        assert sleeps == [15.0, 15.0]

    def test_post_gate_times_out_bounded(self):
        clock = FakeClock(1000.0)
        sleeps: list[float] = []
        calls = {"count": 0}

        def collect_snapshot() -> dict:
            calls["count"] += 1
            return _gate_snapshot("starting")

        def sleep_fn(seconds: float) -> None:
            sleeps.append(seconds)
            clock.current += seconds

        snapshot, errors = deploy_gate.run_gate(
            mode="post",
            collect_snapshot=collect_snapshot,
            evaluate_snapshot=_evaluate_post,
            wait_timeout=60.0,
            poll_interval=15.0,
            sleep_fn=sleep_fn,
            clock=clock.monotonic,
        )
        # Hand-computed: attempts at t=0,15,30,45,60 (deadline) => 5 attempts,
        # 4 sleeps, then a bounded failure - never an endless loop.
        assert errors
        assert all(code.startswith("CONTAINER_NOT_HEALTHY") for code in errors)
        assert snapshot["attempts"] == 5
        assert sleeps == [15.0, 15.0, 15.0, 15.0]
        assert clock.current - 1000.0 == 60.0

    def test_pre_gate_never_waits(self):
        clock = FakeClock(1000.0)
        sleeps: list[float] = []
        calls = {"count": 0}

        def collect_snapshot() -> dict:
            calls["count"] += 1
            return _gate_snapshot("starting")

        def sleep_fn(seconds: float) -> None:
            sleeps.append(seconds)
            clock.current += seconds

        snapshot, errors = deploy_gate.run_gate(
            mode="pre",
            collect_snapshot=collect_snapshot,
            evaluate_snapshot=_evaluate_post,
            wait_timeout=60.0,
            poll_interval=15.0,
            sleep_fn=sleep_fn,
            clock=clock.monotonic,
        )
        assert errors
        assert snapshot["attempts"] == 1
        assert sleeps == []


# --------------------------------------------------------------------------
# F-P5: operator inputs never reach a shell as text
# --------------------------------------------------------------------------
@lru_cache
def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _all_steps(workflow: dict):
    for job_name, job in workflow["jobs"].items():
        for index, step in enumerate(job.get("steps", [])):
            yield job_name, index, step


def _protected_consumer_jobs():
    yield DEPLOY_WORKFLOW, "preflight"
    yield DEPLOY_WORKFLOW, "deploy"
    yield EVIDENCE_WORKFLOW, "collect"


class TestFp5InputInjection:
    @pytest.mark.parametrize("name", [DEPLOY_WORKFLOW, EVIDENCE_WORKFLOW])
    def test_no_input_expansion_inside_run_blocks(self, name):
        workflow = _workflow(name)
        for job_name, _index, step in _all_steps(workflow):
            script = step.get("run")
            if script is None:
                continue
            assert (
                "${{ inputs." not in script
            ), f"{name}/{job_name}/{step.get('name')} expands inputs into a shell"

    @pytest.mark.parametrize("job", ["preflight", "deploy"])
    def test_deploy_demo_validates_inputs_with_failure_codes(self, job):
        steps = _workflow(DEPLOY_WORKFLOW)["jobs"][job]["steps"]
        validation = [s for s in steps if "Validate the operator inputs" in (s.get("name") or "")]
        assert len(validation) == 1
        step = validation[0]
        env = step.get("env") or {}
        for key in ("RELEASE_BUILD_RUN_ID", "RELEASE_COMMIT", "RELEASE_IMAGE_DIGEST", "RELEASE_ID"):
            assert key in env, f"{job}: {key} not staged through step env"
            assert "${{ inputs." in env[key]
        for failure_code in (
            "RELEASE_BUILD_RUN_ID_INVALID",
            "RELEASE_COMMIT_INVALID",
            "RELEASE_IMAGE_DIGEST_INVALID",
            "RELEASE_ID_INVALID",
        ):
            assert failure_code in step["run"]
        assert "=~" in step["run"], "validation must use bash regex matching"

    def test_demo_evidence_validates_observation_hours(self):
        steps = _workflow(EVIDENCE_WORKFLOW)["jobs"]["collect"]["steps"]
        validation = [s for s in steps if "Validate the operator inputs" in (s.get("name") or "")]
        assert len(validation) == 1
        step = validation[0]
        assert "OBSERVATION_HOURS_INVALID" in step["run"]
        assert (step.get("env") or {}).get("OBSERVATION_HOURS") == "${{ inputs.observation_hours }}"
        # The strict 24h floor is enforced in its own step via env, not shell
        # expansion, and the floor value itself is unchanged.
        floor = [s for s in steps if "strict observation floor" in (s.get("name") or "")]
        assert len(floor) == 1
        assert "OBSERVATION_HOURS_BELOW_24" in floor[0]["run"]
        assert "${{ inputs." not in floor[0]["run"]

    @pytest.mark.parametrize("name, job", list(_protected_consumer_jobs()))
    def test_provenance_uses_env_before_release_checkout(self, name, job):
        steps = _workflow(name)["jobs"][job]["steps"]
        names = [s.get("name") or "" for s in steps]
        validation_index = next(
            i for i, n in enumerate(names) if "Validate the operator inputs" in n
        )
        provenance_index = next(
            i for i, n in enumerate(names) if "Verify release build provenance" in n
        )
        checkout_index = next(
            i
            for i, s in enumerate(steps)
            if s.get("uses", "").startswith("actions/checkout")
            and (s.get("with") or {}).get("ref") == "${{ inputs.commit }}"
        )
        # Validation and provenance must both run before the arbitrary checkout.
        assert validation_index < provenance_index < checkout_index
        provenance = steps[provenance_index]
        env = provenance.get("env") or {}
        assert env.get("RELEASE_EVIDENCE_DIR") == "${{ runner.temp }}/qts-release"
        for key in ("RELEASE_BUILD_RUN_ID", "RELEASE_COMMIT", "RELEASE_IMAGE_DIGEST", "RELEASE_ID"):
            assert key in env
        run = provenance["run"]
        assert "verify_release_provenance.py" in run
        assert '"$RELEASE_EVIDENCE_DIR/release-manifest.json"' in run
        assert "${{ inputs." not in run

    @pytest.mark.parametrize("name, job", list(_protected_consumer_jobs()))
    def test_provenance_step_has_no_demo_secrets(self, name, job):
        steps = _workflow(name)["jobs"][job]["steps"]
        provenance = next(
            s for s in steps if "Verify release build provenance" in (s.get("name") or "")
        )
        env = provenance.get("env") or {}
        assert set(env) == {
            "GITHUB_TOKEN",
            "RELEASE_BUILD_RUN_ID",
            "RELEASE_COMMIT",
            "RELEASE_IMAGE_DIGEST",
            "RELEASE_ID",
            "RELEASE_EVIDENCE_DIR",
        }
        assert env["GITHUB_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"


# --------------------------------------------------------------------------
# F-P8: single upload root and a checkout-safe evidence directory
# --------------------------------------------------------------------------
class TestFp8ArtifactChain:
    def test_release_build_uploads_a_single_reports_root(self):
        workflow = _workflow(RELEASE_BUILD_WORKFLOW)
        uploads = [
            step
            for _job, _i, step in _all_steps(workflow)
            if step.get("uses", "").startswith("actions/upload-artifact")
        ]
        assert len(uploads) == 1
        upload = uploads[0]
        with_options = upload.get("with") or {}
        # A single string path `reports/`: any second root would move the
        # artifact root to the repository common ancestor.
        assert with_options.get("path") == "reports/"
        assert with_options.get("if-no-files-found") == "error"

    def test_release_manifest_and_smoke_live_under_reports(self):
        workflow = _workflow(RELEASE_BUILD_WORKFLOW)
        runs = [s["run"] for _j, _i, s in _all_steps(workflow) if "run" in s]
        joined = "\n".join(runs)
        assert "--output reports/release-manifest.json" in joined
        assert "--evidence-dir reports/compose-smoke" in joined
        assert "--startup-smoke reports/compose-smoke/compose-smoke.json" in joined

    def test_single_root_download_layout(self):
        """Hand-computed upload/download mapping for a single `reports/` root."""
        uploaded = {
            "reports/release-manifest.json",
            "reports/compose-smoke/compose-smoke.json",
            "reports/sbom.cdx.json",
            "reports/trivy.sarif",
        }
        root = "reports/"
        downloaded = {path[len(root) :] for path in uploaded if path.startswith(root)}
        # The manifest is at the artifact top level, the smoke evidence in its
        # subdirectory - exactly what the consumer paths expect.
        assert "release-manifest.json" in downloaded
        assert "compose-smoke/compose-smoke.json" in downloaded

    @pytest.mark.parametrize("name, job", list(_protected_consumer_jobs()))
    def test_consumers_download_outside_the_checkout(self, name, job):
        steps = _workflow(name)["jobs"][job]["steps"]
        downloads = [s for s in steps if s.get("uses", "").startswith("actions/download-artifact")]
        evidence_downloads = [
            s for s in downloads if (s.get("with") or {}).get("name") == "release-evidence"
        ]
        assert len(evidence_downloads) == 1
        assert evidence_downloads[0]["with"]["path"] == "${{ runner.temp }}/qts-release"
        # The second checkout relies on the default (clean: true); the evidence
        # survives only because it lives outside the workspace.
        for step in steps:
            if step.get("uses", "").startswith("actions/checkout"):
                assert (step.get("with") or {}).get("path") is None

    def test_three_consumer_jobs_share_one_manifest_path(self):
        paths = set()
        for name, job in _protected_consumer_jobs():
            steps = _workflow(name)["jobs"][job]["steps"]
            provenance = next(
                s for s in steps if "Verify release build provenance" in (s.get("name") or "")
            )
            run = provenance["run"]
            assert '"$RELEASE_EVIDENCE_DIR/release-manifest.json"' in run
            env = provenance.get("env") or {}
            assert env["RELEASE_EVIDENCE_DIR"] == "${{ runner.temp }}/qts-release"
            paths.add(env["RELEASE_EVIDENCE_DIR"] + "/release-manifest.json")
            preflight = next(
                (s for s in steps if "deploy_preflight.py" in (s.get("run") or "")),
                None,
            )
            if preflight is not None:
                assert '"$RELEASE_EVIDENCE_DIR/release-manifest.json"' in preflight["run"]
        assert len(paths) == 1
        assert paths == {"${{ runner.temp }}/qts-release/release-manifest.json"}

    def test_deploy_demo_does_not_upload_runtime_binding(self):
        text = (WORKFLOWS / DEPLOY_WORKFLOW).read_text(encoding="utf-8")
        assert "release-binding" not in text
        assert "runtime-binding" not in text

    def test_binding_is_collected_after_the_window(self):
        steps = _workflow(EVIDENCE_WORKFLOW)["jobs"]["collect"]["steps"]
        names = [s.get("name") or "" for s in steps]
        observation_index = next(i for i, n in enumerate(names) if n == "Run observation window")
        binding_index = next(
            i for i, n in enumerate(names) if n == "Collect the runtime release binding"
        )
        gate_index = next(i for i, n in enumerate(names) if "strict release observation gate" in n)
        assert observation_index < binding_index < gate_index
        binding_step = steps[binding_index]
        assert "collect_runtime_release_binding.py" in binding_step["run"]
        gate_run = steps[gate_index]["run"]
        assert "artifacts/demo/release-binding.json" in gate_run
        assert "--strict-release" in gate_run


# --------------------------------------------------------------------------
# Regression: the manifest stays a unique, complete classification
# --------------------------------------------------------------------------
class TestManifestRegression:
    def test_manifest_files_are_uniquely_classified(self):
        manifest = json.loads((ROOT / "ci" / "test_manifest.json").read_text(encoding="utf-8"))
        groups = manifest["groups"]
        classified: dict[str, str] = {}
        for group, files in groups.items():
            for name in files:
                assert name not in classified, f"{name} in both {classified[name]} and {group}"
                classified[name] = group
        assert len(classified) == 263
        for name in classified:
            assert (ROOT / "trader" / "tests" / name).exists(), f"missing test file {name}"
        assert classified["test_cicd_parentfix2_offline.py"] == "offline"
        assert classified["test_cicd_parentfix2_postgres.py"] == "postgres"

    def test_required_jobs_target_p0_offline_and_postgres(self):
        manifest = json.loads((ROOT / "ci" / "test_manifest.json").read_text(encoding="utf-8"))
        jobs = manifest["ci_jobs"]
        for job, group in (
            ("p0-gate", "p0"),
            ("offline-gate", "offline"),
            ("postgres-integration", "postgres"),
        ):
            assert jobs[job]["group"] == group
            assert jobs[job]["required"] is True
