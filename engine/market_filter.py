"""Market filter: how far SPY has moved since today's US open.

On a broad sell-off every instrument looks oversold at once and mean reversion buys all of them
into the same move (2026-10-14: eight entries at 16:00, six stopped out). When SPY is down more
than MARKET_FILTER_PCT from the open, new mean-reversion entries are bought at half size.
"""
from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, time
from zoneinfo import ZoneInfo

from models import Bar

_NY = ZoneInfo("America/New_York")
_OPEN = time(9, 30)


class MarketMove:
    """SPY's percent move from the day's 09:30 open up to a given time, from 15-min bars."""

    def __init__(self, market_bars: list[Bar]):
        self._bars = market_bars
        self._starts = [b.timestamp.timestamp() for b in market_bars]
        self._opens: dict = {}
        for b in market_bars:
            t = b.timestamp.astimezone(_NY)
            if t.time() == _OPEN:
                self._opens[t.date()] = b.open

    def pct(self, at: datetime, price: float | None = None) -> float | None:
        """Move in % at `at`, from the last bar that started strictly before `at` (no look-ahead),
        or vs `price` if given."""
        day_open = self._opens.get(at.astimezone(_NY).date())
        if not day_open:
            return None
        if price is None:
            i = bisect_left(self._starts, at.timestamp()) - 1
            if i < 0 or self._bars[i].timestamp.astimezone(_NY).date() != at.astimezone(_NY).date():
                return None
            price = self._bars[i].close
        return (price / day_open - 1) * 100


def size_factor(move_pct: float | None, threshold_pct: float) -> float:
    """0.5 when the market is down more than the threshold since the open, else 1.0."""
    if threshold_pct <= 0 or move_pct is None:
        return 1.0
    return 0.5 if move_pct <= -threshold_pct else 1.0
