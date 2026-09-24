from __future__ import annotations

from .base import BrokerAdapter
from .mock import MockBroker

__all__ = ["BrokerAdapter", "MockBroker", "AlpacaBroker"]


def __getattr__(name: str):
    # Lazy so tests/backtests never need httpx or Alpaca credentials.
    if name == "AlpacaBroker":
        from .alpaca import AlpacaBroker
        return AlpacaBroker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
