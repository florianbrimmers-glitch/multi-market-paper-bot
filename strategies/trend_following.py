"""Trend following — Gold & Oil on the 4-hour chart.

Long while the fast MA is above the slow MA; exit when it crosses back below.
"""
from __future__ import annotations

from indicators import closes, sma
from models import Action, Bar, Signal

from .base import Strategy


class TrendFollowing(Strategy):
    name = "trend_following"

    def generate_signal(self, symbol: str, bars: list[Bar], in_position: bool) -> Signal:
        p = self.params
        sig = Signal(symbol=symbol, strategy=self.name)
        cs = closes(bars)
        fast, slow = sma(cs, p.tf_fast_ma), sma(cs, p.tf_slow_ma)
        if fast is None or slow is None:
            sig.reason = "insufficient history"
            return sig
        sig.ref_price = bars[-1].close
        uptrend = fast > slow

        if in_position:
            if not uptrend:
                sig.action = Action.EXIT
                sig.reason = f"fast MA {fast:.2f} crossed below slow MA {slow:.2f}"
            else:
                sig.reason = f"riding trend (fast {fast:.2f} > slow {slow:.2f})"
        elif uptrend:
            sig.action = Action.ENTER_LONG
            sig.reason = f"uptrend (fast {fast:.2f} > slow {slow:.2f})"
        else:
            sig.reason = f"no uptrend (fast {fast:.2f} <= slow {slow:.2f})"
        return sig
