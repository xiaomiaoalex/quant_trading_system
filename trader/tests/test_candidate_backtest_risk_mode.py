"""
红测：覆盖 candidate backtest risk_mode 透传和 BACKTEST_PASSED 条件
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from trader.api.main import app
from trader.storage.in_memory import get_storage


def test_backtest_request_includes_risk_mode_from_dataset():
    """前端提交的 dataset.risk_mode 必须透传到 BacktestRequest"""
    storage = get_storage()
    with TestClient(app) as client:
        # 创建候选策略并通过 debug
        code = """
from trader.core.application.strategy_protocol import (
    MarketData, StrategyResourceLimits, ValidationResult, RiskLevel
)
from trader.core.domain.models.signal import Signal

class MinimalStrategy:
    def __init__(self):
        self.name = "MinimalStrategy"
        self.version = "1.0.0"
        self.risk_level = RiskLevel.LOW
        self.resource_limits = StrategyResourceLimits()
        self.strategy_id = ""
        self.deployment_id = ""
        self.symbols = []

    async def initialize(self, config: dict) -> None:
        pass

    async def on_market_data(self, data: MarketData) -> Signal | None:
        return None

    async def on_fill(self, fill: dict) -> None:
        pass

    async def on_cancel(self, cancel: dict) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def validate(self) -> ValidationResult:
        return ValidationResult.valid()

    def update_config(self, config: dict) -> ValidationResult:
        return ValidationResult.valid()

def get_plugin():
    return MinimalStrategy()
"""
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "risk_mode_test",
                "name": "Risk Mode Test",
                "code": code,
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        debugged = client.post(
            f"/v1/strategy-candidates/{candidate_id}/debug",
            json={"code": code, "config": {}},
        )
        assert debugged.status_code == 200
        assert debugged.json()["ok"] is True

        # 提交 event_replay 回测
        backtest_resp = client.post(
            f"/v1/strategy-candidates/{candidate_id}/backtests",
            json={
                "dataset": {
                    "symbols": ["BTCUSDT"],
                    "start_ts_ms": 1700000000000,
                    "end_ts_ms": 1700000100000,
                    "feature_version": "dev_smoke",
                    "venue": "BINANCE",
                    "initial_capital": 100000,
                    "fee_bps": 10,
                    "slippage_bps": 5,
                    "data_mode": "dev_smoke",
                    "risk_mode": "event_replay",
                },
                "requested_by": "test",
            },
        )
        assert backtest_resp.status_code == 200

        # 验证存储的 backtest 包含 risk_mode
        backtest_run_id = backtest_resp.json()["backtest_run_id"]
        backtest = storage.get_backtest(backtest_run_id)
        assert backtest is not None
        # BacktestRequest 中的 risk_mode 应该被保存
        assert backtest.get("risk_mode") == "event_replay"


def test_dev_smoke_raw_only_does_not_auto_promote():
    """dev_smoke + raw_only 回测完成后不能自动进入 BACKTEST_PASSED"""
    storage = get_storage()
    with TestClient(app) as client:
        code = """
from trader.core.application.strategy_protocol import (
    MarketData, StrategyResourceLimits, ValidationResult, RiskLevel
)
from trader.core.domain.models.signal import Signal

class MinimalStrategy:
    def __init__(self):
        self.name = "MinimalStrategy"
        self.version = "1.0.0"
        self.risk_level = RiskLevel.LOW
        self.resource_limits = StrategyResourceLimits()
        self.strategy_id = ""
        self.deployment_id = ""
        self.symbols = []

    async def initialize(self, config: dict) -> None:
        pass

    async def on_market_data(self, data: MarketData) -> Signal | None:
        return None

    async def on_fill(self, fill: dict) -> None:
        pass

    async def on_cancel(self, cancel: dict) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def validate(self) -> ValidationResult:
        return ValidationResult.valid()

    def update_config(self, config: dict) -> ValidationResult:
        return ValidationResult.valid()

def get_plugin():
    return MinimalStrategy()
"""
        created = client.post(
            "/v1/strategy-candidates",
            json={
                "strategy_id": "no_promote_test",
                "name": "No Promote Test",
                "code": code,
            },
        )
        assert created.status_code == 201
        candidate_id = created.json()["candidate_id"]

        debugged = client.post(
            f"/v1/strategy-candidates/{candidate_id}/debug",
            json={"code": code, "config": {}},
        )
        assert debugged.status_code == 200

        backtest_resp = client.post(
            f"/v1/strategy-candidates/{candidate_id}/backtests",
            json={
                "dataset": {
                    "symbols": ["BTCUSDT"],
                    "start_ts_ms": 1700000000000,
                    "end_ts_ms": 1700000100000,
                    "feature_version": "dev_smoke",
                    "venue": "BINANCE",
                    "initial_capital": 100000,
                    "fee_bps": 10,
                    "slippage_bps": 5,
                    "data_mode": "dev_smoke",
                    "risk_mode": "raw_only",
                },
                "requested_by": "test",
            },
        )
        assert backtest_resp.status_code == 200
        assert backtest_resp.json()["status"] == "BACKTEST_RUNNING"

        # 手动运行回测（模拟 BacktestService._run_backtest 完成）
        from trader.services.deployment import BacktestService
        from trader.api.models.schemas import BacktestRequest

        backtest_run_id = backtest_resp.json()["backtest_run_id"]
        service = BacktestService()
        request = BacktestRequest(
            strategy_id="no_promote_test",
            version=1,
            symbols=["BTCUSDT"],
            start_ts_ms=1700000000000,
            end_ts_ms=1700000100000,
            venue="BINANCE",
            requested_by="test",
            data_mode="dev_smoke",
            risk_mode="raw_only",
            candidate_id=candidate_id,
        )
        backtest = service.create_backtest(request)

        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        task = loop.create_task(service._run_backtest(backtest.run_id, request))
        loop.run_until_complete(task)
        loop.close()

        # dev_smoke + raw_only 不应该自动晋级
        candidate = storage.get_strategy_candidate(candidate_id)
        assert candidate is not None
        assert candidate["status"] != "BACKTEST_PASSED"
