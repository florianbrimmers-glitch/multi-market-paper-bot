"""Synthetic bar builders with known, hand-computable properties."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models import Bar

_T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def bar(close: float, i: int, high: float | None = None, low: float | None = None,
        volume: float = 1000.0) -> Bar:
    return Bar(timestamp=_T0 + timedelta(hours=i), open=close,
               high=close if high is None else high, low=close if low is None else low,
               close=close, volume=volume)


def flat_series(n: int = 30, level: float = 100.0) -> list[Bar]:
    return [bar(level, i, high=level + 0.1, low=level - 0.1) for i in range(n)]


def flat_then_drop(n_flat: int = 25, level: float = 100.0, drop_to: float = 90.0) -> list[Bar]:
    """Flat series then a sharp drop -> strongly negative z-score."""
    return flat_series(n_flat, level) + [bar(drop_to, n_flat, high=level, low=drop_to - 0.5)]


def _channel(n: int, base: float) -> list[Bar]:
    return [bar(base + (i % 3) * 0.2, i, high=base + 1.0, low=base - 1.0) for i in range(n)]


def breakout_with_volume(n_base: int = 25, base: float = 100.0, breakout: float = 110.0) -> list[Bar]:
    return _channel(n_base, base) + [bar(breakout, n_base, high=breakout + 0.5, low=base, volume=5000.0)]


def breakout_no_volume(n_base: int = 25, base: float = 100.0, breakout: float = 110.0) -> list[Bar]:
    return _channel(n_base, base) + [bar(breakout, n_base, high=breakout + 0.5, low=base, volume=1000.0)]


def uptrend(n: int = 80, start: float = 100.0, step: float = 1.0) -> list[Bar]:
    return [bar(start + i * step, i, high=start + i * step + 0.5, low=start + i * step - 0.5)
            for i in range(n)]


def downtrend(n: int = 80, start: float = 200.0, step: float = 1.0) -> list[Bar]:
    return [bar(start - i * step, i, high=start - i * step + 0.5, low=start - i * step - 0.5)
            for i in range(n)]
