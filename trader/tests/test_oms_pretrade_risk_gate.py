from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trader.core.application.risk_engine import RejectionReason, RiskCheckResult, RiskLevel
from trader.core.domain.models.order import OrderStatus
from trader.core.domain.models.risk_decision import RiskSizingDecision, RiskSizingDecisionType
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.oms_callback import OMSCallbackHandler, RiskRejectedError
from trader.storage.in_memory import ControlPlaneInMemoryStorage


def _broker() -> MagicMock:
    broker = MagicMock()
    broker.broker_name = "binance_spot_demo"
    broker.get_symbol_step_size = AsyncMock(return_value=Decimal("0.00001"))
    broker.get_exchange_info = AsyncMock(
        return_value={"symbols": [{"filters": [{"filterType": "NOTIONAL", "minNotional": "10"}]}]}
    )
    broker.get_ticker_prices = AsyncMock(return_value={"BTCUSDT": Decimal("50000")})
    broker._fetch_account = AsyncMock()
    broker._account_cache = {
        "balances": [
            {"asset": "USDT", "free": "10000", "locked": "0"},
            {"asset": "BTC", "free": "1", "locked": "0"},
        ]
    }
    broker.place_order = AsyncMock(
        return_value=MagicMock(
            broker_order_id="broker-1",
            filled_quantity=Decimal("0"),
            average_price=Decimal("0"),
            status=OrderStatus.SUBMITTED,
            created_at=None,
        )
    )
    return broker


def _signal() -> Signal:
    return Signal(
        signal_type=SignalType.LONG,
        symbol="BTCUSDT",
        quantity=Decimal("0.01"),
        price=Decimal("50000"),
    )


@pytest.mark.asyncio
async def test_oms_pretrade_risk_rejection_blocks_broker_place_order() -> None:
    risk_check = AsyncMock(
        return_value=RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.HIGH,
            rejection_reason=RejectionReason.CRYPTO_OPEN_ORDER_EXPOSURE,
            message="symbol risk cap breached",
        )
    )
    broker = _broker()
    handler = OMSCallbackHandler(
        broker=broker,
        storage=ControlPlaneInMemoryStorage(),
        live_trading_enabled=True,
        pre_trade_risk_check=risk_check,
    )

    with pytest.raises(RiskRejectedError, match="symbol risk cap breached"):
        await handler.execute_signal("test_strategy", _signal())

    risk_check.assert_awaited_once()
    broker.place_order.assert_not_awaited()
    stats = handler.get_dedup_stats()
    assert stats["reject_reason_counts"]["CRYPTO_OPEN_ORDER_EXPOSURE"] == 1


@pytest.mark.asyncio
async def test_oms_pretrade_risk_check_exception_fails_closed() -> None:
    risk_check = AsyncMock(side_effect=RuntimeError("risk backend down"))
    broker = _broker()
    handler = OMSCallbackHandler(
        broker=broker,
        storage=ControlPlaneInMemoryStorage(),
        live_trading_enabled=True,
        pre_trade_risk_check=risk_check,
    )

    with pytest.raises(RiskRejectedError, match="risk backend down"):
        await handler.execute_signal("test_strategy", _signal())

    broker.place_order.assert_not_awaited()
    stats = handler.get_dedup_stats()
    assert stats["reject_reason_counts"]["RISK_SYSTEM_ERROR"] == 1


@pytest.mark.asyncio
async def test_oms_pretrade_risk_pass_allows_existing_order_flow() -> None:
    risk_check = AsyncMock(return_value=RiskCheckResult(passed=True))
    broker = _broker()
    handler = OMSCallbackHandler(
        broker=broker,
        storage=ControlPlaneInMemoryStorage(),
        live_trading_enabled=True,
        pre_trade_risk_check=risk_check,
    )

    result = await handler.execute_signal("test_strategy", _signal())

    assert result is not None
    risk_check.assert_awaited_once()
    broker.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_oms_pretrade_risk_check_can_be_late_bound_after_handler_creation() -> None:
    risk_check = AsyncMock(
        return_value=RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.HIGH,
            rejection_reason=RejectionReason.CRYPTO_MARGIN_LIMIT,
            message="late-bound risk rejected",
        )
    )
    broker = _broker()
    handler = OMSCallbackHandler(
        broker=broker,
        storage=ControlPlaneInMemoryStorage(),
        live_trading_enabled=True,
    )

    handler.set_pre_trade_risk_check(risk_check)

    with pytest.raises(RiskRejectedError, match="late-bound risk rejected"):
        await handler.execute_signal("test_strategy", _signal())

    risk_check.assert_awaited_once()
    broker.place_order.assert_not_awaited()


class TestOMSRiskSizingClip:
    """阶段1：实盘 RiskSizing 裁剪测试"""

    @staticmethod
    def _make_clip_risk_check(
        requested_qty: Decimal,
        final_qty: Decimal,
        limiting_factor: str = "symbol_cap",
    ) -> AsyncMock:
        """构造 CLIP 语义的风险检查回调

        新语义：passed=True + decision=clip + final_qty < requested_qty
        """
        sizing_decision = RiskSizingDecision(
            requested_qty=requested_qty,
            normalized_qty=requested_qty,
            max_allowed_qty=final_qty,
            final_qty=final_qty,
            decision=RiskSizingDecisionType.CLIP,
            reason="CLIPPED_BY_RISK_SIZING",
            limiting_factor=limiting_factor,
            constraints=(),
            trace_id="test-trace-001",
        )
        return AsyncMock(
            return_value=RiskCheckResult(
                passed=True,
                risk_level=RiskLevel.LOW,
                rejection_reason=None,
                message="CLIPPED",
                details={"risk_sizing_decision": sizing_decision.to_dict()},
            )
        )

    @pytest.mark.asyncio
    async def test_oms_pretrade_risk_clip_applies_final_qty(self) -> None:
        """CLIP 决策下，broker 实际收到的下单数量必须是 final_qty，不是原始 requested_qty"""
        requested = Decimal("0.01")
        final = Decimal("0.005")
        risk_check = self._make_clip_risk_check(requested, final)

        broker = _broker()
        captured_order = {"quantity": None}

        original_place_order = broker.place_order

        async def capture_place_order(**kwargs) -> MagicMock:
            nonlocal captured_order
            captured_order = kwargs
            return await original_place_order(**kwargs)

        broker.place_order = AsyncMock(side_effect=capture_place_order)

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=risk_check,
        )

        await handler.execute_signal("test_strategy", _signal())

        risk_check.assert_awaited_once()
        broker.place_order.assert_awaited_once()
        assert captured_order is not None
        assert captured_order.get("quantity") == final, (
            f"Broker must receive final_qty={final}, not requested_qty={requested}. "
            f"Got: {captured_order.get('quantity')}"
        )

    @pytest.mark.asyncio
    async def test_oms_pretrade_risk_clip_audits_requested_and_final_qty(self) -> None:
        """CLIP 决策下，审计必须同时记录 requested_qty 和 final_qty 到 storage"""
        requested = Decimal("0.02")
        final = Decimal("0.008")
        risk_check = self._make_clip_risk_check(requested, final, "total_cap")

        broker = _broker()
        broker.place_order = AsyncMock(
            return_value=MagicMock(
                broker_order_id="broker-audit-1",
                filled_quantity=Decimal("0"),
                average_price=Decimal("50000"),
                status=OrderStatus.SUBMITTED,
                created_at=None,
            )
        )

        storage = ControlPlaneInMemoryStorage()
        handler = OMSCallbackHandler(
            broker=broker,
            storage=storage,
            live_trading_enabled=True,
            pre_trade_risk_check=risk_check,
        )

        result = await handler.execute_signal("test_strategy", _signal())

        assert result is not None
        stats = handler.get_dedup_stats()
        assert stats["order_submit_ok"] == 1

        orders = storage.list_orders(account_id=None, venue=None)
        assert len(orders) >= 1
        clip_order = orders[-1]
        assert clip_order.get("risk_requested_qty") == str(requested)
        assert clip_order.get("risk_final_qty") == str(final)
        assert clip_order.get("risk_limiting_factor") == "total_cap"
        assert clip_order.get("risk_trace_id") == "test-trace-001"
        assert Decimal(clip_order.get("qty", "0")) == final
        assert clip_order.get("risk_sizing_decision") is not None

    @pytest.mark.asyncio
    async def test_oms_pretrade_risk_clip_zero_or_none_final_qty_rejects(self) -> None:
        """final_qty <= 0 或缺失时，OMS 必须 fail-closed 返回 REJECT，不得继续下单"""
        sizing_decision = RiskSizingDecision(
            requested_qty=Decimal("0.01"),
            normalized_qty=Decimal("0.01"),
            max_allowed_qty=Decimal("0"),
            final_qty=Decimal("0"),
            decision=RiskSizingDecisionType.CLIP,
            reason="ZERO_FINAL_QTY",
            limiting_factor="margin_limit",
            constraints=(),
            trace_id="test-trace-zero",
        )
        risk_check = AsyncMock(
            return_value=RiskCheckResult(
                passed=True,
                risk_level=RiskLevel.HIGH,
                rejection_reason=None,
                message="final_qty is zero",
                details={"risk_sizing_decision": sizing_decision.to_dict()},
            )
        )

        broker = _broker()
        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=risk_check,
        )

        with pytest.raises(RiskRejectedError):
            await handler.execute_signal("test_strategy", _signal())

        broker.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_oms_pretrade_risk_reject_never_calls_broker(self) -> None:
        """REJECT 决策时，broker 不得收到 place_order 调用"""
        sizing_decision = RiskSizingDecision(
            requested_qty=Decimal("0.01"),
            normalized_qty=Decimal("0"),
            max_allowed_qty=Decimal("0"),
            final_qty=Decimal("0"),
            decision=RiskSizingDecisionType.REJECT,
            reason="REJECTED_BY_RISK",
            limiting_factor="exchange_rule",
            constraints=(),
            trace_id="test-trace-reject",
        )
        risk_check = AsyncMock(
            return_value=RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                rejection_reason=RejectionReason.CRYPTO_EXCHANGE_RULE,
                message="exchange rule rejected",
                details={"risk_sizing_decision": sizing_decision.to_dict()},
            )
        )

        broker = _broker()
        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=risk_check,
        )

        with pytest.raises(RiskRejectedError):
            await handler.execute_signal("test_strategy", _signal())

        broker.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_live_and_backtest_clip_use_same_sizing_decision_payload(self) -> None:
        """live OMS 和 backtest 必须使用相同的 RiskSizingDecision payload 结构"""
        requested = Decimal("0.05")
        final = Decimal("0.02")
        limiting = "cluster_cap"

        sizing_decision = RiskSizingDecision(
            requested_qty=requested,
            normalized_qty=Decimal("0.05"),
            max_allowed_qty=final,
            final_qty=final,
            decision=RiskSizingDecisionType.CLIP,
            reason="CLUSTER_CAP_EXCEEDED",
            limiting_factor=limiting,
            constraints=(),
            trace_id="test-trace-same",
        )

        decision_dict = sizing_decision.to_dict()

        assert decision_dict["requested_qty"] == str(requested)
        assert decision_dict["final_qty"] == str(final)
        assert decision_dict["decision"] == "clip"
        assert decision_dict["limiting_factor"] == limiting

        assert sizing_decision.is_clip
        assert not sizing_decision.is_rejection
        assert not sizing_decision.is_approval
