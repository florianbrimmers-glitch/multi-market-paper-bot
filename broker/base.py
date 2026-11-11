"""Broker abstraction: the engine only talks to this, so Alpaca paper and the mock are interchangeable."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta

from models import Account, Clock, Order, Position


class BrokerAdapter(ABC):
    @abstractmethod
    def get_account(self) -> Account: ...

    @abstractmethod
    def get_positions(self) -> dict[str, Position]:
        """Open positions keyed by position symbol (e.g. 'BTCUSD')."""

    @abstractmethod
    def get_open_orders(self) -> list[Order]:
        """Orders not yet filled or cancelled (queued entries, resting stops)."""

    @abstractmethod
    def clock(self) -> Clock:
        """US equity session clock (crypto trades 24/7 regardless)."""

    def is_market_open(self) -> bool:
        return self.clock().is_open

    def trading_days(self, start: date, end: date) -> list[date]:
        """Exchange sessions from start to end, both included. Default: every weekday (a broker
        with a calendar leaves out the holidays)."""
        days = (start + timedelta(days=i) for i in range((end - start).days + 1))
        return [d for d in days if d.weekday() < 5]

    @abstractmethod
    def submit_order(self, order: Order) -> Order: ...

    @abstractmethod
    def close_position(self, symbol: str) -> Order | None:
        """Flatten a position (and cancel its resting orders); None if nothing held."""

    def last_sell_fill(self, symbol: str, since: datetime | None = None,
                       wait: float = 0.0) -> tuple[float, float] | None:
        """(qty, avg price) of the most recent filled sell of `symbol`, if the broker reports it.
        `since` ignores older fills; `wait` gives a just-sent order a few seconds to fill."""
        return None
