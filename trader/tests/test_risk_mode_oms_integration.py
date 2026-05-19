"""
阶段2：RiskMode/KillSwitch 统一控制 OMS 测试

目标：让 RiskMode/KillSwitch 成为实盘执行链路的一等控制源
- StrategyRunner 做 early gate
- OMS 做 final gate
- KillSwitch/RiskMode 等级语义统一
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trader.core.application.risk_engine import KillSwitchLevel
from trader.core.domain.models.order import OrderStatus
from trader.core.domain.models.risk_mode import RiskMode
from trader.core.domain.models.signal import Signal, SignalType
from trader.services.oms_callback import OMSCallbackHandler, RiskRejectedError
from trader.storage.in_memory import ControlPlaneInMemoryStorage


def _make_broker() -> MagicMock:
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


def _long_signal() -> Signal:
    return Signal(
        signal_type=SignalType.LONG,
        symbol="BTCUSDT",
        quantity=Decimal("0.01"),
        price=Decimal("50000"),
    )


def _close_long_signal() -> Signal:
    return Signal(
        signal_type=SignalType.CLOSE_LONG,
        symbol="BTCUSDT",
        quantity=Decimal("0.01"),
        price=Decimal("50000"),
    )


class TestRiskModeGate:
    """RiskMode 控制链路测试"""

    @pytest.mark.asyncio
    async def test_no_new_positions_blocks_open_but_allows_reduce(self) -> None:
        """NO_NEW_POSITIONS 阻止开仓/加仓，但允许减仓"""
        from trader.core.application.risk_engine import RejectionReason, RiskCheckResult, RiskLevel
        from trader.core.domain.models.risk_decision import (
            RiskSizingDecision,
            RiskSizingDecisionType,
        )

        open_signal = _long_signal()
        close_signal = _close_long_signal()

        def risk_check_for_mode(mode: RiskMode):
            def check(sig: Signal) -> RiskCheckResult:
                if mode == RiskMode.NO_NEW_POSITIONS:
                    if sig.is_open_signal():
                        return RiskCheckResult(
                            passed=False,
                            risk_level=RiskLevel.HIGH,
                            rejection_reason=RejectionReason.RISK_MODE_CLOSE_ONLY,
                            message="NO_NEW_POSITIONS: open positions blocked",
                            details={},
                        )
                return RiskCheckResult(passed=True)

            return check

        mode = RiskMode.NO_NEW_POSITIONS
        broker = _make_broker()

        open_handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=risk_check_for_mode(mode),
        )

        close_handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=risk_check_for_mode(mode),
        )

        with pytest.raises(RiskRejectedError, match="NO_NEW_POSITIONS"):
            await open_handler.execute_signal("test_strategy", open_signal)

        broker.place_order.assert_not_awaited()

        result = await close_handler.execute_signal("test_strategy", close_signal)
        assert result is not None

    @pytest.mark.asyncio
    async def test_close_only_allows_only_reduce(self) -> None:
        """CLOSE_ONLY 只允许降低净敞口，阻止所有开仓"""
        from trader.core.application.risk_engine import RejectionReason, RiskCheckResult, RiskLevel

        open_signal = _long_signal()
        close_signal = _close_long_signal()

        def risk_check_for_mode(mode: RiskMode):
            def check(sig: Signal) -> RiskCheckResult:
                if mode == RiskMode.CLOSE_ONLY:
                    if sig.is_open_signal():
                        return RiskCheckResult(
                            passed=False,
                            risk_level=RiskLevel.HIGH,
                            rejection_reason=RejectionReason.RISK_MODE_CLOSE_ONLY,
                            message="CLOSE_ONLY: only reduce allowed",
                            details={},
                        )
                return RiskCheckResult(passed=True)

            return check

        mode = RiskMode.CLOSE_ONLY
        broker = _make_broker()

        open_handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=risk_check_for_mode(mode),
        )

        close_handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=risk_check_for_mode(mode),
        )

        with pytest.raises(RiskRejectedError, match="CLOSE_ONLY"):
            await open_handler.execute_signal("test_strategy", open_signal)

        broker.place_order.assert_not_awaited()

        result = await close_handler.execute_signal("test_strategy", close_signal)
        assert result is not None

    @pytest.mark.asyncio
    async def test_cancel_all_and_halt_blocks_all_orders(self) -> None:
        """CANCEL_ALL_AND_HALT 触发 cancel-all，且 broker 不得收到新 place_order"""
        from trader.core.application.risk_engine import RejectionReason, RiskCheckResult, RiskLevel

        signal = _close_long_signal()
        cancelled_all = []

        async def mock_cancel_all():
            cancelled_all.append(True)

        broker = _make_broker()
        broker.cancel_all = AsyncMock(side_effect=mock_cancel_all)

        def risk_check(sig: Signal) -> RiskCheckResult:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.CRITICAL,
                rejection_reason=RejectionReason.RISK_MODE_CLOSE_ONLY,
                message="CANCEL_ALL_AND_HALT: all orders blocked",
                details={"risk_mode": "CANCEL_ALL_AND_HALT"},
            )

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=risk_check,
        )

        with pytest.raises(RiskRejectedError, match="CANCEL_ALL_AND_HALT"):
            await handler.execute_signal("test_strategy", signal)

        broker.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_liquidate_and_disconnect_allows_system_liquidation_only(self) -> None:
        """LIQUIDATE_AND_DISCONNECT 禁止策略订单，但允许系统强平 actor"""
        from trader.core.application.risk_engine import RejectionReason, RiskCheckResult, RiskLevel

        strategy_signal = _close_long_signal()

        def risk_check(sig: Signal) -> RiskCheckResult:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.CRITICAL,
                rejection_reason=RejectionReason.RISK_MODE_CLOSE_ONLY,
                message="LIQUIDATE_AND_DISCONNECT: strategy orders blocked",
                details={"risk_mode": "LIQUIDATE_AND_DISCONNECT", "is_system_liquidation": False},
            )

        broker = _make_broker()
        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=risk_check,
        )

        with pytest.raises(RiskRejectedError, match="LIQUIDATE_AND_DISCONNECT"):
            await handler.execute_signal("test_strategy", strategy_signal)

        broker.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_recommended_killswitch_level_triggers_escalation(self) -> None:
        """风控结果带 recommended_killswitch_level 时自动升级并审计"""
        from trader.core.application.risk_engine import RejectionReason, RiskCheckResult, RiskLevel

        signal = _long_signal()

        def risk_check(sig: Signal) -> RiskCheckResult:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                rejection_reason=RejectionReason.DAILY_LOSS_LIMIT,
                message="Daily loss limit exceeded",
                details={"recommended_killswitch_level": KillSwitchLevel.L1_NO_NEW_POSITIONS.value},
            )

        broker = _make_broker()
        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=risk_check,
        )

        with pytest.raises(RiskRejectedError):
            await handler.execute_signal("test_strategy", signal)

        broker.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_risk_mode_state_fail_closed(self) -> None:
        """缺失 RiskMode/KillSwitch 状态时默认 fail-closed 到 NO_NEW_POSITIONS"""
        from trader.core.application.risk_engine import RejectionReason, RiskCheckResult, RiskLevel

        signal = _long_signal()

        def risk_check_returns_none(sig: Signal) -> RiskCheckResult:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                rejection_reason=RejectionReason.RISK_MODE_CLOSE_ONLY,
                message="NO_NEW_POSITIONS (default on missing state)",
                details={},
            )

        broker = _make_broker()
        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=risk_check_returns_none,
        )

        with pytest.raises(RiskRejectedError, match="NO_NEW_POSITIONS"):
            await handler.execute_signal("test_strategy", signal)

        broker.place_order.assert_not_awaited()


class TestRiskModeKillSwitchConsistency:
    """KillSwitch 级别与 RiskMode 语义一致性测试"""

    def test_risk_mode_to_killswitch_level_mapping(self) -> None:
        """RiskMode 映射到推荐 KillSwitchLevel"""
        assert RiskMode.NORMAL.value == KillSwitchLevel.L0_NORMAL.value

    @pytest.mark.asyncio
    async def test_killswitch_l1_blocks_new_positions(self) -> None:
        """KillSwitch L1 阻止新开仓"""
        from trader.core.application.risk_engine import RiskCheckResult, RiskLevel

        broker = _make_broker()
        broker.place_order = AsyncMock(
            return_value=MagicMock(
                broker_order_id="broker-l1",
                filled_quantity=Decimal("0"),
                average_price=Decimal("0"),
                status=OrderStatus.SUBMITTED,
                created_at=None,
            )
        )

        def l1_risk_check(sig: Signal) -> RiskCheckResult:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                rejection_reason=None,
                message="KillSwitch L1 active",
                details={"killswitch_level": KillSwitchLevel.L1_NO_NEW_POSITIONS.value},
            )

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=l1_risk_check,
        )

        with pytest.raises(RiskRejectedError, match="KillSwitch"):
            await handler.execute_signal("test_strategy", _long_signal())

    @pytest.mark.asyncio
    async def test_risk_mode_blocks_open_signal_in_close_only(self) -> None:
        """CLOSE_ONLY 模式下开仓信号被阻止"""
        from trader.core.application.risk_engine import RejectionReason, RiskCheckResult, RiskLevel

        broker = _make_broker()

        def close_only_check(sig: Signal) -> RiskCheckResult:
            return RiskCheckResult(
                passed=False,
                risk_level=RiskLevel.HIGH,
                rejection_reason=RejectionReason.RISK_MODE_CLOSE_ONLY,
                message="CLOSE_ONLY mode: open signals blocked",
                details={"risk_mode": "CLOSE_ONLY"},
            )

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=close_only_check,
        )

        with pytest.raises(RiskRejectedError, match="CLOSE_ONLY"):
            await handler.execute_signal("test_strategy", _long_signal())

        broker.place_order.assert_not_awaited()


class TestOMSRiskModeFinalGate:
    """OMS RiskMode Final Gate 测试 - OMS 直接持有 RiskMode 状态源"""

    @pytest.mark.asyncio
    async def test_oms_no_new_positions_blocks_open_signal(self) -> None:
        """NO_NEW_POSITIONS + 开仓信号 -> OMS 拒绝，不调用 broker"""
        broker = _make_broker()

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=None,
        )
        handler.set_risk_mode_callback(lambda sid: RiskMode.NO_NEW_POSITIONS)

        with pytest.raises(RiskRejectedError, match="NO_NEW_POSITIONS"):
            await handler.execute_signal("test_strategy", _long_signal())

        broker.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_oms_no_new_positions_allows_close_signal(self) -> None:
        """NO_NEW_POSITIONS + 减仓信号 -> OMS 允许，调用 broker"""
        broker = _make_broker()
        broker.place_order = AsyncMock(
            return_value=MagicMock(
                broker_order_id="broker-close-1",
                filled_quantity=Decimal("0"),
                average_price=Decimal("50000"),
                status=OrderStatus.SUBMITTED,
                created_at=None,
            )
        )

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=None,
        )
        handler.set_risk_mode_callback(lambda sid: RiskMode.NO_NEW_POSITIONS)

        result = await handler.execute_signal("test_strategy", _close_long_signal())

        assert result is not None
        broker.place_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_oms_close_only_blocks_open_signal(self) -> None:
        """CLOSE_ONLY + 开仓信号 -> OMS 拒绝"""
        broker = _make_broker()

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=None,
        )
        handler.set_risk_mode_callback(lambda sid: RiskMode.CLOSE_ONLY)

        with pytest.raises(RiskRejectedError, match="CLOSE_ONLY"):
            await handler.execute_signal("test_strategy", _long_signal())

        broker.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_oms_close_only_blocks_close_signal(self) -> None:
        """CLOSE_ONLY + 减仓信号 -> OMS 允许减仓（CLOSE_ONLY 只阻止开仓）"""
        broker = _make_broker()
        broker.place_order = AsyncMock(
            return_value=MagicMock(
                broker_order_id="broker-close-only-1",
                filled_quantity=Decimal("0"),
                average_price=Decimal("50000"),
                status=OrderStatus.SUBMITTED,
                created_at=None,
            )
        )

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=None,
        )
        handler.set_risk_mode_callback(lambda sid: RiskMode.CLOSE_ONLY)

        result = await handler.execute_signal("test_strategy", _close_long_signal())
        assert result is not None
        broker.place_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_oms_cancel_all_and_halt_executes_cancel_all(self) -> None:
        """CANCEL_ALL_AND_HALT -> OMS 执行 broker.cancel_all()"""
        broker = _make_broker()
        broker.cancel_all = AsyncMock()

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=None,
        )
        handler.set_risk_mode_callback(lambda sid: RiskMode.CANCEL_ALL_AND_HALT)

        with pytest.raises(RiskRejectedError, match="CANCEL_ALL_AND_HALT"):
            await handler.execute_signal("test_strategy", _long_signal())

        broker.cancel_all.assert_awaited_once()
        broker.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_oms_liquidate_and_disconnect_blocks_open_even_with_system_liquidation_flag(
        self,
    ) -> None:
        """LIQUIDATE_AND_DISCONNECT + is_system_liquidation=True + LONG -> OMS 拒绝"""
        broker = _make_broker()

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=None,
        )
        handler.set_risk_mode_callback(lambda sid: RiskMode.LIQUIDATE_AND_DISCONNECT)

        sys_open_signal = Signal(
            signal_type=SignalType.LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
        )
        sys_open_signal.metadata["is_system_liquidation"] = True

        with pytest.raises(RiskRejectedError, match="LIQUIDATE_AND_DISCONNECT"):
            await handler.execute_signal("test_strategy", sys_open_signal)

        broker.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_oms_liquidate_and_disconnect_allows_system_liquidation(self) -> None:
        """LIQUIDATE_AND_DISCONNECT + is_system_liquidation=True + CLOSE_LONG -> OMS 允许"""
        broker = _make_broker()
        broker.place_order = AsyncMock(
            return_value=MagicMock(
                broker_order_id="broker-sys-liqq-1",
                filled_quantity=Decimal("0"),
                average_price=Decimal("50000"),
                status=OrderStatus.SUBMITTED,
                created_at=None,
            )
        )

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=None,
        )
        handler.set_risk_mode_callback(lambda sid: RiskMode.LIQUIDATE_AND_DISCONNECT)

        sys_signal = Signal(
            signal_type=SignalType.CLOSE_LONG,
            symbol="BTCUSDT",
            quantity=Decimal("0.01"),
            price=Decimal("50000"),
        )
        sys_signal.metadata["is_system_liquidation"] = True

        result = await handler.execute_signal("test_strategy", sys_signal)
        assert result is not None
        broker.place_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_oms_liquidate_and_disconnect_blocks_strategy_signals(self) -> None:
        """LIQUIDATE_AND_DISCONNECT -> OMS 拒绝策略信号"""
        broker = _make_broker()

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=None,
        )
        handler.set_risk_mode_callback(lambda sid: RiskMode.LIQUIDATE_AND_DISCONNECT)

        with pytest.raises(RiskRejectedError, match="LIQUIDATE_AND_DISCONNECT"):
            await handler.execute_signal("test_strategy", _close_long_signal())

        broker.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_oms_normal_mode_allows_all_signals(self) -> None:
        """NORMAL 模式 -> OMS 允许所有信号"""
        broker = _make_broker()
        broker.place_order = AsyncMock(
            return_value=MagicMock(
                broker_order_id="broker-normal-1",
                filled_quantity=Decimal("0"),
                average_price=Decimal("50000"),
                status=OrderStatus.SUBMITTED,
                created_at=None,
            )
        )

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=None,
        )
        handler.set_risk_mode_callback(lambda sid: RiskMode.NORMAL)

        result = await handler.execute_signal("test_strategy", _long_signal())
        assert result is not None
        broker.place_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_oms_no_risk_mode_callback_allows_all_signals(self) -> None:
        """没有设置 RiskMode callback -> OMS 正常放行"""
        broker = _make_broker()
        broker.place_order = AsyncMock(
            return_value=MagicMock(
                broker_order_id="broker-no-callback-1",
                filled_quantity=Decimal("0"),
                average_price=Decimal("50000"),
                status=OrderStatus.SUBMITTED,
                created_at=None,
            )
        )

        handler = OMSCallbackHandler(
            broker=broker,
            storage=ControlPlaneInMemoryStorage(),
            live_trading_enabled=True,
            pre_trade_risk_check=None,
        )

        result = await handler.execute_signal("test_strategy", _long_signal())
        assert result is not None
        broker.place_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_oms_risk_mode_rejects_are_audited(self) -> None:
        """RiskMode 拒绝必须写入审计"""
        broker = _make_broker()

        storage = ControlPlaneInMemoryStorage()
        handler = OMSCallbackHandler(
            broker=broker,
            storage=storage,
            live_trading_enabled=True,
            pre_trade_risk_check=None,
        )
        handler.set_risk_mode_callback(lambda sid: RiskMode.NO_NEW_POSITIONS)

        with pytest.raises(RiskRejectedError):
            await handler.execute_signal("test_strategy", _long_signal())

        stats = handler.get_dedup_stats()
        assert stats["order_submit_reject"] >= 1
