from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trader.core.domain.models.signal import Signal, SignalType
from trader.services.backtesting.backtest_risk_integration import (
    BacktestRiskEnginePort,
    BacktestRiskIntegration,
    BacktestRiskReport,
    BacktestSignalResult,
    BacktestSignalStatus,
)


class MockRiskEngine:
    """回测用模拟 RiskEngine"""

    def __init__(self, results=None):
        self._results = results or []
        self._index = 0
        self.check_pre_trade_calls = []

    async def check_pre_trade(self, signal):
        self.check_pre_trade_calls.append(signal)
        if self._index < len(self._results):
            result = self._results[self._index]
            self._index += 1
            return result
        return MagicMock(passed=True)


@pytest.fixture
def sample_signal():
    return Signal(
        signal_id="sig-001",
        symbol="BTCUSDT",
        signal_type=SignalType.LONG,
        quantity=Decimal("1.0"),
        price=Decimal("50000.0"),
        strategy_name="test_strategy",
        timestamp=None,
    )


@pytest.fixture
def passing_result():
    result = MagicMock()
    result.passed = True
    result.rejection_reason = None
    result.details = {}
    return result


@pytest.fixture
def rejected_result():
    result = MagicMock()
    result.passed = False
    result.rejection_reason = MagicMock()
    result.rejection_reason.value = "DAILY_LOSS_LIMIT"
    result.details = {
        "risk_sizing_decision": {
            "max_allowed_qty": "0.0",
            "limiting_factor": "symbol_cap",
        }
    }
    return result


@pytest.fixture
def clipped_result():
    result = MagicMock()
    result.passed = False
    result.rejection_reason = MagicMock()
    result.rejection_reason.value = "CRYPTO_OPEN_ORDER_EXPOSURE"
    result.details = {
        "risk_sizing_decision": {
            "max_allowed_qty": "0.5",
            "limiting_factor": "symbol_cap",
        }
    }
    return result


class TestBacktestSignalStatus:
    def test_status_constants(self):
        assert BacktestSignalStatus.APPROVED == "approved"
        assert BacktestSignalStatus.CLIPPED == "clipped"
        assert BacktestSignalStatus.REJECTED == "rejected"


class TestBacktestRiskReport:
    def test_default_report(self):
        report = BacktestRiskReport()
        assert report.total_signals == 0
        assert report.approved_signals == 0
        assert report.clipped_signals == 0
        assert report.rejected_signals == 0
        assert report.rejection_counts == {}
        assert report.clipped_orders == []
        assert report.rejected_orders == []
        assert report.approved_orders == []


class TestBacktestSignalResult:
    def test_approved_result(self, sample_signal):
        result = BacktestSignalResult(
            signal=sample_signal,
            status=BacktestSignalStatus.APPROVED,
        )
        assert result.signal == sample_signal
        assert result.status == "approved"
        assert result.rejection_reason is None
        assert result.max_allowed_qty is None

    def test_rejected_result(self, sample_signal):
        result = BacktestSignalResult(
            signal=sample_signal,
            status=BacktestSignalStatus.REJECTED,
            rejection_reason="DAILY_LOSS_LIMIT",
            max_allowed_qty=Decimal("0.5"),
        )
        assert result.signal == sample_signal
        assert result.status == "rejected"
        assert result.rejection_reason == "DAILY_LOSS_LIMIT"
        assert result.max_allowed_qty == Decimal("0.5")


class TestBacktestRiskIntegration:
    @pytest.mark.asyncio
    async def test_approved_signal_calls_risk_engine_check_pre_trade(
        self, sample_signal, passing_result
    ):
        engine = MockRiskEngine(results=[passing_result])
        integration = BacktestRiskIntegration(engine)
        result = await integration.evaluate_signal(sample_signal)

        assert result.status == BacktestSignalStatus.APPROVED
        assert result.signal == sample_signal
        assert len(engine.check_pre_trade_calls) == 1
        assert engine.check_pre_trade_calls[0] == sample_signal

    @pytest.mark.asyncio
    async def test_rejected_signal_accumulates_report(self, sample_signal, rejected_result):
        engine = MockRiskEngine(results=[rejected_result])
        integration = BacktestRiskIntegration(engine)
        result = await integration.evaluate_signal(sample_signal)

        assert result.status == BacktestSignalStatus.REJECTED
        report = integration.report
        assert report.total_signals == 1
        assert report.approved_signals == 0
        assert report.rejected_signals == 1
        assert report.clipped_signals == 0
        assert "DAILY_LOSS_LIMIT" in report.rejection_counts

    @pytest.mark.asyncio
    async def test_clipped_signal_when_max_allowed_less_than_requested(
        self, sample_signal, clipped_result
    ):
        engine = MockRiskEngine(results=[clipped_result])
        integration = BacktestRiskIntegration(engine)
        eval_result = await integration.evaluate_signal(sample_signal)

        assert eval_result.status == BacktestSignalStatus.CLIPPED
        assert eval_result.max_allowed_qty == Decimal("0.5")
        assert eval_result.rejection_reason == "CRYPTO_OPEN_ORDER_EXPOSURE"

        report = integration.report
        assert report.clipped_signals == 1
        assert len(report.clipped_orders) == 1
        assert report.clipped_orders[0]["max_allowed_qty"] == "0.5"
        assert report.clipped_orders[0]["clipped"] is True

    @pytest.mark.asyncio
    async def test_clipped_signal_not_in_approved_or_rejected(self, sample_signal, clipped_result):
        engine = MockRiskEngine(results=[clipped_result])
        integration = BacktestRiskIntegration(engine)
        await integration.evaluate_signal(sample_signal)

        report = integration.report
        assert report.approved_signals == 0
        assert report.rejected_signals == 0
        assert report.clipped_signals == 1

    @pytest.mark.asyncio
    async def test_rejected_signal_not_in_approved_or_clipped(self, sample_signal, rejected_result):
        engine = MockRiskEngine(results=[rejected_result])
        integration = BacktestRiskIntegration(engine)
        await integration.evaluate_signal(sample_signal)

        report = integration.report
        assert report.approved_signals == 0
        assert report.clipped_signals == 0
        assert report.rejected_signals == 1

    @pytest.mark.asyncio
    async def test_batch_evaluate_signals(self, sample_signal, passing_result):
        engine = MockRiskEngine(results=[passing_result, passing_result])
        integration = BacktestRiskIntegration(engine)

        signals = [sample_signal, sample_signal]
        report = await integration.evaluate_signals(signals)

        assert report.total_signals == 2
        assert report.approved_signals == 2
        assert len(report.approved_orders) == 2

    @pytest.mark.asyncio
    async def test_reset_report(self, sample_signal, rejected_result):
        engine = MockRiskEngine(results=[rejected_result])
        integration = BacktestRiskIntegration(engine)
        await integration.evaluate_signal(sample_signal)
        assert integration.report.total_signals == 1

        integration.reset_report()
        assert integration.report.total_signals == 0
        assert integration.report.approved_signals == 0

    @pytest.mark.asyncio
    async def test_invalid_max_allowed_qty_raises(self, sample_signal):
        result = MagicMock()
        result.passed = False
        result.rejection_reason = MagicMock()
        result.rejection_reason.value = "CRYPTO_OPEN_ORDER_EXPOSURE"
        result.details = {
            "risk_sizing_decision": {
                "max_allowed_qty": "invalid_value",
            }
        }
        engine = MockRiskEngine(results=[result])

        integration = BacktestRiskIntegration(engine)
        with pytest.raises(ValueError, match="Failed to parse max_allowed_qty"):
            await integration.evaluate_signal(sample_signal)

    @pytest.mark.asyncio
    async def test_rejected_order_record_structure(self, sample_signal, rejected_result):
        engine = MockRiskEngine(results=[rejected_result])
        integration = BacktestRiskIntegration(engine)
        await integration.evaluate_signal(sample_signal)

        report = integration.report
        rejected_order = report.rejected_orders[0]
        assert "signal_id" in rejected_order
        assert "symbol" in rejected_order
        assert "requested_quantity" in rejected_order
        assert "max_allowed_qty" in rejected_order
        assert "rejection_reason" in rejected_order
        assert rejected_order["rejected"] is True

    @pytest.mark.asyncio
    async def test_approved_order_record_structure(self, sample_signal, passing_result):
        engine = MockRiskEngine(results=[passing_result])
        integration = BacktestRiskIntegration(engine)
        await integration.evaluate_signal(sample_signal)

        report = integration.report
        approved_order = report.approved_orders[0]
        assert "signal_id" in approved_order
        assert "symbol" in approved_order
        assert "quantity" in approved_order
        assert "rejected" not in approved_order
        assert "clipped" not in approved_order


class TestBacktestRiskEnginePort:
    def test_protocol_compliance(self):
        """验证 MockRiskEngine 实现了 BacktestRiskEnginePort"""
        engine = MockRiskEngine()
        assert hasattr(engine, "check_pre_trade")
        assert callable(engine.check_pre_trade)
