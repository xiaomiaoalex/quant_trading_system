"""Integration-phase counterexamples for the merged CI/CD + foundation stack.

Authored by the fresh independent tester for the ``cicd-foundation-integration``
phase, before reading any engineer conclusion.  Expectations come from the
merged acceptance record (``docs/UNIFIED_TRADING_ACCEPTANCE.md``), the phase-6
observer contract (``--password-file``, ``kind`` records, runtime identity) and
the fail-closed manifest/coverage gates.

Group: ``offline``.  No network and no Docker: the observer is driven through a
fake clock and a fake ``httpx`` client, and the Compose/Dockerfile check is a
static parse.  A YAML parse is never accepted as behavioural evidence where a
real entry point exists - so the strict release gate is driven end to end with
the *real* observer output written by the *real* observer.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import observe_trading_runtime, summarize_observation  # noqa: E402
from scripts.collect_runtime_release_binding import (  # noqa: E402
    APP_SERVICES,
    EXPECTED_SERVICES,
    LABEL_ENVIRONMENT,
    LABEL_RELEASE_COMMIT,
    LABEL_RELEASE_ID,
    build_binding,
    runtime_identity_from_health,
)

COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64
RELEASE_ID = "integration-cicd-foundation"
T0 = 1_800_000_000.0
DAY_MS = 86_400_000
INTERVAL_SECONDS = 60

# Phase 6 deleted these modules; the merged gates must not carry them.
DELETED_MODULES = ("trader/core/application/oms.py", "trader/services/oms_callback.py")

DRILL_MARKERS = (
    (120_000, "runtime_restart"),
    (180_000, "runtime_restart_end"),
    (240_000, "active_reconnect"),
)


# --------------------------------------------------------------------------- #
# Fakes: clock and HTTP (no network contact at any point)
# --------------------------------------------------------------------------- #
class FakeClock:
    def __init__(self, start: float = T0) -> None:
        self.current = start

    def time(self) -> float:
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


def _identity_body() -> dict:
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


def _metrics_body() -> dict:
    return {
        "status": "READY",
        "reconciliation_age_ms": 1_000,
        "outbox_backlog": 0,
        "unbooked_fills": 0,
        "unknown_commands": 0,
        "balance_anomalies": 0,
        "unresolved": [],
    }


def _risk_body() -> dict:
    return {"status": "READY", "reasons": [], "generation": 1, "positions": []}


def make_client_factory(*, degraded_rounds=frozenset()):
    """httpx.AsyncClient stand-in answering the observer endpoints.

    The returned counter records how many times ``/health/trading`` answered;
    rounds in ``degraded_rounds`` answer 503 with ``ready=false`` so the
    observer records real health transitions.
    """
    counter = {"health": 0}

    class FakeTradingClient:
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
                counter["health"] += 1
                if counter["health"] in degraded_rounds:
                    body = dict(_identity_body(), ready=False, reasons=["DEGRADED_FOR_TEST"])
                    return FakeResponse(503, body)
                return FakeResponse(200, _identity_body())
            if path == "/v1/trading/metrics":
                return FakeResponse(200, _metrics_body())
            if path.startswith("/v1/trading/accounts/"):
                return FakeResponse(200, _risk_body())
            return FakeResponse(200, {})

    return FakeTradingClient, counter


# --------------------------------------------------------------------------- #
# Binding and window builders (the real collector code, synthetic window)
# --------------------------------------------------------------------------- #
def _iso(epoch_s: float) -> str:
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _valid_containers() -> dict:
    containers: dict = {}
    for service in EXPECTED_SERVICES:
        item = {
            "name": f"qts-integration-{service}-1",
            "present": True,
            "running": True,
            "health": "healthy",
            "image": "postgres:16",
            "image_id": "",
            "labels": {},
        }
        if service in APP_SERVICES:
            item["image"] = "ghcr.io/example/qts@" + DIGEST
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
        "trading": {"status": 200, "body": _identity_body()},
    }


def valid_binding() -> dict:
    """Build the binding exactly the way the real collector builds it."""
    health = _valid_health()
    binding = build_binding(
        project="qts-integration",
        containers=_valid_containers(),
        health=health,
        expected_release_id=RELEASE_ID,
        expected_commit=COMMIT,
        expected_digest=DIGEST,
        runtime=runtime_identity_from_health(health),
    )
    # Anchor the collector timestamp inside the synthetic window by hand; the
    # real collector stamps "now", which would not overlap a synthetic window.
    binding["generated_at"] = _iso(T0 + 60_000)
    return binding


def _run_observer(tmp_path: Path, *, degraded_rounds=frozenset()):
    """Run the real phase-6 observer over a 24h/60s synthetic window."""
    out = tmp_path / "observation.jsonl"
    # Phase 6: credentials are never passed in argv; they come from a file.
    password_file = tmp_path / "operator-password.txt"
    password_file.write_text("synthetic-no-secret\n", encoding="utf-8")
    client_factory, counter = make_client_factory(degraded_rounds=degraded_rounds)
    clock = FakeClock()
    argv = [
        "observer",
        "--password-file",
        str(password_file),
        "--duration",
        "86400",
        "--interval",
        str(INTERVAL_SECONDS),
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
        assert asyncio.run(observe_trading_runtime.main()) == 0
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    return out, lines, counter


def _run_strict_summarize(window: Path, binding_path: Path):
    argv = [
        "summarize",
        "--input",
        str(window),
        "--min-hours",
        "24",
        "--strict-release",
        "--release-evidence",
        str(binding_path),
        "--expected-commit",
        COMMIT,
        "--expected-digest",
        DIGEST,
    ]
    captured = io.StringIO()
    with patch.object(sys, "argv", argv), contextlib.redirect_stdout(captured):
        exit_code = summarize_observation.main()
    return exit_code, json.loads(captured.getvalue())


def _append_drill_markers(handle, window_start_ms: int) -> None:
    for offset_ms, name in DRILL_MARKERS:
        handle.write(json.dumps({"ts_ms": window_start_ms + offset_ms, "marker": name}) + "\n")


# --------------------------------------------------------------------------- #
# The merged observer -> summarize strict chain (phase-6 record format)
# --------------------------------------------------------------------------- #
def test_merged_observer_output_passes_the_strict_release_gate(tmp_path):
    """Real phase-6 observer output must satisfy the real --strict-release gate.

    24h at 60s with one degraded round: the JSONL mixes ``kind="sample"`` lines
    with the ``observer_started``/``observer_finished`` markers and two ready
    transitions.  The gate must count only the samples, ignore the markers and
    transitions, bind every sample to the collected runtime identity and pass
    with zero blockers.
    """
    out, lines, counter = _run_observer(tmp_path, degraded_rounds={3})
    samples = [line for line in lines if line.get("kind") == "sample"]
    markers = [line for line in lines if line.get("kind") == "marker"]
    transitions = [line for line in lines if line.get("kind") == "transition"]
    assert counter["health"] == 1441
    assert len(samples) == 1441
    assert [marker["marker"] for marker in markers] == [
        "observer_started",
        "observer_finished",
    ]
    assert [(item["from_ready"], item["to_ready"]) for item in transitions] == [
        (True, False),
        (False, True),
    ]
    for sample in samples:
        runtime = sample["runtime"]
        assert runtime["release_id"] == RELEASE_ID
        assert runtime["commit"] == COMMIT
        assert runtime["image_digest"] == DIGEST
        assert runtime["environment"] == "demo"
        assert runtime["instance_owner"] == "exec-1"
        assert runtime["observed_at_ms"] == sample["ts_ms"]
        assert isinstance(sample["monotonic_ms"], int)
        assert isinstance(sample["attempts"], list)

    window_start_ms = samples[0]["ts_ms"]
    window_end_ms = samples[-1]["ts_ms"]
    assert window_end_ms - window_start_ms == DAY_MS
    # Drill evidence recorded inside the window, the way the operator appends
    # it to the durable JSONL after a real restart/reconnect drill.
    with out.open("a", encoding="utf-8") as handle:
        _append_drill_markers(handle, window_start_ms)

    binding_path = tmp_path / "binding.json"
    binding_path.write_text(json.dumps(valid_binding()), encoding="utf-8")
    exit_code, summary = _run_strict_summarize(out, binding_path)
    assert (exit_code, summary["ok"], summary["blockers"]) == (0, True, [])
    assert summary["parse_errors"] == 0
    assert summary["sample_identity_checked"] is True
    assert summary["duration_hours"] >= 24.0
    # The degraded round and the marker/transition records were not samples.
    assert summary["ready_ratio"] < 1.0


def test_strict_gate_rejects_a_sample_served_by_a_different_runtime(tmp_path):
    """One sample with a foreign runtime identity must fail the strict gate."""
    out, lines, _ = _run_observer(tmp_path)
    tampered = list(lines)
    for line in tampered:
        if line.get("kind") == "sample":
            line["runtime"]["commit"] = "b" * 40
            break
    drill = [
        {"ts_ms": int(T0 * 1000) + offset_ms, "marker": name} for offset_ms, name in DRILL_MARKERS
    ]
    window = tmp_path / "tampered-window.jsonl"
    payload = "\n".join(json.dumps(item) for item in tampered + drill) + "\n"
    window.write_text(payload, encoding="utf-8")
    binding_path = tmp_path / "binding.json"
    binding_path.write_text(json.dumps(valid_binding()), encoding="utf-8")

    exit_code, summary = _run_strict_summarize(window, binding_path)
    assert exit_code == 1
    assert summary["ok"] is False
    assert "SAMPLE_RUNTIME_IDENTITY_MISMATCH:commit" in summary["blockers"]


# --------------------------------------------------------------------------- #
# The merged manifest stays a unique, complete, disk-derived classification
# --------------------------------------------------------------------------- #
def test_manifest_uniquely_classifies_every_current_test_file():
    manifest = json.loads((ROOT / "ci" / "test_manifest.json").read_text(encoding="utf-8"))
    classified: dict[str, str] = {}
    for group, files in manifest["groups"].items():
        assert files, f"group {group} is empty"
        for name in files:
            assert name not in classified, f"{name} double-classified: {group}"
            classified[name] = group
    on_disk = {path.name for path in (ROOT / "trader" / "tests").glob("test_*.py")}
    assert on_disk == set(classified), (
        f"unclassified: {sorted(on_disk - set(classified))}; "
        f"stale: {sorted(set(classified) - on_disk)}"
    )
    # The merged baseline size is derived from disk (235 at integration time).
    # The regression to catch is a stale, missing or duplicated classification,
    # not a magic constant; this file itself must be classified as offline.
    assert len(on_disk) == len(classified)
    assert len(on_disk) > 0
    assert classified[Path(__file__).name] == "offline"


# --------------------------------------------------------------------------- #
# Coverage floors dropped the deleted modules, not the merged bar
# --------------------------------------------------------------------------- #
def test_coverage_thresholds_dropped_the_deleted_modules_not_the_merged_bar():
    payload = json.loads((ROOT / "ci" / "coverage_thresholds.json").read_text(encoding="utf-8"))
    # Only the actual threshold entries count; the notes may mention the
    # historical module names as prose.
    paths = {entry["path"] for entry in payload["thresholds"]}
    for deleted in DELETED_MODULES:
        assert deleted not in paths, f"deleted module still carries a floor: {deleted}"
        assert not (ROOT / deleted).exists(), f"deleted module reappeared: {deleted}"
    for entry in payload["thresholds"]:
        if entry["kind"] == "overall":
            assert entry["path"] == ""
            assert entry["min_percent"] >= 75.0, entry
            continue
        assert (ROOT / entry["path"]).exists(), f"floor targets a missing path: {entry}"
    # The floors still guard the unified trading core that replaced the legacy
    # OMS callback path.
    assert "trader/trading/service.py" in paths
    assert "trader/trading/" in paths


# --------------------------------------------------------------------------- #
# The demo overlay specialises the single foundation stack
# --------------------------------------------------------------------------- #
class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that collapses Compose YAML tags (``!reset``, ``!override``)."""


_ComposeLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def test_demo_overlay_and_dockerfile_keep_the_single_foundation_stack():
    compose_text = (ROOT / "compose.yml").read_text(encoding="utf-8")
    overlay_text = (ROOT / "deploy" / "compose.demo.yml").read_text(encoding="utf-8")
    dockerfile_text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    base = yaml.load(compose_text, Loader=_ComposeLoader)
    overlay = yaml.load(overlay_text, Loader=_ComposeLoader)

    # The overlay specialises exactly the foundation services; it neither adds
    # a second app stack nor drops one, and there is exactly one postgres.
    assert set(overlay["services"]) == set(base["services"])
    assert sum(1 for name in base["services"] if "postgres" in name) == 1

    app_services = {
        name for name, cfg in base["services"].items() if isinstance(cfg.get("build"), dict)
    }
    assert {"control", "execution"} <= app_services
    builds = {name: base["services"][name]["build"] for name in app_services}
    assert {build["dockerfile"] for build in builds.values()} == {"Dockerfile"}
    assert {build["context"] for build in builds.values()} == {"."}
    assert (ROOT / "Dockerfile").is_file()

    # Every overlay app service runs one immutable release image from the same
    # single stack: no rebuild, no extra entrypoint override, demo labels on.
    for name in ("control", "execution", "research", "history", "backup"):
        cfg = overlay["services"][name]
        assert cfg.get("build") is None, name
        assert "QTS_RELEASE_IMAGE" in cfg["image"], name
        assert cfg["labels"]["com.qts.environment"] == "demo", name
        assert "command" not in cfg, name
    # The merged stack must not reference the modules phase 6 deleted.
    for needle in ("oms_callback", "core.application.oms"):
        assert needle not in compose_text
        assert needle not in overlay_text
        assert needle not in dockerfile_text
