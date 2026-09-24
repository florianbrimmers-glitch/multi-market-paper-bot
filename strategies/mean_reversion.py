"""Mean reversion — SPY & QQQ on 15-minute candles.

Go long when price stretches far below its recent mean (z-score <= entry_z); exit once it
reverts to the mean (z-score >= exit_z). Long-only.
"""
from __future__ import annotations

from indicators import closes, zscore
from models import Action, Bar, Signal

from .base import Strategy


class MeanReversion(Strategy):
    name = "mean_reversion"

    def generate_signal(self, symbol: str, bars: list[Bar], in_position: bool) -> Signal:
        p = self.params
        sig = Signal(symbol=symbol, strategy=self.name)
        z = zscore(closes(bars), p.mr_lookback)
        if z is None:
            sig.reason = "insufficient history"
            return sig
        sig.ref_price = bars[-1].close

        if in_position:
            if z >= p.mr_exit_z:
                sig.action = Action.EXIT
                sig.reason = f"reverted to mean (z={z:.2f})"
            else:
                sig.reason = f"holding, still below mean (z={z:.2f})"
        elif z <= p.mr_entry_z:
            sig.action = Action.ENTER_LONG
            sig.reason = f"stretched below mean (z={z:.2f}) — snapback long"
        else:
            sig.reason = f"no stretch (z={z:.2f})"
        return sig
