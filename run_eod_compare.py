"""Compare the close-before-the-bell variants for mean reversion on recent Alpaca history.

  python run_eod_compare.py

Same strategy, risk and session rules as live; stops that gap fill at the open. Paper only —
past behaviour is no prediction of future returns.
"""
from __future__ import annotations

import config
import i18n
from data import fetch_bars
from engine import backtest_symbol

MODES = {"off": "über Nacht halten", "winners": "nur Gewinner glattstellen", "all": "alle glattstellen"}


def main() -> None:
    insts = [i for i in config.INSTRUMENTS if i.strategy == "mean_reversion"]
    totals = {m: 0.0 for m in MODES}
    for inst in insts:
        bars = fetch_bars(inst.symbol, inst.asset_class, inst.timeframe, limit=10000)
        days = len({b.timestamp.date() for b in bars})
        print(f"\n**{inst.symbol}** ({len(bars)} Kerzen, {days} Tage)")
        for mode, label in MODES.items():
            r = backtest_symbol(inst.symbol, inst.strategy, bars, eod_mode=mode, bar_minutes=15)
            pl = r.ending_equity - r.starting_equity
            totals[mode] += pl
            print(f"- {label}: {r.num_trades} Trades, Trefferquote {i18n.num(r.win_rate, 0)} %, "
                  f"Ergebnis {i18n.signed_usd(pl)}, max. Rückgang {i18n.num(r.max_drawdown_pct)} %")
    print("\n**Summe über alle vier Werte**")
    for mode, label in MODES.items():
        print(f"- {label}: {i18n.signed_usd(totals[mode])}")
    print("\nPapiergeld/Backtest — keine Vorhersage künftiger Ergebnisse.")


if __name__ == "__main__":
    main()
