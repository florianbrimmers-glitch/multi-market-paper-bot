"""Risk engine: hard stop cap, volatility-based sizing, correlation filter.

1. Stop distance = ATR_STOP_MULT * ATR, never more than MAX_STOP_PCT (default 1%) from entry.
2. qty = (equity * RISK_PER_TRADE_PCT) / stop distance, capped at MAX_POSITION_PCT of equity
   in notional and by available buying power.
3. Two instruments in the same correlation group (SPY & QQQ) can't both be long.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import config
from indicators import atr
from models import Account, Bar, Position, Side, TradePlan


@dataclass
class RiskDecision:
    plan: TradePlan | None
    reason: str

    @property
    def approved(self) -> bool:
        return self.plan is not None


def stop_distance(entry_price: float, bars: list[Bar]) -> float:
    hard_cap = entry_price * config.max_stop_pct()
    a = atr(bars, config.STRATEGY_PARAMS.atr_period)
    if not a or a <= 0:
        return hard_cap
    return min(a * config.atr_stop_mult(), hard_cap)


def size_entry(
    symbol: str,
    entry_price: float,
    bars: list[Bar],
    account: Account,
    positions: dict[str, Position],
    correlation_group: str | None = None,
    group_of: dict[str, str | None] | None = None,
    allow_fractional: bool = False,
) -> RiskDecision:
    """Turn a raw ENTER_LONG signal into a risk-approved TradePlan, or reject it."""
    group_of = group_of or {}

    if symbol in positions and positions[symbol].is_long:
        return RiskDecision(None, "already long")

    if correlation_group:
        for held, pos in positions.items():
            if held != symbol and pos.is_long and group_of.get(held) == correlation_group:
                return RiskDecision(None, f"correlation filter: {held} already long ({correlation_group})")

    if entry_price <= 0:
        return RiskDecision(None, "invalid entry price")

    dist = stop_distance(entry_price, bars)
    qty = account.equity * config.risk_per_trade_pct() / dist
    qty = min(qty, account.equity * config.max_position_pct() / entry_price,
              account.buying_power / entry_price)
    if not allow_fractional:
        qty = math.floor(qty)
    if qty <= 0:
        return RiskDecision(None, "position size rounds to zero")

    return RiskDecision(
        TradePlan(
            symbol=symbol, side=Side.BUY, qty=qty, entry_price=entry_price,
            stop_price=round(entry_price - dist, 2), risk_amount=qty * dist,
            reason=f"risk {config.risk_per_trade_pct() * 100:.2f}% of equity, "
                   f"stop {dist / entry_price * 100:.2f}% away",
        ),
        "approved",
    )
