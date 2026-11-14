"""Regression tests for optional-group collection exclusion."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFTEST_PATH = ROOT / "trader" / "tests" / "conftest.py"


def _load_conftest():
    spec = importlib.util.spec_from_file_location("qts_tests_conftest_under_test", CONFTEST_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _configure(monkeypatch, group="", excluded="", subset="") -> object:
    monkeypatch.setenv("QTS_TEST_GROUP", group)
    monkeypatch.setenv("QTS_TEST_EXCLUDE_GROUPS", excluded)
    monkeypatch.setenv("QTS_TEST_SUBSET", subset)
    monkeypatch.setenv("QTS_TEST_NETWORK_POLICY", "offline")
    conftest = _load_conftest()
    config = SimpleNamespace()
    conftest.pytest_configure(config)
    return config


def test_external_exclusion_is_rejected_for_a_required_group(monkeypatch):
    with pytest.raises(pytest.UsageError, match="required CI group"):
        _configure(monkeypatch, excluded="p0")


def test_selected_and_excluded_groups_must_not_conflict(monkeypatch):
    with pytest.raises(pytest.UsageError, match="both select: external"):
        _configure(monkeypatch, group="external", excluded="external")


def test_unknown_excluded_group_is_a_usage_error(monkeypatch):
    with pytest.raises(pytest.UsageError, match="unknown QTS_TEST_EXCLUDE_GROUPS"):
        _configure(monkeypatch, excluded="missing-group")


def test_external_items_are_removed_without_importing_them(monkeypatch):
    config = _configure(monkeypatch, excluded="external")
    conftest = _load_conftest()
    external = ROOT / "trader" / "tests" / "test_vectorbt_risk_adapter.py"
    offline = ROOT / "trader" / "tests" / "test_cicd_ci_group_exclusion.py"
    external_item = SimpleNamespace(
        path=external,
        fspath=str(external),
        add_marker=lambda marker: pytest.fail("excluded item must not be marked"),
    )
    offline_item = SimpleNamespace(
        path=offline,
        fspath=str(offline),
        add_marker=lambda marker: None,
    )
    items = [external_item, offline_item]
    conftest.pytest_collection_modifyitems(config, items)
    assert items == [offline_item]


def test_collect_ignore_covers_external_modules_before_import(monkeypatch):
    monkeypatch.setenv("QTS_TEST_EXCLUDE_GROUPS", "external")
    monkeypatch.delenv("QTS_TEST_GROUP", raising=False)
    monkeypatch.delenv("QTS_TEST_SUBSET", raising=False)
    ignored = set(_load_conftest()._collect_ignore())
    assert {
        "test_backtesting_vectorbt_adapter.py",
        "test_vectorbt_risk_adapter.py",
    } <= ignored


def test_default_jobs_have_no_exclusions(monkeypatch):
    monkeypatch.delenv("QTS_TEST_EXCLUDE_GROUPS", raising=False)
    monkeypatch.delenv("QTS_TEST_GROUP", raising=False)
    monkeypatch.delenv("QTS_TEST_SUBSET", raising=False)
    assert _load_conftest()._collect_ignore() == []


def test_required_subset_members_cannot_be_excluded(monkeypatch):
    """The phase-6 guard test lives in ``ops`` but a required job runs it."""
    with pytest.raises(pytest.UsageError, match="required CI group"):
        _configure(monkeypatch, excluded="ops")


def test_selected_group_limits_collection_to_that_group(monkeypatch):
    monkeypatch.setenv("QTS_TEST_GROUP", "postgres")
    monkeypatch.delenv("QTS_TEST_EXCLUDE_GROUPS", raising=False)
    monkeypatch.delenv("QTS_TEST_SUBSET", raising=False)
    ignored = set(_load_conftest()._collect_ignore())
    assert "test_postgres_storage.py" not in ignored
    assert "test_event_driven_risk_replay.py" in ignored  # p0
    assert "test_phase6_startup_guard.py" in ignored  # ops
    assert "test_vectorbt_risk_adapter.py" in ignored  # external


def test_selected_subset_limits_collection_to_its_members(monkeypatch):
    monkeypatch.setenv("QTS_TEST_SUBSET", "phase6_guard")
    monkeypatch.delenv("QTS_TEST_GROUP", raising=False)
    monkeypatch.delenv("QTS_TEST_EXCLUDE_GROUPS", raising=False)
    ignored = set(_load_conftest()._collect_ignore())
    assert "test_phase6_startup_guard.py" not in ignored
    assert "test_ai_strategy_generator.py" in ignored
