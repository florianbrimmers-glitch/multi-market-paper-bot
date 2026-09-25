"""Momentum breakout — Bitcoin on the 1-hour chart.

Enter when the close breaks the Donchian channel high on heavy volume (filters fakeouts);
exit on a trailing Donchian-low break.
"""
from __future__ import annotations

from indicators import avg_volume, donchian_high, donchian_low
from models import Action, Bar, Signal

from .base import Strategy


class MomentumBreakout(Strategy):
    name = "momentum_breakout"

    def generate_signal(self, symbol: str, bars: list[Bar], in_position: bool) -> Signal:
        p = self.params
        sig = Signal(symbol=symbol, strategy=self.name)
        if not bars:
            sig.reason = "keine Daten"
            return sig
        last = bars[-1]
        sig.ref_price = last.close

        if in_position:
            exit_level = donchian_low(bars, p.mb_exit_lookback)
            if exit_level is not None and last.close < exit_level:
                sig.action = Action.EXIT
                sig.reason = f"unter das {p.mb_exit_lookback}-Kerzen-Tief {exit_level:.2f} gefallen — Schwung weg"
            else:
                sig.reason = "Ausbruch hält"
            return sig

        channel = donchian_high(bars, p.mb_channel_lookback)
        avg_vol = avg_volume(bars, p.mb_vol_lookback)
        if channel is None or not avg_vol:
            sig.reason = "zu wenig Kurshistorie"
            return sig

        ratio = last.volume / avg_vol
        if last.close > channel and ratio >= p.mb_vol_mult:
            sig.action = Action.ENTER_LONG
            sig.reason = f"Ausbruch über {channel:.2f} bei {ratio:.1f}-fachem Volumen"
        elif last.close > channel:
            sig.reason = f"über {channel:.2f}, aber schwaches Volumen ({ratio:.1f}x) — vermutlich Fehlausbruch"
        else:
            sig.reason = f"unter der Ausbruchsmarke {channel:.2f}"
        return sig
