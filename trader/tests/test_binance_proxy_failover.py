from __future__ import annotations

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey

from trader.adapters.binance.proxy_failover import (
    ProxyFailoverConfig,
    ProxyFailoverController,
    is_proxy_failure,
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


def test_a_proxy_that_keeps_refusing_connections_is_quarantined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead backup must not be retried every cooldown cycle."""
    _set_primary_backup(monkeypatch)
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "trader.adapters.binance.proxy_failover.time.time",
        lambda: clock["now"],
    )
    controller = ProxyFailoverController(
        ProxyFailoverConfig(failure_threshold=1, cooldown_seconds=30.0)
    )
    backup = "http://127.0.0.1:10808"

    controller.report_failure(backup, hard=True)
    controller.report_failure(backup, hard=True)

    # 隔离期内即使冷却结束也不再选择它
    clock["now"] = 1031.0
    assert controller.select_proxy() != backup


def test_quarantine_never_returns_no_proxy_while_a_candidate_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when everything is quarantined the request must still use a proxy."""
    _set_primary_backup(monkeypatch)
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "trader.adapters.binance.proxy_failover.time.time",
        lambda: clock["now"],
    )
    controller = ProxyFailoverController(
        ProxyFailoverConfig(failure_threshold=1, cooldown_seconds=30.0)
    )
    for proxy in ("http://127.0.0.1:7890", "http://127.0.0.1:10808"):
        controller.report_failure(proxy, hard=True)
        controller.report_failure(proxy, hard=True)

    assert controller.select_proxy() is not None


def test_soft_failures_never_quarantine_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A target-side failure must not condemn the proxy."""
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

    controller.report_failure(primary, hard=False)
    controller.report_failure(primary, hard=False)

    clock["now"] = 1031.0
    assert controller.select_proxy() == primary


def _proxy_connection_key() -> ConnectionKey:
    return ConnectionKey(
        host="127.0.0.1",
        port=10808,
        is_ssl=False,
        ssl=False,
        proxy=None,
        proxy_auth=None,
        proxy_headers_hash=None,
    )


def _target_connection_error() -> aiohttp.ClientConnectorError:
    key = ConnectionKey(
        host="demo-api.binance.com",
        port=443,
        is_ssl=True,
        ssl=False,
        proxy=None,
        proxy_auth=None,
        proxy_headers_hash=None,
    )
    return aiohttp.ClientConnectorError(key, ConnectionRefusedError(22, "connection refused"))


def test_a_refused_local_proxy_is_a_proxy_failure() -> None:
    """The proxy process is down: the failure belongs to the proxy, not the target."""
    refused = ConnectionRefusedError(22, "connection refused")

    proxy_error = aiohttp.ClientProxyConnectionError(_proxy_connection_key(), refused)
    assert is_proxy_failure(proxy_error, "http://127.0.0.1:10808")
    assert is_proxy_failure(refused, "http://127.0.0.1:10808")
    assert is_proxy_failure(refused, "http://localhost:10808")


def test_a_target_failure_is_not_a_proxy_failure() -> None:
    """The proxy answered; the venue is unreachable. The proxy stays healthy."""
    refused = ConnectionRefusedError(22, "connection refused")

    assert not is_proxy_failure(_target_connection_error(), "http://127.0.0.1:7890")
    assert not is_proxy_failure(refused, "http://proxy.example.com:8080")
    assert not is_proxy_failure(refused, None)


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
