"""Simple test for OrderBook"""
import pytest
from decimal import Decimal

from trader.core.domain.models.orderbook import OrderBook, OrderBookLevel


def test_orderbook_creation():
    """Simple test"""
    ob = OrderBook(symbol="BTCUSDT")
    assert ob.symbol == "BTCUSDT"


def test_orderbook_with_levels():
    """Test with levels"""
    ob = OrderBook(
        symbol="BTCUSDT",
        bids=[OrderBookLevel(price=Decimal("100.0"), quantity=Decimal("10"))],
        asks=[OrderBookLevel(price=Decimal("100.1"), quantity=Decimal("10"))]
    )
    assert ob.symbol == "BTCUSDT"
    assert len(ob.bids) == 1
    assert len(ob.asks) == 1
    assert ob.bids[0].price == Decimal("100.0")
    assert ob.bids[0].quantity == Decimal("10")
    assert ob.asks[0].price == Decimal("100.1")
    assert ob.asks[0].quantity == Decimal("10")
