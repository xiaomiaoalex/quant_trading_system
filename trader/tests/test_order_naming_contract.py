"""Canonical internal order naming contract tests."""

from datetime import datetime, timezone
from decimal import Decimal

from trader.core.application.ports import BrokerOrder
from trader.core.application.replay_runner import StreamEvent, convert_stream_event_to_raw_update
from trader.core.domain.models.events import create_order_created_event
from trader.core.domain.models.order import Order, OrderSide, OrderStatus, OrderType


def _order() -> Order:
    return Order(
        order_id="order-1",
        cl_ord_id="cl-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=Decimal("2"),
        price=Decimal("100"),
        status=OrderStatus.SUBMITTED,
    )


def test_order_and_broker_dto_use_canonical_internal_field_names() -> None:
    order = _order()
    broker_order = BrokerOrder(
        broker_order_id="broker-1",
        cl_ord_id="cl-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=Decimal("2"),
        filled_qty=Decimal("0"),
        average_price=Decimal("0"),
        status=OrderStatus.SUBMITTED,
        created_at=datetime.now(timezone.utc),
    )

    assert order.cl_ord_id == broker_order.cl_ord_id == "cl-1"
    assert order.qty == broker_order.qty == Decimal("2")
    assert not hasattr(order, "client_order_id")
    assert not hasattr(order, "quantity")


def test_new_order_event_writes_v2_canonical_fields() -> None:
    event = create_order_created_event(_order())

    assert event.schema_version == 2
    assert event.data["cl_ord_id"] == "cl-1"
    assert event.data["qty"] == Decimal("2")
    assert "client_order_id" not in event.data
    assert "quantity" not in event.data


def test_replay_reads_legacy_order_field_names_but_normalizes_to_canonical_id() -> None:
    raw = convert_stream_event_to_raw_update(
        StreamEvent(
            event_id="event-1",
            stream_key="orders",
            seq=1,
            event_type="ORDER_CREATED",
            aggregate_id="aggregate-1",
            aggregate_type="Order",
            timestamp=datetime.now(timezone.utc),
            ts_ms=1,
            data={"client_order_id": "legacy-cl-1", "quantity": "2"},
            metadata={},
            schema_version=1,
        )
    )

    assert raw is not None
    assert raw.cl_ord_id == "legacy-cl-1"
