"""Test OrderBook import and instantiation"""
import pytest

from trader.core.domain.models.orderbook import OrderBook


def test_orderbook_import():
    """Test that OrderBook can be imported"""
    assert OrderBook is not None


def test_orderbook_creation():
    """Test OrderBook creation with symbol"""
    ob = OrderBook(symbol='BTCUSDT')
    assert ob.symbol == 'BTCUSDT'
    assert ob.bids == []
    assert ob.asks == []
    assert ob.timestamp is None
