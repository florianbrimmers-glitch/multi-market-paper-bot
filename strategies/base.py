"""Strategy contract. Live engine and backtester call generate_signal() on the same objects."""
from __future__ import annotations

from abc import ABC, abstractmethod

from config import StrategyParams
from models import Bar, Signal


class Strategy(ABC):
    name: str = "base"

    def __init__(self, params: StrategyParams) -> None:
        self.params = params

    @abstractmethod
    def generate_signal(self, symbol: str, bars: list[Bar], in_position: bool) -> Signal:
        """`bars` is chronological; the last element is the most recent closed bar.
        Must be a pure function of its inputs (no I/O) so it backtests cleanly."""
        raise NotImplementedError
