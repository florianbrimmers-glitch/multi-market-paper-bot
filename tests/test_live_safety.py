"""Live-order safety: market hours, pending orders, protective stops, Alpaca payloads."""
from __future__ import annotations

import json

import httpx
import pytest

from broker.mock import MockBroker
from engine import run_tick
from engine.trader import ensure_protective_stops
from models import Order, OrderType, Position, Side
from tests import fixtures as fx


def _fetch(symbols):
    def fetch(inst):
        if inst.symbol in symbols:
            return fx.breakout_with_volume() if inst.symbol == "BTC/USD" else fx.flat_then_drop()
        return fx.flat_series()
    return fetch


@pytest.fixture(autouse=True)
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("DRY_RUN", "false")


def test_equities_not_traded_when_market_closed_but_crypto_is():
    b = MockBroker()
    b.market_open = False
    b.set_price("SPY", 90.0)
    b.set_price("BTC/USD", 110.0)
    recs = {r.symbol: r for r in run_tick(b, fetch=_fetch({"SPY", "BTC/USD"}), run_id="t")}
    assert recs["SPY"].action_taken == "skipped:market closed"
    assert recs["BTC/USD"].action_taken == "submitted"
    assert "SPY" not in b.get_positions()


def test_no_duplicate_entry_while_buy_is_pending():
    b = MockBroker()
    b.resting.append(Order(symbol="SPY", side=Side.BUY, qty=5))  # queued, not yet filled
    b.set_price("SPY", 90.0)
    spy = next(r for r in run_tick(b, fetch=_fetch({"SPY"}), run_id="t") if r.symbol == "SPY")
    assert spy.action_taken == "skipped:order pending"
    assert not [o for o in b.orders if o.side == Side.BUY]


def test_equity_entry_carries_attached_stop():
    b = MockBroker()
    b.set_price("SPY", 90.0)
    spy = next(r for r in run_tick(b, fetch=_fetch({"SPY"}), run_id="t") if r.symbol == "SPY")
    entry = next(o for o in b.orders if o.side == Side.BUY)
    assert entry.stop_loss == spy.plan.stop_price
    assert [o.type for o in b.resting] == [OrderType.STOP]


def test_crypto_entry_gets_stop_limit_after_fill():
    b = MockBroker()
    b.set_price("BTC/USD", 110.0)
    run_tick(b, fetch=_fetch({"BTC/USD"}), run_id="t")
    stops = [o for o in b.resting if o.symbol == "BTC/USD"]
    assert len(stops) == 1 and stops[0].type == OrderType.STOP_LIMIT
    assert stops[0].limit_price < stops[0].stop_price <= 110.0


def test_safety_net_protects_unprotected_position_once():
    b = MockBroker()
    positions = {"GLD": Position(symbol="GLD", qty=10, avg_entry_price=200.0)}
    assert ensure_protective_stops(b, positions, []) == ["GLD"]
    assert b.resting[0].stop_price == 198.0  # 1% hard cap
    assert ensure_protective_stops(b, positions, b.get_open_orders()) == []


# --- Alpaca adapter payloads (no network: httpx MockTransport) -------------
@pytest.fixture
def alpaca(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "secret")
    from broker.alpaca import AlpacaBroker
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "o1", "status": "accepted"})

    broker = AlpacaBroker()
    broker._client = httpx.Client(base_url=broker.base_url, transport=httpx.MockTransport(handler))
    return broker, sent


def test_alpaca_equity_entry_is_gtc_oto(alpaca):
    broker, sent = alpaca
    broker.submit_order(Order(symbol="SPY", side=Side.BUY, qty=10, stop_loss=99.0))
    assert sent[0]["time_in_force"] == "gtc"
    assert sent[0]["order_class"] == "oto"
    assert sent[0]["stop_loss"] == {"stop_price": "99.0"}


def test_alpaca_crypto_never_uses_oto(alpaca):
    broker, sent = alpaca
    broker.submit_order(Order(symbol="BTC/USD", side=Side.BUY, qty=0.1, stop_loss=99.0))
    assert "order_class" not in sent[0]
    broker.submit_order(Order(symbol="BTC/USD", side=Side.SELL, qty=0.1, type=OrderType.STOP_LIMIT,
                              stop_price=99.0, limit_price=98.5))
    assert sent[1]["type"] == "stop_limit" and sent[1]["limit_price"] == "98.5"
