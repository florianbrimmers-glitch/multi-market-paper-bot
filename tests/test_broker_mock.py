from __future__ import annotations

from broker.mock import MockBroker
from models import Order, OrderStatus, OrderType, Side


def test_buy_then_close_roundtrip():
    b = MockBroker(100_000.0)
    b.set_price("SPY", 100.0)
    o = b.submit_order(Order(symbol="SPY", side=Side.BUY, qty=10))
    assert o.status == OrderStatus.FILLED and o.filled_price == 100.0
    assert b.get_account().cash == 99_000.0
    b.set_price("SPY", 110.0)
    assert b.get_account().equity == 100_100.0
    assert b.close_position("SPY") is not None
    assert b.get_positions() == {}
    assert b.get_account().cash == 100_100.0


def test_stop_rests_until_triggered():
    b = MockBroker()
    b.set_price("SPY", 100.0)
    b.submit_order(Order(symbol="SPY", side=Side.BUY, qty=10))
    stop = b.submit_order(Order(symbol="SPY", side=Side.SELL, qty=10, type=OrderType.STOP, stop_price=99.0))
    assert stop.status == OrderStatus.NEW and "SPY" in b.get_positions()
    assert b.trigger_stops("SPY", bar_low=99.5) == []
    assert b.trigger_stops("SPY", bar_low=98.0) == [stop]
    assert stop.filled_price == 99.0 and b.get_positions() == {}


def test_close_cancels_resting_stop():
    b = MockBroker()
    b.set_price("SPY", 100.0)
    b.submit_order(Order(symbol="SPY", side=Side.BUY, qty=10))
    b.submit_order(Order(symbol="SPY", side=Side.SELL, qty=10, type=OrderType.STOP, stop_price=99.0))
    b.close_position("SPY")
    assert b.resting == []


def test_close_nothing_returns_none():
    assert MockBroker().close_position("SPY") is None


def test_crypto_symbol_normalised():
    b = MockBroker()
    b.set_price("BTC/USD", 50_000.0)
    b.submit_order(Order(symbol="BTC/USD", side=Side.BUY, qty=0.1))
    assert "BTCUSD" in b.get_positions()


def test_day_pl_baseline():
    b = MockBroker(100_000.0)
    b.mark_day_close()
    b.set_price("SPY", 100.0)
    b.submit_order(Order(symbol="SPY", side=Side.BUY, qty=10))
    b.set_price("SPY", 105.0)
    assert b.get_account().day_pl == 50.0
