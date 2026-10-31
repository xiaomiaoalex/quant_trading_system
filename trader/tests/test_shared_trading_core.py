"""BAR and live admission/settlement must use the same economic rules."""

from decimal import Decimal

import pytest

from trader.tests.test_research_runtime import bars, dataset
from trader.trading.backtest import EventBacktestRuntime, StrategySignal


@pytest.mark.parametrize(
    "config,reason",
    [
        ({"max_gross_notional": "50"}, "GROSS_EXPOSURE_LIMIT"),
        ({"max_symbol_notional": "50"}, "SYMBOL_EXPOSURE_LIMIT"),
    ],
)
def test_bar_executes_frozen_risk_policy_limits(config, reason):
    result = EventBacktestRuntime(
        datasets=[dataset()],
        strategy=lambda context, bar: [StrategySignal("BTCUSDT", "BUY", Decimal("1"))],
        config=config,
    ).run()
    assert result.fills == []
    assert result.rejections == {reason: 4}


def test_bar_rejects_unknown_risk_policy_fields():
    with pytest.raises(ValueError, match="Unknown risk policy fields"):
        EventBacktestRuntime(
            datasets=[dataset()],
            strategy=lambda context, bar: [],
            config={"max_gross_notionl": "50"},
        )


def test_pending_buy_consumes_the_shared_portfolio_limit():
    def strategy(context, bar):
        if context.bar_index == 0:
            return [
                StrategySignal("BTCUSDT", "BUY", Decimal("1")),
                StrategySignal("BTCUSDT", "BUY", Decimal("1")),
            ]
        return []

    result = EventBacktestRuntime(
        datasets=[dataset()], strategy=strategy, config={"max_gross_notional": "150"}
    ).run()
    assert len(result.fills) == 1
    assert result.rejections == {"GROSS_EXPOSURE_LIMIT": 1}


@pytest.mark.asyncio
async def test_bar_and_live_have_identical_ledger_after_same_buy_and_sell():
    from trader.tests.test_trading_service import bootstrap, command, make_service
    from trader.trading.models import Execution

    def strategy(context, bar):
        if context.bar_index == 0:
            return [StrategySignal("BTCUSDT", "BUY", Decimal("1"))]
        if context.bar_index == 1:
            return [StrategySignal("BTCUSDT", "SELL", Decimal("1"))]
        return []

    runtime = EventBacktestRuntime(
        datasets=[dataset()], strategy=strategy, initial_cash=Decimal("1000")
    )
    result = runtime.run()
    live = make_service()
    await bootstrap(live)
    for index, side in enumerate(("BUY", "SELL")):
        receipt = await live.place(command(command_id=str(index), side=side))
        await live.record_execution(
            Execution(
                "acct",
                receipt.cl_ord_id,
                "BTCUSDT",
                str(index),
                Decimal("1"),
                Decimal("100"),
                Decimal("0.1"),
                "USDT",
            )
        )
    state = await live.store.read("acct")
    assert runtime.account.balances == state.balances
    assert runtime.account.positions == state.positions
    assert [(item["asset"], Decimal(item["delta"])) for item in runtime.account.ledger] == [
        (item["asset"], Decimal(item["delta"])) for item in state.ledger
    ]
    assert runtime.account.holds == state.holds == {}
    assert result.net_value == Decimal("999.8")
    live_fees = -sum(
        (Decimal(item["delta"]) for item in state.ledger if item["kind"] == "fee"), Decimal(0)
    )
    assert result.total_fees == live_fees == Decimal("0.2")
    assert result.net_value == state.balances["USDT"] + state.balances["BTC"] * Decimal("100")


def test_shared_risk_uses_historical_event_clock_for_freshness(monkeypatch):
    monkeypatch.setattr("time.time", lambda: 9_999_999_999)

    def strategy(context, bar):
        return [StrategySignal("BTCUSDT", "BUY", Decimal("1"))]

    result = EventBacktestRuntime(
        datasets=[dataset()],
        strategy=strategy,
        config={"max_mark_age_ms": "1", "max_reconciliation_age_ms": "1"},
    ).run()
    assert len(result.fills) == 3
    assert result.rejections == {}


def test_closed_positions_release_shared_limit_for_reentry():
    def strategy(context, bar):
        side = "SELL" if context.bar_index == 1 else "BUY"
        return [StrategySignal("BTCUSDT", side, Decimal("1"))]

    result = EventBacktestRuntime(
        datasets=[dataset()], strategy=strategy, config={"max_gross_notional": "100"}
    ).run()
    assert [fill["side"] for fill in result.fills] == ["BUY", "SELL", "BUY"]
    assert result.rejections == {"GROSS_EXPOSURE_LIMIT": 1}


@pytest.mark.asyncio
async def test_partial_base_fee_executions_match_live_and_replay_once():
    from trader.tests.test_trading_service import bootstrap, command, make_service
    from trader.trading import core
    from trader.trading.models import Execution

    live = make_service()
    await bootstrap(live)
    account = await live.store.read("acct")
    request = command(qty=Decimal("2"))
    receipt = await live.place(request)
    core.reserve_order(account, request, receipt.cl_ord_id, live.policy, live.clock())
    facts = [
        Execution(
            "acct",
            receipt.cl_ord_id,
            "BTCUSDT",
            "one",
            Decimal("0.5"),
            Decimal("100"),
            Decimal("0.001"),
            "BTC",
        ),
        Execution(
            "acct",
            receipt.cl_ord_id,
            "BTCUSDT",
            "two",
            Decimal("0.5"),
            Decimal("110"),
            Decimal("0.055"),
            "USDT",
        ),
    ]
    for execution in facts:
        assert core.book_execution(account, execution, live.policy)
        assert await live.record_execution(execution)
        state = await live.store.read("acct")
        assert account.balances == state.balances
        assert account.positions == state.positions
        assert account.holds == state.holds
        assert account.ledger == state.ledger
        assert (
            account.orders[receipt.cl_ord_id].booked_qty
            == state.orders[receipt.cl_ord_id].booked_qty
        )
        assert core.book_execution(account, execution, live.policy) is False
        assert await live.record_execution(execution) is False
    assert account.balances == {"USDT": Decimal("894.945"), "BTC": Decimal("0.999")}
    assert account.positions["BTCUSDT"] == Decimal("0.999")
    assert account.balances["USDT"] + account.balances["BTC"] * Decimal("110") == Decimal(
        "1004.835"
    )


@pytest.mark.asyncio
async def test_bar_price_change_and_sale_nav_match_live_replayed_fills():
    from trader.tests.test_trading_service import bootstrap, command, make_service
    from trader.trading.models import Execution

    values = bars(3)
    values[1].update(high="110", close="110")
    values[2].update(open="110", high="110", low="110", close="110")

    def strategy(context, bar):
        if context.bar_index in {0, 1}:
            side = "BUY" if context.bar_index == 0 else "SELL"
            return [StrategySignal("BTCUSDT", side, Decimal("1"))]
        return []

    runtime = EventBacktestRuntime(
        datasets=[dataset(values)], strategy=strategy, initial_cash=Decimal("1000")
    )
    result = runtime.run()
    live = make_service()
    await bootstrap(live)
    assert result.equity_curve == [Decimal("1000"), Decimal("1009.9"), Decimal("1009.79")]
    for index, fill in enumerate(result.fills):
        receipt = await live.place(
            command(command_id=str(index), side=fill["side"], qty=fill["qty"], price=fill["price"])
        )
        await live.record_execution(
            Execution(
                "acct",
                receipt.cl_ord_id,
                "BTCUSDT",
                str(index),
                fill["qty"],
                fill["price"],
                fill["fee"],
                "USDT",
            )
        )
        state = await live.store.read("acct")
        nav = state.balances["USDT"] + state.balances["BTC"] * Decimal("110")
        assert nav == result.equity_curve[index + 1]
    assert runtime.account.balances == state.balances
    assert result.net_value == Decimal("1009.79")
    assert result.total_fees == Decimal("0.21")
