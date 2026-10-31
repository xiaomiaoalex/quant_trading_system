"""Offline acceptance gates for reviewable sandbox observation evidence."""

import json
import sys

import pytest
from scripts.summarize_observation import main


def samples():
    return [
        {"ts_ms": minute * 60_000, "health": {"body": {"ready": True}}, "metrics": {}}
        for minute in range(61)
    ]


def markers():
    return [
        {"ts_ms": 120_000, "marker": "runtime_restart", "detail": "restart evidence"},
        {"ts_ms": 180_000, "marker": "active_reconnect", "detail": "reconnect evidence"},
    ]


def summarize(tmp_path, monkeypatch, records, extra=()):
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


@pytest.mark.parametrize("missing", ["runtime_restart", "active_reconnect"])
def test_missing_required_drill_marker_blocks_delivery(tmp_path, monkeypatch, missing):
    code, report = summarize(
        tmp_path, monkeypatch, samples() + [m for m in markers() if m["marker"] != missing]
    )
    assert code == 1
    assert f"MISSING_MARKER:{missing}" in report["blockers"]


def test_complete_continuous_window_with_drills_passes(tmp_path, monkeypatch):
    code, report = summarize(tmp_path, monkeypatch, samples() + markers())
    assert code == 0 and report["ok"]
    assert report["max_sample_gap_ms"] == 60_000


@pytest.mark.parametrize("bad_line", ["{malformed", "[]", '{"ts_ms": "invalid"}'])
def test_invalid_evidence_lines_cannot_be_ignored(tmp_path, monkeypatch, bad_line):
    code, report = summarize(tmp_path, monkeypatch, samples() + markers() + [bad_line])
    assert code == 1
    assert report["parse_errors"] == 1
    assert "INVALID_EVIDENCE_LINES" in report["blockers"]


def test_long_sample_gap_blocks_two_endpoints_faking_a_window(tmp_path, monkeypatch):
    code, report = summarize(tmp_path, monkeypatch, [samples()[0], samples()[-1]] + markers())
    assert code == 1
    assert "SAMPLE_GAP_EXCEEDED" in report["blockers"]


def test_gap_tolerance_is_explicit_and_configurable(tmp_path, monkeypatch):
    records = samples()[::5] + markers()
    code, report = summarize(tmp_path, monkeypatch, records, ("--max-gap-seconds", "300"))
    assert code == 0
    assert report["max_sample_gap_ms"] == 300_000


def test_markers_outside_observed_window_do_not_count(tmp_path, monkeypatch):
    drills = markers()
    drills[0]["ts_ms"] = 4_000_000
    code, report = summarize(tmp_path, monkeypatch, samples() + drills)
    assert code == 1
    assert "MISSING_MARKER:runtime_restart" in report["blockers"]


def test_completed_restart_marker_pair_satisfies_the_drill_gate(tmp_path, monkeypatch):
    drills = [
        {"ts_ms": 120_000, "marker": "runtime_restart_start", "detail": "killed pid 1"},
        {"ts_ms": 150_000, "marker": "runtime_restart_end", "detail": "READY again"},
        {"ts_ms": 180_000, "marker": "active_reconnect", "detail": "reconnect evidence"},
    ]
    code, report = summarize(tmp_path, monkeypatch, samples() + drills)
    assert code == 0 and report["ok"]


def test_restart_start_without_a_completed_restart_still_blocks(tmp_path, monkeypatch):
    drills = [
        {"ts_ms": 120_000, "marker": "runtime_restart_start", "detail": "killed pid 1"},
        {"ts_ms": 180_000, "marker": "active_reconnect", "detail": "reconnect evidence"},
    ]
    code, report = summarize(tmp_path, monkeypatch, samples() + drills)
    assert code == 1
    assert "MISSING_MARKER:runtime_restart" in report["blockers"]


def test_out_of_order_sample_timestamps_block_delivery(tmp_path, monkeypatch):
    records = samples()
    records[1], records[2] = records[2], records[1]
    code, report = summarize(tmp_path, monkeypatch, records + markers())
    assert code == 1
    assert "NON_MONOTONIC_SAMPLES" in report["blockers"]
