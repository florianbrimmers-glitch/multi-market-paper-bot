"""Strategy registry — maps the config `strategy` key to a Strategy instance."""
from __future__ import annotations

from config import StrategyParams

from .base import Strategy
from .mean_reversion import MeanReversion
from .momentum_breakout import MomentumBreakout
from .trend_following import TrendFollowing

__all__ = ["Strategy", "MeanReversion", "MomentumBreakout", "TrendFollowing", "build_strategy"]

_REGISTRY: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (MeanReversion, MomentumBreakout, TrendFollowing)
}


def build_strategy(name: str, params: StrategyParams | None = None) -> Strategy:
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise ValueError(f"Unknown strategy: {name!r}. Known: {sorted(_REGISTRY)}") from None
    return cls(params or StrategyParams())
