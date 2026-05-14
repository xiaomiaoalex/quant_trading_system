from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from trader.core.domain.models.order import OrderSide
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.backtesting.backtest_risk_integration import (
    BacktestRiskEnginePort,
    BacktestRiskIntegration,
    BacktestRiskReport,
    BacktestSignalResult,
    BacktestSignalStatus,
)
from trader.services.backtesting.execution_simulator import (
    NextBarOpenExecutor,
    OrderExecutionConfig,
    PendingOrder,
)
from trader.services.backtesting.risk_aware_order_processor import (
    ExecutableOrder,
    RiskAwareExecutionReport,
    RiskAwareOrderProcessor,
)


class MockRiskEngine:
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
        result = MagicMock()
        result.passed = True
        return result


class FakeRiskIntegration:
    def __init__(self, result):
        self._result = result

    async def evaluate_signal(self, signal):
        return self._result


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
def executor():
    config = OrderExecutionConfig()
    return NextBarOpenExecutor(config)


class TestExecutableOrder:
    def test_order_creation(self, sample_signal):
        order = ExecutableOrder(
            original_signal=sample_signal,
            quantity=Decimal("1.0"),
            effective_quantity=Decimal("0.5"),
            is_clipped=True,
            max_allowed_qty=Decimal("0.5"),
        )
        assert order.original_signal == sample_signal
        assert order.effective_quantity == Decimal("0.5")
        assert order.is_clipped is True


class TestRiskAwareExecutionReport:
    def test_default_report(self):
        report = RiskAwareExecutionReport()
        assert report.total_signals == 0
        assert report.queued_orders == []
        assert report.approved_queued == 0
        assert report.clipped_queued == 0
        assert report.rejected_skipped == 0


class TestRiskAwareOrderProcessor:
    @pytest.mark.asyncio
    async def test_approved_signal_queued(self, sample_signal, executor):
        engine = MockRiskEngine()
        integration = BacktestRiskIntegration(engine)
        processor = RiskAwareOrderProcessor(integration, executor)

        signal = sample_signal
        result = await processor.process_signal_async(signal)

        assert result is not None
        assert isinstance(result, ExecutableOrder)
        assert result.is_clipped is False
        assert result.effective_quantity == Decimal("1.0")
        assert len(executor.get_pending_orders()) == 1

    @pytest.mark.asyncio
    async def test_rejected_signal_not_queued(self, sample_signal, executor):
        result_mock = MagicMock()
        result_mock.passed = False
        result_mock.rejection_reason = MagicMock()
        result_mock.rejection_reason.value = "DAILY_LOSS_LIMIT"
        result_mock.details = {"risk_sizing_decision": {"max_allowed_qty": "0.0"}}

        engine = MockRiskEngine(results=[result_mock])
        integration = BacktestRiskIntegration(engine)
        processor = RiskAwareOrderProcessor(integration, executor)

        signal = sample_signal
        eval_result = await processor.process_signal_async(signal)

        assert eval_result is None
        assert len(executor.get_pending_orders()) == 0

        report = processor.report
        assert report.rejected_skipped == 1
        assert report.approved_queued == 0
        assert report.clipped_queued == 0

    @pytest.mark.asyncio
    async def test_clipped_signal_queued_with_effective_qty(self, sample_signal, executor):
        result_mock = MagicMock()
        result_mock.passed = False
        result_mock.rejection_reason = MagicMock()
        result_mock.rejection_reason.value = "CRYPTO_OPEN_ORDER_EXPOSURE"
        result_mock.details = {
            "risk_sizing_decision": {
                "max_allowed_qty": "0.5",
                "limiting_factor": "symbol_cap",
            }
        }

        engine = MockRiskEngine(results=[result_mock])
        integration = BacktestRiskIntegration(engine)
        processor = RiskAwareOrderProcessor(integration, executor)

        signal = sample_signal
        eval_result = await processor.process_signal_async(signal)

        assert eval_result is not None
        assert isinstance(eval_result, ExecutableOrder)
        assert eval_result.is_clipped is True
        assert eval_result.effective_quantity == Decimal("0.5")
        assert len(executor.get_pending_orders()) == 1

        pending = executor.get_pending_orders()[0]
        assert pending.quantity == Decimal("0.5")

        report = processor.report
        assert report.clipped_queued == 1
        assert report.approved_queued == 0
        assert report.rejected_skipped == 0

    @pytest.mark.asyncio
    async def test_clipped_without_positive_max_allowed_qty_is_not_queued(
        self, sample_signal, executor
    ):
        integration = FakeRiskIntegration(
            BacktestSignalResult(
                signal=sample_signal,
                status=BacktestSignalStatus.CLIPPED,
                rejection_reason="CRYPTO_OPEN_ORDER_EXPOSURE",
                max_allowed_qty=Decimal("0"),
                effective_quantity=Decimal("0"),
            )
        )
        processor = RiskAwareOrderProcessor(integration, executor)

        result = await processor.process_signal_async(sample_signal)

        assert result is None
        assert len(executor.get_pending_orders()) == 0
        assert processor.report.rejected_skipped == 1
        assert processor.report.rejected_reasons == {"CRYPTO_OPEN_ORDER_EXPOSURE": 1}

    @pytest.mark.asyncio
    async def test_approved_invalid_signal_type_is_not_queued(self, sample_signal, executor):
        invalid_signal = Signal(
            signal_id="sig-none",
            symbol=sample_signal.symbol,
            signal_type=SignalType.NONE,
            quantity=sample_signal.quantity,
            price=sample_signal.price,
            strategy_name=sample_signal.strategy_name,
            timestamp=None,
        )
        integration = FakeRiskIntegration(
            BacktestSignalResult(
                signal=invalid_signal,
                status=BacktestSignalStatus.APPROVED,
                effective_quantity=invalid_signal.quantity,
            )
        )
        processor = RiskAwareOrderProcessor(integration, executor)

        result = await processor.process_signal_async(invalid_signal)

        assert result is None
        assert len(executor.get_pending_orders()) == 0
        assert processor.report.approved_queued == 0
        assert processor.report.rejected_skipped == 1
        assert processor.report.rejected_reasons == {"INVALID_SIGNAL_TYPE": 1}

    @pytest.mark.asyncio
    async def test_close_short_signal_queues_buy_order(self, sample_signal, executor):
        close_short_signal = Signal(
            signal_id="sig-close-short",
            symbol=sample_signal.symbol,
            signal_type=SignalType.CLOSE_SHORT,
            quantity=sample_signal.quantity,
            price=sample_signal.price,
            strategy_name=sample_signal.strategy_name,
            timestamp=None,
        )
        integration = FakeRiskIntegration(
            BacktestSignalResult(
                signal=close_short_signal,
                status=BacktestSignalStatus.APPROVED,
                effective_quantity=close_short_signal.quantity,
            )
        )
        processor = RiskAwareOrderProcessor(integration, executor)

        result = await processor.process_signal_async(close_short_signal)

        assert result is not None
        assert executor.get_pending_orders()[0].side == OrderSide.BUY

    @pytest.mark.asyncio
    async def test_rejected_signal_not_in_any_queue(self, sample_signal, executor):
        result_mock = MagicMock()
        result_mock.passed = False
        result_mock.rejection_reason = MagicMock()
        result_mock.rejection_reason.value = "MAX_POSITIONS"
        result_mock.details = {"risk_sizing_decision": {"max_allowed_qty": "0.0"}}

        engine = MockRiskEngine(results=[result_mock])
        integration = BacktestRiskIntegration(engine)
        processor = RiskAwareOrderProcessor(integration, executor)

        signal = sample_signal
        await processor.process_signal_async(signal)

        report = processor.report
        assert report.total_signals == 1
        assert len(executor.get_pending_orders()) == 0

    @pytest.mark.asyncio
    async def test_mixed_signals_correct_queues(self, sample_signal, executor):
        passed_result = MagicMock()
        passed_result.passed = True

        clipped_result = MagicMock()
        clipped_result.passed = False
        clipped_result.rejection_reason = MagicMock()
        clipped_result.rejection_reason.value = "CRYPTO_OPEN_ORDER_EXPOSURE"
        clipped_result.details = {"risk_sizing_decision": {"max_allowed_qty": "0.5"}}

        rejected_result = MagicMock()
        rejected_result.passed = False
        rejected_result.rejection_reason = MagicMock()
        rejected_result.rejection_reason.value = "DAILY_LOSS_LIMIT"
        rejected_result.details = {"risk_sizing_decision": {"max_allowed_qty": "0.0"}}

        engine = MockRiskEngine(results=[passed_result, clipped_result, rejected_result])
        integration = BacktestRiskIntegration(engine)
        processor = RiskAwareOrderProcessor(integration, executor)

        await processor.process_signal_async(sample_signal)
        await processor.process_signal_async(sample_signal)
        await processor.process_signal_async(sample_signal)

        report = processor.report
        assert report.total_signals == 3
        assert report.approved_queued == 1
        assert report.clipped_queued == 1
        assert report.rejected_skipped == 1
        assert len(executor.get_pending_orders()) == 2

    @pytest.mark.asyncio
    async def test_reset_report(self, sample_signal, executor):
        result_mock = MagicMock()
        result_mock.passed = False
        result_mock.rejection_reason = MagicMock()
        result_mock.rejection_reason.value = "DAILY_LOSS_LIMIT"
        result_mock.details = {"risk_sizing_decision": {"max_allowed_qty": "0.0"}}

        engine = MockRiskEngine(results=[result_mock])
        integration = BacktestRiskIntegration(engine)
        processor = RiskAwareOrderProcessor(integration, executor)

        await processor.process_signal_async(sample_signal)
        assert processor.report.total_signals == 1

        processor.reset_report()
        assert processor.report.total_signals == 0

    @pytest.mark.asyncio
    async def test_queued_order_structure(self, sample_signal, executor):
        engine = MockRiskEngine()
        integration = BacktestRiskIntegration(engine)
        processor = RiskAwareOrderProcessor(integration, executor)

        await processor.process_signal_async(sample_signal)

        report = processor.report
        queued_order = report.queued_orders[0]
        assert "signal_id" in queued_order
        assert "symbol" in queued_order
        assert "requested_quantity" in queued_order
        assert "effective_quantity" in queued_order
        assert "is_clipped" in queued_order


class TestBacktestSignalResultEffectiveQuantity:
    @pytest.mark.asyncio
    async def test_approved_has_effective_quantity(self, sample_signal):
        result = MagicMock()
        result.passed = True

        engine = MockRiskEngine(results=[result])
        integration = BacktestRiskIntegration(engine)
        eval_result = await integration.evaluate_signal(sample_signal)

        assert eval_result.effective_quantity == Decimal("1.0")
        assert eval_result.status == BacktestSignalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_clipped_has_effective_quantity_from_max_allowed(self, sample_signal):
        result = MagicMock()
        result.passed = False
        result.rejection_reason = MagicMock()
        result.rejection_reason.value = "CRYPTO_OPEN_ORDER_EXPOSURE"
        result.details = {"risk_sizing_decision": {"max_allowed_qty": "0.5"}}

        engine = MockRiskEngine(results=[result])
        integration = BacktestRiskIntegration(engine)
        eval_result = await integration.evaluate_signal(sample_signal)

        assert eval_result.effective_quantity == Decimal("0.5")
        assert eval_result.status == BacktestSignalStatus.CLIPPED

    @pytest.mark.asyncio
    async def test_rejected_has_no_effective_quantity(self, sample_signal):
        result = MagicMock()
        result.passed = False
        result.rejection_reason = MagicMock()
        result.rejection_reason.value = "DAILY_LOSS_LIMIT"
        result.details = {"risk_sizing_decision": {"max_allowed_qty": "0.0"}}

        engine = MockRiskEngine(results=[result])
        integration = BacktestRiskIntegration(engine)
        eval_result = await integration.evaluate_signal(sample_signal)

        assert eval_result.effective_quantity is None
        assert eval_result.status == BacktestSignalStatus.REJECTED
