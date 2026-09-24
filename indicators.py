"""Dependency-free technical indicators (stdlib only), operating on lists of Bar."""
from __future__ import annotations

from statistics import mean, pstdev

from models import Bar


def closes(bars: list[Bar]) -> list[float]:
    return [b.close for b in bars]


def sma(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return mean(values[-period:])


def zscore(values: list[float], period: int) -> float | None:
    """z-score of the latest value vs. the trailing `period` window."""
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    sd = pstdev(window)
    if sd == 0:
        return 0.0
    return (window[-1] - mean(window)) / sd


def _window(bars: list[Bar], period: int, offset: int) -> list[Bar] | None:
    end = len(bars) - offset
    if period <= 0 or end < period:
        return None
    return bars[end - period:end]


def donchian_high(bars: list[Bar], period: int, offset: int = 1) -> float | None:
    """Highest high over `period` bars ending `offset` bars ago (offset=1 excludes the current bar)."""
    w = _window(bars, period, offset)
    return max(b.high for b in w) if w else None


def donchian_low(bars: list[Bar], period: int, offset: int = 1) -> float | None:
    w = _window(bars, period, offset)
    return min(b.low for b in w) if w else None


def avg_volume(bars: list[Bar], period: int, offset: int = 1) -> float | None:
    w = _window(bars, period, offset)
    return mean(b.volume for b in w) if w else None


def atr(bars: list[Bar], period: int) -> float | None:
    """Average True Range over `period` bars (simple average of true ranges)."""
    if period <= 0 or len(bars) < period + 1:
        return None
    trs = [
        max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        for prev, cur in zip(bars[-(period + 1):-1], bars[-period:])
    ]
    return mean(trs)
