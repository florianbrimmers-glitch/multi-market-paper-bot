"""In-memory broker for offline tests and the backtester. No network, deterministic.

Market orders fill at the current price. STOP / STOP_LIMIT orders rest until trigger_stops()
sees a low at or below the stop price — like a real broker, not instantly. An entry with
`stop_loss` attaches a resting stop once it fills (Alpaca OTO behaviour).
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

from broker.base import BrokerAdapter
from models import Account, Clock, Order, OrderStatus, OrderType, Position, Side


def _key(symbol: str) -> str:
    return symbol.replace("/", "")


class MockBroker(BrokerAdapter):
    def __init__(self, starting_equity: float = 100_000.0) -> None:
        self.cash = starting_equity
        self.last_equity = starting_equity
        self._positions: dict[str, Position] = {}
        self._prices: dict[str, float] = {}
        self._ids = itertools.count(1)
        self.orders: list[Order] = []
        self.resting: list[Order] = []
        self.market_open = True
        self.fixed_clock: Clock | None = None

    def set_price(self, symbol: str, price: float) -> None:
        k = _key(symbol)
        self._prices[k] = price
        pos = self._positions.get(k)
        if pos:
            pos.market_value = pos.qty * price
            pos.unrealized_pl = (price - pos.avg_entry_price) * pos.qty

    def get_account(self) -> Account:
        equity = self.cash + sum(p.qty * self._prices.get(s, 0.0) for s, p in self._positions.items())
        return Account(equity=equity, cash=self.cash, buying_power=self.cash, last_equity=self.last_equity)

    def get_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def get_open_orders(self) -> list[Order]:
        return list(self.resting)

    def clock(self) -> Clock:
        if self.fixed_clock is not None:
            return self.fixed_clock
        now = datetime.now(timezone.utc)
        return Clock(timestamp=now, is_open=self.market_open,
                     next_open=now + timedelta(hours=1), next_close=now + timedelta(hours=2))

    def _fill(self, order: Order, price: float) -> Order:
        k = _key(order.symbol)
        order.status, order.filled_price = OrderStatus.FILLED, price
        if order.side == Side.BUY:
            self.cash -= order.qty * price
            pos = self._positions.get(k)
            if pos:
                total = pos.qty + order.qty
                pos.avg_entry_price = (pos.avg_entry_price * pos.qty + price * order.qty) / total
                pos.qty = total
            else:
                self._positions[k] = Position(symbol=k, qty=order.qty, avg_entry_price=price)
        else:
            pos = self._positions.get(k)
            if pos:
                qty = min(order.qty, pos.qty)
                self.cash += qty * price
                pos.qty -= qty
                if pos.qty <= 1e-9:
                    del self._positions[k]
        self.set_price(order.symbol, price)
        return order

    def submit_order(self, order: Order) -> Order:
        order.id = str(next(self._ids))
        self.orders.append(order)
        if order.type in (OrderType.STOP, OrderType.STOP_LIMIT):
            self.resting.append(order)
            return order
        self._fill(order, self._prices.get(_key(order.symbol), 0.0))
        if order.stop_loss is not None:
            self.submit_order(Order(symbol=order.symbol, side=Side.SELL, qty=order.qty,
                                    type=OrderType.STOP, stop_price=order.stop_loss))
        return order

    def trigger_stops(self, symbol: str, bar_low: float) -> list[Order]:
        """Fill resting stops for `symbol` whose stop price was touched by `bar_low`."""
        hit = [o for o in self.resting if _key(o.symbol) == _key(symbol) and bar_low <= (o.stop_price or 0)]
        for o in hit:
            self.resting.remove(o)
            self._fill(o, o.stop_price)
        return hit

    def close_position(self, symbol: str) -> Order | None:
        k = _key(symbol)
        self.resting = [o for o in self.resting if _key(o.symbol) != k]
        pos = self._positions.get(k)
        if not pos:
            return None
        return self.submit_order(Order(symbol=symbol, side=Side.SELL, qty=pos.qty))

    def mark_day_close(self) -> None:
        self.last_equity = self.get_account().equity
