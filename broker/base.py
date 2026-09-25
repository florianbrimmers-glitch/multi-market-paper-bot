"""Broker abstraction: the engine only talks to this, so Alpaca paper and the mock are interchangeable."""
from __future__ import annotations

from abc import ABC, abstractmethod

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

    @abstractmethod
    def submit_order(self, order: Order) -> Order: ...

    @abstractmethod
    def close_position(self, symbol: str) -> Order | None:
        """Flatten a position (and cancel its resting orders); None if nothing held."""
