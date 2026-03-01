"""
RiskEngine 分层风控测试
======================
验证 Pre/In/Post 插件接口与 Fail-Closed 行为。
"""
from decimal import Decimal

import pytest

from trader.adapters.broker.testing.fake_broker import FakeBroker
from trader.core.application.risk_engine import (
    RiskEngine,
    RiskCheckResult,
    RiskLevel,
    RejectionReason,
)
from trader.core.domain.models.signal import Signal, SignalType


class BlockPreTradePlugin:
    async def check(self, signal, metrics, engine):
        return RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.HIGH,
            rejection_reason=RejectionReason.MAX_POSITIONS,
            message="blocked by pre-trade plugin",
            details={"plugin": "pre"}
        )


class ExplodingPreTradePlugin:
    async def check(self, signal, metrics, engine):
        raise RuntimeError("plugin exploded")


class BlockInTradePlugin:
    async def check(self, context, engine):
        return RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.HIGH,
            rejection_reason=RejectionReason.CANCEL_RATE,
            message="blocked by in-trade plugin",
            details={"plugin": "in"}
        )


class BlockPostTradePlugin:
    async def check(self, context, engine):
        return RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.CRITICAL,
            rejection_reason=RejectionReason.MAX_DRAWDOWN,
            message="blocked by post-trade plugin",
            details={"plugin": "post"}
        )


def _build_buy_signal() -> Signal:
    return Signal(
        strategy_name="test",
        signal_type=SignalType.BUY,
        symbol="BTCUSDT",
        price=Decimal("50000"),
        quantity=Decimal("0.1")
    )


@pytest.mark.asyncio
async def test_check_signal_compatibility_uses_pre_trade():
    broker = FakeBroker()
    broker.set_balance(Decimal("10000"), Decimal("10000"))
    await broker.connect()

    engine = RiskEngine(broker)
    signal = _build_buy_signal()

    legacy = await engine.check_signal(signal)
    staged = await engine.check_pre_trade(signal)

    assert legacy.passed is True
    assert staged.passed is True
    assert legacy.details["recommended_killswitch_level"] == 0
    assert staged.details["recommended_killswitch_level"] == 0


@pytest.mark.asyncio
async def test_pre_trade_plugin_block_and_fail_closed():
    broker = FakeBroker()
    broker.set_balance(Decimal("10000"), Decimal("10000"))
    await broker.connect()

    blocked_engine = RiskEngine(broker, pre_trade_plugins=[BlockPreTradePlugin()])
    blocked = await blocked_engine.check_pre_trade(_build_buy_signal())
    assert blocked.passed is False
    assert blocked.rejection_reason == RejectionReason.MAX_POSITIONS
    assert blocked.details["recommended_killswitch_level"] == 1

    fail_closed_engine = RiskEngine(broker, pre_trade_plugins=[ExplodingPreTradePlugin()])
    fail_closed = await fail_closed_engine.check_pre_trade(_build_buy_signal())
    assert fail_closed.passed is False
    assert fail_closed.rejection_reason == RejectionReason.RISK_SYSTEM_ERROR
    assert fail_closed.details["recommended_killswitch_level"] == 3


@pytest.mark.asyncio
async def test_in_trade_and_post_trade_plugins_can_block():
    broker = FakeBroker()
    await broker.connect()

    engine = RiskEngine(
        broker,
        in_trade_plugins=[BlockInTradePlugin()],
        post_trade_plugins=[BlockPostTradePlugin()],
    )

    in_trade = await engine.check_in_trade({"event": "cancel-storm"})
    post_trade = await engine.check_post_trade({"event": "drawdown-spike"})

    assert in_trade.passed is False
    assert in_trade.rejection_reason == RejectionReason.CANCEL_RATE
    assert in_trade.details["recommended_killswitch_level"] == 1

    assert post_trade.passed is False
    assert post_trade.rejection_reason == RejectionReason.MAX_DRAWDOWN
    assert post_trade.details["recommended_killswitch_level"] == 2


def test_killswitch_level_mapping():
    engine = RiskEngine(FakeBroker())

    assert engine.recommend_killswitch_level(RiskCheckResult(passed=True)) == 0
    assert engine.recommend_killswitch_level(
        RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.HIGH,
            rejection_reason=RejectionReason.MAX_ORDER_RATE,
        )
    ) == 1
    assert engine.recommend_killswitch_level(
        RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.CRITICAL,
            rejection_reason=RejectionReason.MAX_DRAWDOWN,
        )
    ) == 2
    assert engine.recommend_killswitch_level(
        RiskCheckResult(
            passed=False,
            risk_level=RiskLevel.CRITICAL,
            rejection_reason=RejectionReason.RISK_SYSTEM_ERROR,
        )
    ) == 3
