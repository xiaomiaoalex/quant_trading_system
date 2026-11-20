"""Contract tests for the PR preview browser smoke entry point."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import preview_browser_smoke as smoke  # noqa: E402


def test_required_browser_checks_cover_login_and_read_only_state():
    assert smoke.REQUIRED_CHECKS == (
        "unauthenticated_deep_link_rejected",
        "wrong_password_rejected",
        "login_succeeded",
        "identity_visible",
        "protected_readonly_accessible",
        "blocked_state_visible",
    )


def test_non_loopback_origin_fails_closed(tmp_path):
    password = tmp_path / "password.txt"
    password.write_text("synthetic-password", encoding="utf-8")
    evidence = tmp_path / "browser.json"
    code = smoke.main(
        [
            "--origin",
            "https://example.com",
            "--password-file",
            str(password),
            "--evidence",
            str(evidence),
        ]
    )
    assert code == 1
    report = json.loads(evidence.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert "ORIGIN_NOT_LOOPBACK" in report["problems"]
    assert "synthetic-password" not in evidence.read_text(encoding="utf-8")


def test_missing_browser_runtime_fails_closed_without_fake_success(tmp_path, monkeypatch):
    monkeypatch.setattr(
        smoke,
        "browser_available",
        lambda: (False, "PLAYWRIGHT_UNAVAILABLE: synthetic test"),
    )
    password = tmp_path / "password.txt"
    password.write_text("synthetic-password", encoding="utf-8")
    evidence = tmp_path / "browser.json"
    code = smoke.main(
        [
            "--origin",
            "http://127.0.0.1:18080",
            "--password-file",
            str(password),
            "--evidence",
            str(evidence),
        ]
    )
    assert code == 1
    report = json.loads(evidence.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert any("PLAYWRIGHT_UNAVAILABLE" in item for item in report["problems"])
    assert report["checks"] == {}


def test_safe_origin_accepts_only_loopback_http():
    assert smoke._safe_origin("http://127.0.0.1:18080") == "http://127.0.0.1:18080"
    assert smoke._safe_origin("http://localhost:18080") == "http://localhost:18080"
    assert smoke._safe_origin("http://user:pass@127.0.0.1:18080") is None
    assert smoke._safe_origin("http://127.0.0.1:18080/path") is None
    assert smoke._safe_origin("http://0.0.0.0:18080") is None
    assert smoke._safe_origin("https://127.0.0.1:18080") is None


def test_default_browser_paths_match_the_current_frontend_routes():
    source = (ROOT / "Frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert 'path="/research/data"' in source
    assert 'path="/monitor"' in source

    smoke_source = (ROOT / "scripts" / "preview_browser_smoke.py").read_text(encoding="utf-8")
    assert 'parser.add_argument("--deep-link", default="/research/data")' in smoke_source
    assert 'parser.add_argument("--profile-path", default="/monitor")' in smoke_source
    assert 'parser.add_argument("--trading-path", default="/health/trading")' in smoke_source
    assert smoke.PROTECTED_READONLY_STATUSES == frozenset({200, 503})
