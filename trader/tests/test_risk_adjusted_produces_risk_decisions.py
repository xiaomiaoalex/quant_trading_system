"""
红测：覆盖 risk_adjusted 回测必须产生风险决策（approved/clipped/rejected）
"""
from __future__ import annotations

import asyncio

from trader.services.deployment import BacktestService
from trader.api.models.schemas import BacktestRequest
from trader.storage.in_memory import get_storage


def test_risk_adjusted_backtest_produces_risk_decisions():
    """risk_adjusted 回测必须在 metrics 中包含 approved_orders, clipped_orders, rejected_orders"""
    storage = get_storage()

    code = """
from trader.core.application.strategy_protocol import (
    MarketData, StrategyResourceLimits, ValidationResult, RiskLevel
)
from trader.core.domain.models.signal import Signal, SignalType

class SignalStrategy:
    def __init__(self):
        self.name = "SignalStrategy"
        self.version = "1.0.0"
        self.risk_level = RiskLevel.LOW
        self.resource_limits = StrategyResourceLimits()
        self.strategy_id = ""
        self.deployment_id = ""
        self.symbols = []
        self._index = 0

    async def initialize(self, config: dict) -> None:
        pass

    async def on_market_data(self, data: MarketData) -> Signal | None:
        self._index += 1
        if self._index % 5 == 0:
            return Signal(
                signal_id=f"sig_{self._index}",
                symbol=data.symbol,
                signal_type=SignalType.BUY,
                quantity=1.0,
                price=data.price,
                strategy_name=self.name,
                timestamp=data.timestamp,
            )
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
    return SignalStrategy()
"""

    # 注册策略代码
    storage.create_strategy({
        "strategy_id": "risk_decision_test",
        "name": "Risk Decision Test",
        "entrypoint": "dynamic:risk_decision_test",
    })
    storage.create_strategy_code(
        "risk_decision_test",
        {
            "code_version": 1,
            "code": code,
            "checksum": "abc123",
            "created_by": "test",
        },
    )

    service = BacktestService()
    request = BacktestRequest(
        strategy_id="risk_decision_test",
        version=1,
        strategy_code_version=1,
        engine="vectorbt",
        symbols=["BTCUSDT"],
        start_ts_ms=1700000000000,
        end_ts_ms=1700003600000,
        venue="BINANCE",
        requested_by="test",
        data_mode="dev_smoke",
        risk_mode="risk_adjusted",
    )
    backtest = service.create_backtest(request)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(service._run_backtest(backtest.run_id, request))
    loop.run_until_complete(task)
    loop.close()

    # 从存储中获取回测结果
    backtest_result = storage.get_backtest(backtest.run_id)
    assert backtest_result is not None
    if backtest_result["status"] == "FAILED":
        print(f"Backtest failed with error: {backtest_result.get('error')}")
    assert backtest_result["status"] == "COMPLETED"

    # 验证回测结果包含风控决策字段
    metrics = backtest_result.get("metrics", {})
    assert "approved_orders" in metrics, "risk_adjusted backtest must include approved_orders"
    assert "clipped_orders" in metrics, "risk_adjusted backtest must include clipped_orders"
    assert "rejected_orders" in metrics, "risk_adjusted backtest must include rejected_orders"
    assert "rejection_reason_counts" in metrics, "risk_adjusted backtest must include rejection_reason_counts"

    # 由于使用了真实的 RiskEngine，应该有风控决策产生
    # 注意：FakeBroker 初始余额足够，所以可能全部通过，但字段必须存在
    assert isinstance(metrics["approved_orders"], list)
    assert isinstance(metrics["clipped_orders"], list)
    assert isinstance(metrics["rejected_orders"], list)
    assert isinstance(metrics["rejection_reason_counts"], dict)


def test_event_replay_backtest_produces_risk_replay_metrics():
    """event_replay 回测必须在 metrics 中包含 risk_replay 统计"""
    storage = get_storage()

    code = """
from trader.core.application.strategy_protocol import (
    MarketData, StrategyResourceLimits, ValidationResult, RiskLevel
)
from trader.core.domain.models.signal import Signal, SignalType

class SignalStrategy:
    def __init__(self):
        self.name = "SignalStrategy"
        self.version = "1.0.0"
        self.risk_level = RiskLevel.LOW
        self.resource_limits = StrategyResourceLimits()
        self.strategy_id = ""
        self.deployment_id = ""
        self.symbols = []
        self._index = 0

    async def initialize(self, config: dict) -> None:
        pass

    async def on_market_data(self, data: MarketData) -> Signal | None:
        self._index += 1
        if self._index % 5 == 0:
            return Signal(
                signal_id=f"sig_{self._index}",
                symbol=data.symbol,
                signal_type=SignalType.BUY,
                quantity=1.0,
                price=data.price,
                strategy_name=self.name,
                timestamp=data.timestamp,
            )
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
    return SignalStrategy()
"""

    storage.create_strategy({
        "strategy_id": "event_replay_test",
        "name": "Event Replay Test",
        "entrypoint": "dynamic:event_replay_test",
    })
    storage.create_strategy_code(
        "event_replay_test",
        {
            "code_version": 1,
            "code": code,
            "checksum": "def456",
            "created_by": "test",
        },
    )

    service = BacktestService()
    request = BacktestRequest(
        strategy_id="event_replay_test",
        version=1,
        strategy_code_version=1,
        engine="vectorbt",
        symbols=["BTCUSDT"],
        start_ts_ms=1700000000000,
        end_ts_ms=1700003600000,
        venue="BINANCE",
        requested_by="test",
        data_mode="dev_smoke",
        risk_mode="event_replay",
    )
    backtest = service.create_backtest(request)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(service._run_backtest(backtest.run_id, request))
    loop.run_until_complete(task)
    loop.close()

    # 从存储中获取回测结果
    backtest_result = storage.get_backtest(backtest.run_id)
    assert backtest_result is not None
    if backtest_result["status"] == "FAILED":
        print(f"Backtest failed with error: {backtest_result.get('error')}")
    assert backtest_result["status"] == "COMPLETED"

    # 验证 event_replay 结果包含 risk_replay 统计
    metrics = backtest_result.get("metrics", {})
    assert "risk_replay" in metrics, "event_replay backtest must include risk_replay metrics"
    risk_replay = metrics["risk_replay"]
    assert "approved_orders" in risk_replay
    assert "clipped_orders" in risk_replay
    assert "rejected_orders" in risk_replay
    assert "rejection_reason_counts" in risk_replay
