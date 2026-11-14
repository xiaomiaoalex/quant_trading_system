"""Engineer tests for the parent-identified window gate and DR recovery defects.

Covers P6-PARENT-RPO-WINDOW-BOUNDARIES and
P6-PARENT-S3-RECONNECT-IDEMPOTENCY. These are engineer-owned implementation
tests; the independent tester owns its own counterexamples.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trader.ops.backup import summarize_rpo
from trader.ops.s3 import S3Config

ROOT = Path(__file__).resolve().parents[2]
START = 1_793_854_800_000
DAY = 86_400_000


def _script_module(name: str):
    spec = importlib.util.spec_from_file_location(
        f"engineer_phase6_{name}", ROOT / "scripts" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _journal(path: Path, completions: list[int]) -> Path:
    path.write_text(
        "\n".join(
            json.dumps({"status": "succeeded", "backup": {"completed_at_ms": ms}})
            for ms in completions
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _window_rpo(path: Path, start: int, end: int, rpo_seconds: int = 300):
    return summarize_rpo(path, rpo_seconds, window_start_ms=start, window_end_ms=end)


# ---------------------------------------------------------------- RPO boundaries


def test_rpo_uses_milliseconds_not_floored_seconds(tmp_path: Path) -> None:
    journal = _journal(tmp_path / "j.jsonl", [START, START + 300_000])
    assert summarize_rpo(journal, 300)["rpo_satisfied"] is True
    journal = _journal(tmp_path / "j2.jsonl", [START, START + 300_001])
    result = summarize_rpo(journal, 300)
    assert result["rpo_satisfied"] is False
    assert result["max_gap_ms"] == 300_001


def test_window_requires_a_fresh_backup_at_or_before_start(tmp_path: Path) -> None:
    fresh = _journal(tmp_path / "fresh.jsonl", [START - 299_000, START + 180_000])
    assert _window_rpo(fresh, START, START + DAY)["head_covered"] is True
    stale = _journal(tmp_path / "stale.jsonl", [START - 301_000, START + 180_000])
    result = _window_rpo(stale, START, START + DAY)
    assert result["head_covered"] is False
    assert result["rpo_satisfied"] is False


def test_window_rejects_missing_tail_even_with_fresh_head(tmp_path: Path) -> None:
    journal = _journal(
        tmp_path / "head-only.jsonl",
        [START, START + 180_000, START + 360_000, START + 540_000],
    )
    result = _window_rpo(journal, START, START + DAY)
    assert result["head_covered"] is True
    assert result["tail_covered"] is False
    assert result["rpo_satisfied"] is False
    assert result["tail_age_ms"] == DAY - 540_000


def test_window_future_backups_cannot_cover_the_missing_head(tmp_path: Path) -> None:
    journal = _journal(
        tmp_path / "tail-only.jsonl",
        [START + DAY - n * 180_000 for n in range(4)],
    )
    result = _window_rpo(journal, START, START + DAY)
    assert result["head_covered"] is False
    assert result["rpo_satisfied"] is False


def test_window_internal_gap_fails(tmp_path: Path) -> None:
    completions = [START - 60_000, START + 60_000]
    completions += [START + 60_000 + 180_000 * n for n in range(1, 200)]
    # insert a two-hour hole
    completions = [c for c in completions if not (START + 3_600_000 < c < START + 10_800_000)]
    completions.append(START + 3_601_000)
    completions.append(START + 10_799_000)
    journal = _journal(tmp_path / "gap.jsonl", completions)
    result = _window_rpo(journal, START, START + DAY)
    assert result["rpo_satisfied"] is False
    assert max(result["gaps_ms"]) > 300_000


def test_window_full_coverage_control_passes(tmp_path: Path) -> None:
    completions = [START - 120_000]
    completions += list(range(START, START + DAY + 1, 180_000))
    journal = _journal(tmp_path / "full.jsonl", completions)
    result = _window_rpo(journal, START, START + DAY)
    assert result["head_covered"] is True
    assert result["tail_covered"] is True
    assert result["rpo_satisfied"] is True


# ------------------------------------------------------------- supervisor verdict


def _supervisor(tmp_path: Path, backups: list[int]):
    module = _script_module("sandbox_supervisor")
    tmp_path.mkdir(parents=True, exist_ok=True)
    observation = tmp_path / "observation.jsonl"
    rows = [{"kind": "marker", "ts_ms": START, "marker": "observer_started"}]
    for index in range(2881):
        rows.append(
            {
                "kind": "sample",
                "ts_ms": START + index * 30_000,
                "sample_gap_ms": 30_000,
                "ready": True,
                "health": {"status_code": 200, "body": {"reasons": []}},
                "metrics": {
                    "unresolved": [],
                    "unresolved_entries": [],
                    "coverage": {
                        "contiguous": True,
                        "recorded_at_ms": START + index * 30_000,
                    },
                    "evidence": {
                        "executions": {"count": 1},
                        "send_outcomes": {"repeated_send_commands": []},
                    },
                },
            }
        )
    rows.append({"kind": "marker", "ts_ms": START + DAY, "marker": "observer_finished"})
    observation.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    _journal(backup_dir / "backup-journal.jsonl", backups)
    supervisor = object.__new__(module.SandboxSupervisor)
    supervisor.observation_path = observation
    supervisor.backup_dir = backup_dir
    supervisor.dr_evidence_path = tmp_path / "dr-evidence.json"
    supervisor.dr_journal_path = tmp_path / "dr-cycles.jsonl"
    supervisor.feeder = SimpleNamespace(
        state={"cycles": len(backups), "successes": len(backups), "failures": 0}
    )
    supervisor.start_ms = START
    supervisor.aborted_reason = ""
    supervisor.run_id = "engineer-window-gates"
    supervisor.project = "qts-engineer-window-gates"
    supervisor.args = SimpleNamespace(duration_hours=24, sample_seconds=30.0)
    return supervisor


def test_supervisor_uses_window_bound_rpo(tmp_path: Path) -> None:
    head_only = _supervisor(tmp_path / "head", [START + 180_000 * n for n in range(4)])
    summary = head_only._summarize()
    assert summary["criteria"]["dr_backups_fresh"] is False
    assert summary["criteria"]["no_persistent_reasons"] is True

    full = START - 120_000
    complete = _supervisor(tmp_path / "full", [full] + list(range(START, START + DAY + 1, 180_000)))
    summary = complete._summarize()
    assert summary["criteria"]["dr_backups_fresh"] is True


def _window_with_reason(tmp_path: Path, reason: str, samples_active: range):
    supervisor = _supervisor(
        tmp_path, [START - 120_000] + list(range(START, START + DAY + 1, 180_000))
    )
    lines = supervisor.observation_path.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    for index, row in enumerate(rows):
        if row.get("kind") != "sample":
            continue
        if index in samples_active:
            row["ready"] = False
            row["health"] = {"status_code": 503, "body": {"reasons": [reason]}}
    supervisor.observation_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return supervisor


def test_recovery_generation_reason_uses_the_recovery_bound(tmp_path: Path) -> None:
    supervisor = _window_with_reason(tmp_path, "RECOVERY_GENERATION_OR_OVERFLOW", range(10, 17))
    summary = supervisor._summarize()
    assert summary["criteria"]["no_persistent_reasons"] is True


def test_dr_backup_stale_keeps_the_strict_bound(tmp_path: Path) -> None:
    supervisor = _window_with_reason(tmp_path, "DR_BACKUP_STALE", range(10, 17))
    summary = supervisor._summarize()
    assert summary["criteria"]["no_persistent_reasons"] is False


# ---------------------------------------------------- S3 reconnect idempotency


class _FakeS3Exceptions:
    class BucketAlreadyOwnedByYou(Exception):
        pass

    class BucketAlreadyExists(Exception):
        pass


class _FakeS3:
    exceptions = _FakeS3Exceptions

    def __init__(self, create_error=None, head_error=None):
        self.create_error = create_error
        self.head_error = head_error
        self.head_calls = 0

    def create_bucket(self, Bucket):
        if self.create_error is not None:
            raise self.create_error
        return {}

    def head_bucket(self, Bucket):
        self.head_calls += 1
        if self.head_error is not None:
            raise self.head_error
        return {}

    def list_objects_v2(self, **kwargs):
        return {"KeyCount": 0}


def _config(
    bucket: str = "qts-20261105T000000Z-abcdef12", endpoint: str = "http://127.0.0.1:19002"
):
    return S3Config(
        endpoint_url=endpoint,
        bucket=bucket,
        prefix="phase6/run-1",
        access_key="access",
        secret_key="secret",
        region="us-east-1",
    )


def test_create_bucket_accepts_a_verified_existing_task_bucket(monkeypatch) -> None:
    module = _script_module("sandbox_supervisor")
    client = _FakeS3(create_error=_FakeS3Exceptions.BucketAlreadyExists("exists"))
    monkeypatch.setattr(module, "_s3_client", lambda config: client)
    module._create_bucket(_config())
    assert client.head_calls == 1


def test_create_bucket_rejects_an_inaccessible_existing_bucket(monkeypatch) -> None:
    module = _script_module("sandbox_supervisor")
    client = _FakeS3(
        create_error=_FakeS3Exceptions.BucketAlreadyExists("exists"),
        head_error=PermissionError("403 AccessDenied"),
    )
    monkeypatch.setattr(module, "_s3_client", lambda config: client)
    with pytest.raises(RuntimeError, match="BUCKET_EXISTING_BUT_NOT_ACCESSIBLE"):
        module._create_bucket(_config())


def test_create_bucket_rejects_a_foreign_target(monkeypatch) -> None:
    module = _script_module("sandbox_supervisor")
    client = _FakeS3(create_error=_FakeS3Exceptions.BucketAlreadyExists("exists"))
    monkeypatch.setattr(module, "_s3_client", lambda config: client)
    with pytest.raises(RuntimeError, match="S3_BUCKET_NOT_TASK_TARGET"):
        module._create_bucket(_config(bucket="someone-else"))
    with pytest.raises(RuntimeError, match="S3_ENDPOINT_NOT_TASK_LOOPBACK"):
        module._create_bucket(_config(endpoint="http://example.com:19002"))


def test_create_bucket_does_not_swallow_authorization_errors(monkeypatch) -> None:
    module = _script_module("sandbox_supervisor")
    client = _FakeS3(create_error=PermissionError("AccessDenied"))
    monkeypatch.setattr(module, "_s3_client", lambda config: client)
    with pytest.raises(PermissionError):
        module._create_bucket(_config())


class _FakeTunnel:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def _tunnel_supervisor(tmp_path: Path, tunnel: _FakeTunnel):
    module = _script_module("sandbox_supervisor")
    supervisor = object.__new__(module.SandboxSupervisor)
    supervisor.tunnel = tunnel
    supervisor.run_id = "engineer-tunnel"
    supervisor.supervisor_journal = tmp_path / "supervisor.jsonl"
    supervisor.context = {
        "ssh_destination": "alex@192.168.3.4",
        "remote_port": 19002,
        "bucket": "qts-20261105T000000Z-abcdef12",
        "access_key": "access",
        "secret_key": "secret",
        "s3_config": _config(),
    }
    return module, supervisor


def test_ensure_tunnel_rebuilds_after_a_failed_application_probe(
    tmp_path: Path, monkeypatch
) -> None:
    module, supervisor = _tunnel_supervisor(tmp_path, _FakeTunnel())
    started: list[int] = []

    def fake_start(destination, remote_port, **kwargs):
        started.append(remote_port)
        return _FakeTunnel(), 12345

    probes = iter([RuntimeError("S3_READBACK_FAILED"), None])

    def fake_probe(config):
        value = next(probes, None)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(module, "_start_tunnel", fake_start)
    monkeypatch.setattr(module, "_create_bucket", lambda config: None)
    monkeypatch.setattr(module, "_probe_bucket", fake_probe)
    asyncio.run(supervisor._ensure_tunnel())
    assert started == [19002]
    journal = (tmp_path / "supervisor.jsonl").read_text(encoding="utf-8")
    assert "APPLICATION_PROBE_FAILED" in journal
    assert supervisor.tunnel is not None


def test_ensure_tunnel_reuses_a_live_application_healthy_tunnel(
    tmp_path: Path, monkeypatch
) -> None:
    tunnel = _FakeTunnel()
    module, supervisor = _tunnel_supervisor(tmp_path, tunnel)
    started: list[int] = []
    monkeypatch.setattr(
        module,
        "_start_tunnel",
        lambda destination, port, **kwargs: (started.append(port), (_FakeTunnel(), 1))[1],
    )
    monkeypatch.setattr(module, "_probe_bucket", lambda config: None)
    asyncio.run(supervisor._ensure_tunnel())
    assert started == []
    assert tunnel.terminated is False


def test_sanitize_diagnostic_redacts_credentials() -> None:
    module = _script_module("sandbox_supervisor")
    cleaned = module._sanitize_diagnostic(
        "ssh: secret=abc123 password=hunter2 host-key-ok", secrets=("abc123",)
    )
    assert "abc123" not in cleaned
    assert "hunter2" not in cleaned
    assert "<redacted>" in cleaned


def _metrics_gap_window(tmp_path: Path, gap_from_index: int):
    """A dense 24h window whose tail has no metrics body (401 samples)."""
    supervisor = _supervisor(
        tmp_path, [START - 120_000] + list(range(START, START + DAY + 1, 180_000))
    )
    rows = [
        json.loads(line)
        for line in supervisor.observation_path.read_text(encoding="utf-8").splitlines()
    ]
    sample_index = 0
    for row in rows:
        if row.get("kind") != "sample":
            continue
        if sample_index >= gap_from_index:
            row["metrics_status_code"] = 401
            row.pop("metrics", None)
        sample_index += 1
    supervisor.observation_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return supervisor


def test_a_window_whose_tail_lost_metrics_cannot_pass(tmp_path: Path) -> None:
    """A missing metrics body is an evidence gap, never "nothing unresolved"."""
    supervisor = _metrics_gap_window(tmp_path, gap_from_index=1440)
    summary = supervisor._summarize()
    criteria = summary["criteria"]
    assert summary["metrics_missing"] > 0
    assert criteria["metrics_samples_present"] is False
    assert criteria["metrics_gap_within_bound"] is False
    assert criteria["coverage_fresh_at_end"] is False
    assert criteria["unresolved_cleared_at_end"] is False
    assert summary["passed"] is False


def test_a_fully_evidenced_window_still_passes(tmp_path: Path) -> None:
    supervisor = _supervisor(
        tmp_path, [START - 120_000] + list(range(START, START + DAY + 1, 180_000))
    )
    summary = supervisor._summarize()
    criteria = summary["criteria"]
    assert summary["metrics_missing"] == 0
    assert criteria["metrics_samples_present"] is True
    assert criteria["metrics_gap_within_bound"] is True
    assert criteria["coverage_fresh_at_end"] is True
    assert criteria["unresolved_cleared_at_end"] is True
    assert summary["passed"] is True


def _coverage_age_window(tmp_path: Path, age_ms: int):
    supervisor = _supervisor(
        tmp_path, [START - 120_000] + list(range(START, START + DAY + 1, 180_000))
    )
    rows = [
        json.loads(line)
        for line in supervisor.observation_path.read_text(encoding="utf-8").splitlines()
    ]
    samples = [row for row in rows if row.get("kind") == "sample"]
    last = samples[-1]
    last["metrics"]["coverage"]["recorded_at_ms"] = int(last["ts_ms"]) - age_ms
    supervisor.observation_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return supervisor


def test_coverage_freshness_tolerates_one_reconcile_cycle(tmp_path: Path) -> None:
    """The coverage summary refreshes every reconcile interval, so a 300s
    bound would reject a healthy window; the bound must cover a skipped
    reconcile plus the sampling slack."""
    supervisor = _coverage_age_window(tmp_path, 400_000)
    summary = supervisor._summarize()
    assert summary["coverage_fresh_limit_ms"] >= 780_000
    assert summary["criteria"]["coverage_fresh_at_end"] is True


def test_clearly_stale_coverage_at_the_end_still_fails(tmp_path: Path) -> None:
    supervisor = _coverage_age_window(tmp_path, 900_000)
    summary = supervisor._summarize()
    assert summary["criteria"]["coverage_fresh_at_end"] is False
    assert summary["passed"] is False


def test_missing_coverage_timestamp_at_the_end_fails_closed(tmp_path: Path) -> None:
    """A coverage summary without recorded_at_ms must not be treated as fresh."""
    supervisor = _supervisor(
        tmp_path, [START - 120_000] + list(range(START, START + DAY + 1, 180_000))
    )
    rows = [
        json.loads(line)
        for line in supervisor.observation_path.read_text(encoding="utf-8").splitlines()
    ]
    samples = [row for row in rows if row.get("kind") == "sample"]
    samples[-1]["metrics"]["coverage"].pop("recorded_at_ms", None)
    supervisor.observation_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    summary = supervisor._summarize()
    assert summary["coverage_missing_recorded_at_ms"] >= 1
    assert summary["criteria"]["coverage_fresh_at_end"] is False
    assert summary["passed"] is False


def test_duration_uses_the_monotonic_marker_origin(tmp_path: Path) -> None:
    """A wall-clock slew must not shave seconds off a full 24h observation.

    The observer's monotonic clock is the authority for the elapsed window;
    the wall-clock start/end markers can differ by seconds after a 24h run.
    """
    supervisor = _supervisor(
        tmp_path, [START - 120_000] + list(range(START, START + DAY + 1, 180_000))
    )
    rows = [
        json.loads(line)
        for line in supervisor.observation_path.read_text(encoding="utf-8").splitlines()
    ]
    markers = [row for row in rows if row.get("kind") == "marker"]
    markers[0]["monotonic_ms"] = 1_000_000
    markers[1]["monotonic_ms"] = 1_000_000 + DAY
    # the wall clock slewed 2s backwards over the day
    markers[1]["ts_ms"] = START + DAY - 2_000
    supervisor.observation_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    summary = supervisor._summarize()
    assert summary["duration_seconds"] == DAY // 1000
    assert summary["criteria"]["duration_at_least_24h"] is True


def test_reconciliation_reason_uses_the_recovery_bound(tmp_path: Path) -> None:
    """UNBOOKED_EXECUTIONS can appear as a readiness reason while a fill waits
    for the next reconcile; it shares the 30-minute reconciliation bound."""
    supervisor = _window_with_reason(tmp_path, "UNBOOKED_EXECUTIONS", range(10, 30))
    summary = supervisor._summarize()
    assert summary["reason_max_duration_ms"]["UNBOOKED_EXECUTIONS"] >= 300_000
    assert summary["criteria"]["no_persistent_reasons"] is True
