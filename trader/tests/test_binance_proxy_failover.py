from __future__ import annotations

import pytest

from trader.adapters.binance.proxy_failover import (
    ProxyFailoverConfig,
    ProxyFailoverController,
)


def _set_primary_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINANCE_PROXY_URL", "http://127.0.0.1:7890")
    monkeypatch.setenv("BINANCE_BACKUP_PROXY_URL", "http://127.0.0.1:10808")


def test_select_proxy_prefers_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_primary_backup(monkeypatch)
    controller = ProxyFailoverController(
        ProxyFailoverConfig(failure_threshold=2, cooldown_seconds=30.0)
    )

    selected = controller.select_proxy()

    assert selected == "http://127.0.0.1:7890"


def test_switch_to_backup_after_threshold_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_primary_backup(monkeypatch)
    now = 1000.0
    monkeypatch.setattr(
        "trader.adapters.binance.proxy_failover.time.time",
        lambda: now,
    )
    controller = ProxyFailoverController(
        ProxyFailoverConfig(failure_threshold=2, cooldown_seconds=30.0)
    )

    primary = controller.select_proxy()
    assert primary == "http://127.0.0.1:7890"

    controller.report_failure(primary)
    # 未达到阈值，仍使用主代理
    assert controller.select_proxy() == "http://127.0.0.1:7890"

    controller.report_failure(primary)
    # 达到阈值后切备代理
    assert controller.select_proxy() == "http://127.0.0.1:10808"


def test_primary_recovers_after_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_primary_backup(monkeypatch)
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "trader.adapters.binance.proxy_failover.time.time",
        lambda: clock["now"],
    )
    controller = ProxyFailoverController(
        ProxyFailoverConfig(failure_threshold=1, cooldown_seconds=30.0)
    )

    primary = controller.select_proxy()
    controller.report_failure(primary)
    assert controller.select_proxy() == "http://127.0.0.1:10808"

    # 冷却期结束后应恢复主代理优先
    clock["now"] = 1031.0
    assert controller.select_proxy() == "http://127.0.0.1:7890"


def test_explicit_proxy_can_fallback_to_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_primary_backup(monkeypatch)
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "trader.adapters.binance.proxy_failover.time.time",
        lambda: clock["now"],
    )
    controller = ProxyFailoverController(
        ProxyFailoverConfig(failure_threshold=1, cooldown_seconds=60.0)
    )

    explicit = "http://127.0.0.1:9999"
    selected = controller.select_proxy(explicit_proxy=explicit)
    assert selected == explicit

    controller.report_failure(explicit)
    # explicit 进入冷却后，回退到环境主代理
    assert controller.select_proxy(explicit_proxy=explicit) == "http://127.0.0.1:7890"
