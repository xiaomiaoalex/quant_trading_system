from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from trader.core.domain.models.signal import Signal, SignalType
from trader.services.backtesting.ports import BacktestConfig
from trader.services.backtesting.vectorbt_risk_adapter import (
    VectorBTAdapterWithRisk,
    VectorBTRiskAdapterConfig,
    VectorBTRiskInputPlan,
    VectorBTRiskMetrics,
)


class MockBaseAdapter:
    def __init__(self, data_provider=None):
        self._data_provider = data_provider

    def _get_data_provider(self):
        return self._data_provider


class MockRiskEngine:
    def __init__(self, results=None):
        self._results = list(results or [])
        self._index = 0
        self.checked_signals = []

    async def check_pre_trade(self, signal):
        self.checked_signals.append(signal)
        if self._index < len(self._results):
            result = self._results[self._index]
            self._index += 1
            return result
        return _risk_result(passed=True)


class MockDataProvider:
    def __init__(self, klines):
        self._klines = klines

    async def get_klines(self, symbol, interval, start_date, end_date):
        return self._klines


class MockKline:
    def __init__(self, close_price, timestamp=None):
        self.close = Decimal(str(close_price))
        self.timestamp = timestamp or datetime(2026, 5, 13, tzinfo=timezone.utc)


@pytest.fixture
def sample_config():
    return BacktestConfig(
        start_date=datetime(2026, 5, 13, tzinfo=timezone.utc),
        end_date=datetime(2026, 5, 14, tzinfo=timezone.utc),
        initial_capital=Decimal("10000"),
        symbol="ETHUSDT",
        interval="1h",
    )


@pytest.fixture
def klines():
    return [MockKline(100 + i) for i in range(5)]


def _risk_result(passed, reason=None, max_allowed_qty=None):
    result = MagicMock()
    result.passed = passed
    result.rejection_reason = None
    result.details = {}
    if reason is not None:
        result.rejection_reason = MagicMock()
        result.rejection_reason.value = reason
    if max_allowed_qty is not None:
        result.details = {"risk_sizing_decision": {"max_allowed_qty": str(max_allowed_qty)}}
    return result


class TestVectorBTRiskAdapterConfig:
    def test_default_config(self):
        config = VectorBTRiskAdapterConfig()
        assert config.enable_risk_adjustment is True
        assert config.default_order_quantity == Decimal("1")
        assert config.freq == "1h"


class TestVectorBTRiskInputPlanning:
    @pytest.mark.asyncio
    async def test_risk_signals_use_backtest_config_symbol_price_and_timestamp(
        self, sample_config, klines
    ):
        engine = MockRiskEngine(results=[_risk_result(passed=True)])
        adapter = VectorBTAdapterWithRisk(
            base_adapter=MockBaseAdapter(),
            risk_engine=engine,
            config=VectorBTRiskAdapterConfig(default_order_quantity=Decimal("0.25")),
        )

        plan = await adapter._build_risk_adjusted_input_plan(sample_config, klines, [1, 0, 0, 0, 0])

        checked = engine.checked_signals[0]
        assert checked.symbol == "ETHUSDT"
        assert checked.quantity == Decimal("0.25")
        assert checked.price == Decimal("100")
        assert checked.timestamp == klines[0].timestamp
        assert plan.entries == [True, False, False, False, False]
        assert plan.sizes == [0.25, 0.0, 0.0, 0.0, 0.0]

    @pytest.mark.asyncio
    async def test_rejected_signal_is_zeroed_and_counted(self, sample_config, klines):
        engine = MockRiskEngine(
            results=[_risk_result(passed=False, reason="DAILY_LOSS_LIMIT", max_allowed_qty="0")]
        )
        adapter = VectorBTAdapterWithRisk(MockBaseAdapter(), risk_engine=engine)

        plan = await adapter._build_risk_adjusted_input_plan(sample_config, klines, [1, 0, 0, 0, 0])

        assert plan.entries == [False, False, False, False, False]
        assert plan.exits == [False, False, False, False, False]
        assert plan.sizes == [0.0, 0.0, 0.0, 0.0, 0.0]
        assert len(plan.rejected_orders) == 1
        assert plan.rejection_reason_counts == {"DAILY_LOSS_LIMIT": 1}

    @pytest.mark.asyncio
    async def test_clipped_signal_writes_effective_size(self, sample_config, klines):
        engine = MockRiskEngine(
            results=[
                _risk_result(
                    passed=False,
                    reason="CRYPTO_OPEN_ORDER_EXPOSURE",
                    max_allowed_qty="0.4",
                )
            ]
        )
        adapter = VectorBTAdapterWithRisk(MockBaseAdapter(), risk_engine=engine)

        plan = await adapter._build_risk_adjusted_input_plan(sample_config, klines, [1, 0, 0, 0, 0])

        assert plan.entries == [True, False, False, False, False]
        assert plan.sizes == [0.4, 0.0, 0.0, 0.0, 0.0]
        assert len(plan.clipped_orders) == 1
        assert plan.clipped_orders[0]["effective_quantity"] == "0.4"

    @pytest.mark.asyncio
    async def test_close_signal_uses_exit_side_not_short_open(self, sample_config, klines):
        engine = MockRiskEngine(results=[_risk_result(passed=True)])
        adapter = VectorBTAdapterWithRisk(MockBaseAdapter(), risk_engine=engine)

        plan = await adapter._build_risk_adjusted_input_plan(
            sample_config, klines, [-1, 0, 0, 0, 0]
        )

        assert engine.checked_signals[0].signal_type == SignalType.CLOSE_LONG
        assert plan.entries == [False, False, False, False, False]
        assert plan.exits == [True, False, False, False, False]

    @pytest.mark.asyncio
    async def test_existing_signal_objects_keep_their_quantity(self, sample_config, klines):
        signal = Signal(
            signal_id="custom",
            symbol="SOLUSDT",
            signal_type=SignalType.LONG,
            quantity=Decimal("3"),
            price=Decimal("150"),
            strategy_name="custom_strategy",
            timestamp=klines[0].timestamp,
        )
        engine = MockRiskEngine(results=[_risk_result(passed=True)])
        adapter = VectorBTAdapterWithRisk(MockBaseAdapter(), risk_engine=engine)

        plan = await adapter._build_risk_adjusted_input_plan(sample_config, klines, [signal])

        assert engine.checked_signals[0] == signal
        assert plan.entries == [True]
        assert plan.sizes == [3.0]


class TestVectorBTResultContract:
    def test_build_result_carries_risk_report_fields(self, sample_config):
        adapter = VectorBTAdapterWithRisk(MockBaseAdapter())
        raw_plan = VectorBTRiskInputPlan(
            raw_signals=[{"signal_id": "raw"}],
        )
        risk_plan = VectorBTRiskInputPlan(
            approved_orders=[{"signal_id": "a"}],
            clipped_orders=[{"signal_id": "c"}],
            rejected_orders=[{"signal_id": "r"}],
            rejection_reason_counts={"DAILY_LOSS_LIMIT": 1},
        )

        result = adapter._build_result(
            raw_metrics=VectorBTRiskMetrics(
                equity_curve=[10000, 9900],
                max_drawdown=0.01,
                sharpe_ratio=1.2,
                total_return=-0.01,
                win_rate=0.5,
                num_trades=1,
                final_capital=9900,
            ),
            risk_adjusted_metrics=VectorBTRiskMetrics(
                equity_curve=[10000, 10000],
                max_drawdown=0,
                sharpe_ratio=0,
                total_return=0,
                win_rate=0,
                num_trades=0,
                final_capital=10000,
            ),
            config=sample_config,
            raw_plan=raw_plan,
            risk_plan=risk_plan,
        )

        assert result.raw_signals == [{"signal_id": "raw"}]
        assert result.approved_orders == [{"signal_id": "a"}]
        assert result.clipped_orders == [{"signal_id": "c"}]
        assert result.rejected_orders == [{"signal_id": "r"}]
        assert result.rejection_reason_counts == {"DAILY_LOSS_LIMIT": 1}
        assert result.max_drawdown_before_risk == Decimal("0.01")
        assert result.max_drawdown_after_risk == Decimal("0")
        assert result.risk_adjusted_metrics["max_drawdown"] == 0


class TestVectorBTBacktestWithRisk:
    @pytest.mark.asyncio
    async def test_run_backtest_with_risk_populates_risk_adjusted_curve(self, sample_config):
        klines = [MockKline(price) for price in [100, 105, 95, 110, 90]]
        provider = MockDataProvider(klines)
        engine = MockRiskEngine(
            results=[
                _risk_result(passed=True),
                _risk_result(passed=False, reason="DAILY_LOSS_LIMIT", max_allowed_qty="0"),
            ]
        )
        adapter = VectorBTAdapterWithRisk(
            base_adapter=MockBaseAdapter(provider),
            risk_engine=engine,
            config=VectorBTRiskAdapterConfig(default_order_quantity=Decimal("1")),
        )

        class Strategy:
            async def generate_signals(self, bars):
                return [1, 0, -1, 0, 0]

        result = await adapter.run_backtest_with_risk(sample_config, Strategy())

        assert len(result.risk_adjusted_equity_curve) == len(klines)
        assert result.max_drawdown_after_risk is not None
        assert len(engine.checked_signals) == 2
