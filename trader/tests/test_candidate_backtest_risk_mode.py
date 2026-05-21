"""
红测：覆盖 candidate backtest risk_mode 透传和 BACKTEST_PASSED 条件
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient

from trader.api.main import app
from trader.api.models.schemas import BacktestRequest
from trader.services.deployment import BacktestService
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


def test_dev_smoke_raw_only_completes_backtest_then_validation_rejects():
    """dev_smoke + raw_only 可完成回测，但 validation 阶段拒绝促进资格"""
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

        # 回测完成后进入 BACKTEST_PASSED，按钮/轮询闭环可继续到 validation。
        candidate = storage.get_strategy_candidate(candidate_id)
        assert candidate is not None
        assert candidate["status"] == "BACKTEST_PASSED"

        # 但 dev_smoke/raw_only 仍不能通过促进前验证。
        validated = client.post(f"/v1/strategy-candidates/{candidate_id}/validate")
        assert validated.status_code == 200
        payload = validated.json()
        assert payload["status"] == "REJECTED"
        failed_rules = payload["validation"]["failed_rules"]
        assert "dev_smoke_backtest_not_deployable" in failed_rules
        assert "raw_only_backtest_not_deployable" in failed_rules


def test_event_replay_equity_curve_uses_market_data_timestamps_ms():
    """event_replay 生成的 equity_curve timestamp 必须是 Unix 毫秒时间轴"""
    request = BacktestRequest(
        strategy_id="timestamp_test",
        version=1,
        symbols=["BTCUSDT"],
        start_ts_ms=1700000000000,
        end_ts_ms=1700003600000,
        venue="BINANCE",
        requested_by="test",
        data_mode="dev_smoke",
        risk_mode="event_replay",
    )
    replay_result = SimpleNamespace(
        equity_curve=[Decimal("100000"), Decimal("100250")],
        max_drawdown=Decimal("0.02"),
        approved_orders=[],
        clipped_orders=[],
        rejected_orders=[],
        rejection_reason_counts={},
    )

    simulation = BacktestService()._event_replay_result_to_simulation(
        replay_result,
        request,
        equity_timestamps_ms=[1700000000000, 1700003600000],
    )

    assert [point["timestamp"] for point in simulation["equity_curve"]] == [
        1700000000000,
        1700003600000,
    ]
