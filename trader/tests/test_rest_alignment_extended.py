"""
Extended Coverage Tests for REST Alignment
==========================================
增加 rest_alignment 模块的测试覆盖率
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from trader.adapters.binance.proxy_failover import (
    get_proxy_failover_controller,
    reset_proxy_failover_controller,
)
from trader.adapters.binance.rate_limit import Priority
from trader.adapters.binance.rest_alignment import (
    AlignmentConfig,
    AlignmentMetrics,
    RESTAlignmentCoordinator,
)


class _FakeResponse:
    def __init__(self, status: int = 200, payload: dict | None = None) -> None:
        self.status = status
        self._payload = payload or {}
        self.headers: dict[str, str] = {}

    async def json(self) -> dict:
        return self._payload

    async def text(self) -> str:
        return str(self._payload)


class _FakeRequestContext:
    def __init__(self, response: _FakeResponse | None = None, exc: Exception | None = None) -> None:
        self._response = response
        self._exc = exc

    async def __aenter__(self) -> _FakeResponse:
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FailoverSession:
    def __init__(self, bad_proxy: str, payload: dict) -> None:
        self._bad_proxy = bad_proxy
        self._payload = payload
        self.proxies: list[str | None] = []

    def get(self, url, *, proxy=None, timeout=None):
        del url, timeout
        self.proxies.append(proxy)
        if proxy == self._bad_proxy:
            return _FakeRequestContext(exc=aiohttp.ClientConnectionError("bad proxy"))
        return _FakeRequestContext(response=_FakeResponse(payload=self._payload))


class TestRESTAlignmentExtended:
    """扩展的 REST Alignment 测试"""

    def test_alignment_config_defaults(self):
        """测试默认配置"""
        config = AlignmentConfig()

        assert config.base_url == "https://testnet.binance.vision/api"
        assert config.p0_interval_seconds == 60.0
        assert config.p1_interval_seconds == 120.0
        assert config.p2_interval_seconds == 300.0

    def test_alignment_config_explicit(self):
        """测试显式配置"""
        config = AlignmentConfig(
            base_url="https://api.binance.com/api",
            p0_interval_seconds=30.0,
            min_alignment_interval=15.0,
        )

        assert config.base_url == "https://api.binance.com/api"
        assert config.p0_interval_seconds == 30.0
        assert config.min_alignment_interval == 15.0

    def test_alignment_metrics_defaults(self):
        """测试默认指标"""
        metrics = AlignmentMetrics()

        assert metrics.total_alignments == 0
        assert metrics.successful_alignments == 0
        assert metrics.failed_alignments == 0

    @pytest.mark.asyncio
    async def test_coordinator_initialization(self):
        """测试协调器初始化"""
        coordinator = RESTAlignmentCoordinator(api_key="test_key", secret_key="test_secret")

        assert coordinator._running is False
        assert coordinator._api_key == "test_key"
        assert coordinator._secret_key == "test_secret"

    @pytest.mark.asyncio
    async def test_start(self):
        """测试启动"""
        coordinator = RESTAlignmentCoordinator(api_key="test_key", secret_key="test_secret")

        await coordinator.start()

        assert coordinator._running is True
        assert coordinator._session is not None

    @pytest.mark.asyncio
    async def test_stop_not_running(self):
        """测试停止 - 未运行"""
        coordinator = RESTAlignmentCoordinator(api_key="test_key", secret_key="test_secret")

        coordinator._running = False

        await coordinator.stop()

        assert coordinator._running is False

    @pytest.mark.asyncio
    async def test_get_server_time_retries_backup_proxy(self, monkeypatch):
        """get_server_time should fail over from primary proxy to backup proxy."""
        bad_proxy = "http://bad-proxy:10808"
        good_proxy = "http://good-proxy:7890"
        monkeypatch.setenv("BINANCE_PROXY_URL", bad_proxy)
        monkeypatch.setenv("BINANCE_BACKUP_PROXY_URL", good_proxy)
        monkeypatch.setenv("BINANCE_PROXY_FAILOVER_THRESHOLD", "1")
        reset_proxy_failover_controller()

        coordinator = RESTAlignmentCoordinator(
            api_key="test_key",
            secret_key="test_secret",
            config=AlignmentConfig(alignment_timeout=0.1),
        )
        fake_session = _FailoverSession(bad_proxy, {"serverTime": 1_700_000_000_123})
        coordinator._session = fake_session

        server_time = await coordinator.get_server_time()

        assert server_time == 1_700_000_000_123
        assert fake_session.proxies == [bad_proxy, good_proxy]
        assert get_proxy_failover_controller().get_state()["active_proxy"] == good_proxy

    @pytest.mark.asyncio
    async def test_sync_server_time_offset_retries_backup_proxy(self, monkeypatch):
        """Time sync should fail over from primary proxy to backup proxy."""
        bad_proxy = "http://bad-proxy:10808"
        good_proxy = "http://good-proxy:7890"
        monkeypatch.setenv("BINANCE_PROXY_URL", bad_proxy)
        monkeypatch.setenv("BINANCE_BACKUP_PROXY_URL", good_proxy)
        monkeypatch.setenv("BINANCE_PROXY_FAILOVER_THRESHOLD", "1")
        reset_proxy_failover_controller()
        monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)

        coordinator = RESTAlignmentCoordinator(
            api_key="test_key",
            secret_key="test_secret",
            config=AlignmentConfig(alignment_timeout=0.1),
        )
        fake_session = _FailoverSession(bad_proxy, {"serverTime": 1_700_000_005_000})
        coordinator._session = fake_session

        await coordinator._sync_server_time_offset()

        assert coordinator._timestamp_offset_ms == 5_000
        assert fake_session.proxies == [bad_proxy, good_proxy]
        assert get_proxy_failover_controller().get_state()["active_proxy"] == good_proxy


class TestAlignmentMetrics:
    """对齐指标测试"""

    def test_metrics_increment(self):
        """测试指标递增"""
        metrics = AlignmentMetrics()

        metrics.total_alignments += 1
        metrics.successful_alignments += 1
        metrics.failed_alignments += 1

        assert metrics.total_alignments == 1
        assert metrics.successful_alignments == 1
        assert metrics.failed_alignments == 1

    def test_metrics_setters(self):
        """测试指标设置"""
        metrics = AlignmentMetrics()

        metrics.last_alignment_ts = time.time()
        metrics.last_alignment_reason = "test"
        metrics.last_error = "error"

        assert metrics.last_alignment_reason == "test"
        assert metrics.last_error == "error"


class TestAlignmentConfigExtended:
    """配置扩展测试"""

    def test_alignment_timeout(self):
        """测试对齐超时"""
        config = AlignmentConfig(alignment_timeout=20.0)
        assert config.alignment_timeout == 20.0

    def test_min_alignment_interval(self):
        """测试最小对齐间隔"""
        config = AlignmentConfig(min_alignment_interval=10.0)
        assert config.min_alignment_interval == 10.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
