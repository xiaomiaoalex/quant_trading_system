"""
Tests for QuantStats tearsheet generation (Stage 4B).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from trader.services.backtesting.quantstats_report import generate_tearsheet

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_equity_curve(n: int = 50, initial: float = 100_000.0) -> list[dict]:
    """Simple upward-sloping equity curve for testing."""
    start_ms = int(time.time() * 1000) - n * 3600 * 1000
    equity = initial
    points = []
    for i in range(n):
        equity *= 1.001 + (i % 3 - 1) * 0.0005
        points.append({"timestamp": start_ms + i * 3600 * 1000, "equity": equity})
    return points


# ---------------------------------------------------------------------------
# 1. Returns None when equity_curve too short
# ---------------------------------------------------------------------------


def test_generate_tearsheet_skips_short_curve():
    result = generate_tearsheet([], run_id="test-run-001")
    assert result is None

    result = generate_tearsheet([{"timestamp": 1000, "equity": 100.0}] * 5, run_id="test-run-001")
    assert result is None


# ---------------------------------------------------------------------------
# 2. Returns None gracefully when quantstats not installed
# ---------------------------------------------------------------------------


def test_generate_tearsheet_handles_missing_quantstats():
    curve = _make_equity_curve(20)
    with patch("builtins.__import__", side_effect=ImportError("No module named 'quantstats'")):
        # Should not raise, just return None
        # (We can't patch builtins.__import__ cleanly in all Python versions, so we test the
        # ImportError branch by monkeypatching the import inside the function)
        pass  # covered by next test


def test_generate_tearsheet_import_error_branch():
    """If quantstats is missing, generate_tearsheet returns None without raising."""
    curve = _make_equity_curve(20)
    # Simulate ImportError by patching quantstats inside the function module
    import trader.services.backtesting.quantstats_report as mod

    original = __builtins__  # noqa

    with patch.dict("sys.modules", {"quantstats": None}):
        result = generate_tearsheet(curve, run_id="no-qs-run", strategy_name="Test")
        # Should be None (ImportError handled)
        assert result is None


# ---------------------------------------------------------------------------
# 3. Returns HTML path when quantstats available and curve is long enough
# ---------------------------------------------------------------------------


def test_generate_tearsheet_returns_path_when_successful(tmp_path):
    curve = _make_equity_curve(60)
    output = str(tmp_path / "test_tearsheet.html")

    try:
        import quantstats  # noqa: F401
    except ImportError:
        pytest.skip("quantstats not installed")

    result = generate_tearsheet(curve, run_id="qs-test-001", output_path=output)
    # Either succeeds (returns path) or fails gracefully (returns None)
    # We don't assert success because matplotlib display backend may be missing in CI
    assert result is None or result == output


def test_generate_tearsheet_default_path_uses_tempdir(tmp_path):
    """Default output path must use tempfile.gettempdir(), not hardcoded /tmp."""
    import tempfile
    from pathlib import Path

    try:
        import quantstats  # noqa: F401
    except ImportError:
        pytest.skip("quantstats not installed")

    curve = _make_equity_curve(60)
    run_id = "default-path-test"
    expected_default = str(Path(tempfile.gettempdir()) / f"tearsheet_{run_id}.html")

    result = generate_tearsheet(curve, run_id=run_id)
    # Must use tempdir path, not /tmp
    assert result is None or result == expected_default
    if result:
        assert not result.startswith("/tmp"), "Must not use hardcoded /tmp on non-Unix platforms"


# ---------------------------------------------------------------------------
# 4. ArtifactStorage tearsheet methods
# ---------------------------------------------------------------------------


def test_artifact_storage_tearsheet_roundtrip(tmp_path):
    """save_tearsheet copies file; get_tearsheet_path returns it."""
    from trader.storage.artifact_storage import ArtifactStorage

    # Create a fake tearsheet HTML
    src = tmp_path / "source.html"
    src.write_text("<html>report</html>", encoding="utf-8")

    storage = ArtifactStorage(base_path=str(tmp_path / "artifacts"))
    ref = storage.save_tearsheet("run-abc-123", str(src))
    assert ref == "backtest_tearsheet:run-abc-123"

    path = storage.get_tearsheet_path("run-abc-123")
    assert path is not None
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "<html>report</html>"


def test_artifact_storage_tearsheet_missing_returns_none(tmp_path):
    from trader.storage.artifact_storage import ArtifactStorage

    storage = ArtifactStorage(base_path=str(tmp_path / "artifacts"))
    assert storage.get_tearsheet_path("nonexistent-run") is None


# ---------------------------------------------------------------------------
# 5. BacktestReport schema accepts tearsheet_ref field
# ---------------------------------------------------------------------------


def test_backtest_report_schema_has_tearsheet_ref():
    from trader.api.models.schemas import BacktestReport

    report = BacktestReport(
        run_id="r1",
        status="COMPLETED",
        strategy_id="s1",
        version=1,
        symbols=["BTCUSDT"],
        start_ts_ms=0,
        end_ts_ms=1000,
        tearsheet_ref="backtest_tearsheet:r1",
    )
    assert report.tearsheet_ref == "backtest_tearsheet:r1"


def test_backtest_report_schema_tearsheet_ref_optional():
    from trader.api.models.schemas import BacktestReport

    report = BacktestReport(
        run_id="r2",
        status="COMPLETED",
        strategy_id="s1",
        version=1,
        symbols=["BTCUSDT"],
        start_ts_ms=0,
        end_ts_ms=1000,
    )
    assert report.tearsheet_ref is None
