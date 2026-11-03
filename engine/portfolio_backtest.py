"""Mean reversion on several markets at once, sharing one account — unlike backtest_symbol, this
models buying power and total exposure, so caps on leverage can be compared."""
from __future__ import annotations

from dataclasses import dataclass, field

import config
from engine.backtest import Trade, _session_minutes
from models import Account, Action, Bar, Position
from risk import size_entry
from strategies import build_strategy

COOLDOWN_BARS = 6


@dataclass
class PortfolioResult:
    trades: list[Trade] = field(default_factory=list)
    equity_by_day: dict[str, float] = field(default_factory=dict)
    overnight_exposure: dict[str, float] = field(default_factory=dict)  # day -> gross long / equity at the close
    smaller_entries: int = 0  # entries cut below their risk size by the exposure cap


def backtest_portfolio(data: dict[str, list[Bar]], max_exposure: float, starting_equity: float = 100_000.0,
                       group_of: dict[str, str | None] | None = None, late_exposure: float | None = None,
                       late_minutes: float = 120) -> PortfolioResult:
    """max_exposure: gross long value allowed, as a multiple of equity (Alpaca intraday ~4).
    late_exposure: a lower cap for entries in the last `late_minutes` of the session — those are
    the positions most likely to be held overnight."""
    group_of = group_of or {}
    strat = build_strategy("mean_reversion", config.STRATEGY_PARAMS)
    res = PortfolioResult()
    cash = starting_equity
    pos: dict[str, list] = {}  # sym -> [qty, entry, stop, Trade]
    last: dict[str, float] = {}
    idx = {s: 0 for s in data}
    last_stop = {s: -10**9 for s in data}
    events = sorted({b.timestamp for bars in data.values() for b in bars})

    def equity() -> float:
        return cash + sum(p[0] * last[s] for s, p in pos.items())

    for ts in events:
        for sym, bars in data.items():
            i = idx[sym]
            if i >= len(bars) or bars[i].timestamp != ts:
                continue
            idx[sym] = i + 1
            bar = bars[i]
            session = _session_minutes(bar, 15)
            if session is None:
                continue
            last[sym] = bar.close
            if sym in pos and bar.low <= pos[sym][2]:  # stop, a gap fills at the open
                qty, _, stop, t = pos.pop(sym)
                fill = min(bar.open, stop)
                cash += qty * fill
                t.exit_time, t.exit_price, t.reason_out = ts.isoformat(), fill, "hard stop hit"
                last_stop[sym] = i
            since_open, to_close = session
            sig = strat.generate_signal(sym, bars[: i + 1], sym in pos)
            if sig.action == Action.EXIT and sym in pos:
                qty, _, _, t = pos.pop(sym)
                cash += qty * bar.close
                t.exit_time, t.exit_price, t.reason_out = ts.isoformat(), bar.close, sig.reason
            elif (sig.action == Action.ENTER_LONG and sym not in pos
                  and since_open >= config.opening_delay_min() and to_close > config.eod_no_entry_min()
                  and i - last_stop[sym] > COOLDOWN_BARS):
                eq = equity()
                gross = sum(p[0] * last[s] for s, p in pos.items())
                cap = (late_exposure if late_exposure is not None and to_close <= late_minutes
                       else max_exposure)
                acct = Account(equity=eq, cash=cash, buying_power=max(0.0, cap * eq - gross))
                held = {s: Position(symbol=s, qty=p[0], avg_entry_price=p[1]) for s, p in pos.items()}
                d = size_entry(sym, bar.close, bars[: i + 1], acct, held,
                               correlation_group=group_of.get(sym), group_of=group_of)
                if d.approved:
                    free = size_entry(sym, bar.close, bars[: i + 1],
                                      Account(equity=eq, cash=cash, buying_power=10 * eq), held,
                                      correlation_group=group_of.get(sym), group_of=group_of)
                    if free.approved and d.plan.qty < free.plan.qty:
                        res.smaller_entries += 1
                    t = Trade(sym, ts.isoformat(), bar.close, d.plan.qty)
                    res.trades.append(t)
                    pos[sym] = [d.plan.qty, bar.close, d.plan.stop_price, t]
                    cash -= d.plan.qty * bar.close
        # record the end-of-day state once all markets have their last bar of the session
        day = ts.date().isoformat()
        eq = equity()
        res.equity_by_day[day] = eq
        sessions = [_session_minutes(b, 15) for b in [data[s][idx[s] - 1] for s in data if idx[s]]]
        if any(s and s[1] <= 0 for s in sessions) and eq > 0:
            gross = sum(p[0] * last[s] for s, p in pos.items())
            res.overnight_exposure[day] = gross / eq
    return res
