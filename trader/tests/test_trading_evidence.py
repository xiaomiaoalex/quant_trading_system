"""Order observations cannot stand in for economic execution facts."""

from decimal import Decimal

import pytest

from trader.tests.test_trading_service import bootstrap, command, make_service
from trader.trading.models import (
    ExchangeOrderResult,
    ExchangeOutcome,
    Execution,
    NotReady,
    OrderObservation,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,order_type",
    [("PARTIALLY_FILLED", "LIMIT"), ("FILLED", "MARKET"), ("CANCELLED", "LIMIT")],
)
async def test_unbooked_observations_block_until_real_facts_arrive(status, order_type):
    service = make_service()
    await bootstrap(service)
    receipt = await service.place(command(order_type=order_type))
    await service.observe(
        OrderObservation("acct", receipt.cl_ord_id, status, Decimal("0.5"), "b-1", 1_000_100)
    )
    assert "UNBOOKED_EXECUTIONS" in (await service.risk_view("acct")).reasons
    with pytest.raises(NotReady):
        await service.place(command(command_id="blocked"))
    state = await service.store.read("acct")
    assert state.balances["USDT"] == Decimal("1000")
    await service.record_execution(
        Execution("acct", receipt.cl_ord_id, "BTCUSDT", "e-1", Decimal("0.5"), Decimal("100"))
    )
    state = await service.store.read("acct")
    assert state.balances["USDT"] == Decimal("950")
    assert "UNBOOKED_EXECUTIONS" not in (await service.risk_view("acct")).reasons
    if status in {"FILLED", "CANCELLED"}:
        assert receipt.cl_ord_id not in state.holds


@pytest.mark.asyncio
async def test_stale_terminal_observation_does_not_release_active_order_hold():
    service = make_service()
    await bootstrap(service)
    receipt = await service.place(command(qty=Decimal("2")))
    await service.observe(
        OrderObservation(
            "acct", receipt.cl_ord_id, "PARTIALLY_FILLED", Decimal("1"), "b-1", 1_000_200
        )
    )
    await service.observe(
        OrderObservation("acct", receipt.cl_ord_id, "CANCELLED", Decimal("0"), "b-1", 1_000_100)
    )
    state = await service.store.read("acct")
    assert state.orders[receipt.cl_ord_id].status == "PARTIALLY_FILLED"
    assert state.holds[receipt.cl_ord_id].amount == Decimal("200.200")


@pytest.mark.asyncio
async def test_one_completed_order_cannot_clear_another_orders_missing_fills():
    service = make_service()
    await bootstrap(service)
    first = await service.place(command())
    second = await service.place(command(command_id="second"))
    await service.observe(
        OrderObservation("acct", first.cl_ord_id, "FILLED", Decimal("1"), "b-1", 1_000_100)
    )
    await service.record_execution(
        Execution("acct", second.cl_ord_id, "BTCUSDT", "e-2", Decimal("1"), Decimal("100"))
    )
    assert not (await service.risk_view("acct")).ready
    assert "UNBOOKED_EXECUTIONS" in (await service.risk_view("acct")).reasons


@pytest.mark.asyncio
async def test_limit_non_gtc_is_rejected_before_persisting_or_sending():
    service = make_service()
    await bootstrap(service)
    with pytest.raises(ValueError, match="UNSUPPORTED_TIME_IN_FORCE"):
        await service.place(command(time_in_force="IOC"))
    assert not (await service.store.read("acct")).orders
    assert not service.exchange.placed


@pytest.mark.asyncio
async def test_rest_place_response_cumulative_fill_blocks_without_booking():
    service = make_service()
    await bootstrap(service)
    service.exchange.place_outcome = ExchangeOrderResult(
        ExchangeOutcome.ACCEPTED, "b-1", "PARTIALLY_FILLED", cumulative_qty=Decimal("0.5")
    )
    await service.place(command())
    assert "UNBOOKED_EXECUTIONS" in (await service.risk_view("acct")).reasons
    ledger = (await service.store.read("acct")).ledger
    assert [entry for entry in ledger if entry["kind"] in {"trade", "fee"}] == []


@pytest.mark.asyncio
async def test_complete_order_does_not_clear_other_filled_order_mismatch():
    service = make_service()
    await bootstrap(service)
    first = await service.place(command())
    second = await service.place(command(command_id="second"))
    for receipt in (first, second):
        await service.observe(
            OrderObservation("acct", receipt.cl_ord_id, "FILLED", Decimal("1"), "b-1", 1_000_100)
        )
    await service.record_execution(
        Execution("acct", second.cl_ord_id, "BTCUSDT", "e-2", Decimal("1"), Decimal("100"))
    )
    assert "FILL_OBSERVATION_MISMATCH" in (await service.risk_view("acct")).reasons


@pytest.mark.asyncio
async def test_late_fill_is_booked_but_conflicting_cancel_total_blocks_until_corrected():
    service = make_service()
    await bootstrap(service)
    receipt = await service.place(command())
    await service.observe(
        OrderObservation("acct", receipt.cl_ord_id, "CANCELLED", Decimal("0"), "b-1", 1_000_100)
    )
    await service.record_execution(
        Execution("acct", receipt.cl_ord_id, "BTCUSDT", "late", Decimal("0.5"), Decimal("100"))
    )
    state = await service.store.read("acct")
    assert state.orders[receipt.cl_ord_id].status == "CANCELLED"
    assert state.balances["BTC"] == Decimal("0.5")
    assert "TERMINAL_EXECUTION_MISMATCH" in (await service.risk_view("acct")).reasons
    await service.observe(
        OrderObservation("acct", receipt.cl_ord_id, "CANCELLED", Decimal("0.5"), "b-1", 1_000_200)
    )
    assert "TERMINAL_EXECUTION_MISMATCH" not in (await service.risk_view("acct")).reasons


@pytest.mark.asyncio
async def test_unvalued_asset_blocks_even_without_exposure_limits():
    service = make_service()
    await bootstrap(service, balances={"USDT": Decimal("1000"), "BNB": Decimal("1")})
    assert "VALUATION_INCOMPLETE" in (await service.risk_view("acct")).reasons
    with pytest.raises(NotReady):
        await service.place(command())


@pytest.mark.asyncio
async def test_nonfinite_mark_is_incomplete_instead_of_raising_decimal_error():
    service = make_service()
    await bootstrap(
        service,
        balances={"USDT": Decimal("1000"), "BTC": Decimal("1")},
        marks={"BTCUSDT": {"price": "NaN", "at_ms": 1_000_000}},
    )
    assert "VALUATION_INCOMPLETE" in (await service.risk_view("acct")).reasons
