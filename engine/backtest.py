"""Backtester — replays history through the SAME strategy + risk code as the live engine.

Validates behaviour on history. It is NOT a profit projection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import config
from broker.mock import MockBroker
from models import Action, Bar, Order, OrderType, Side
from risk import size_entry
from strategies import build_strategy

_NY = ZoneInfo("America/New_York")
_OPEN, _CLOSE = time(9, 30), time(16, 0)


def _session_minutes(bar: Bar, bar_minutes: int) -> tuple[float, float] | None:
    """(minutes since open, minutes to close) at the moment this bar has closed and the live
    loop would evaluate it; None outside the regular US session."""
    t = (bar.timestamp + timedelta(minutes=bar_minutes)).astimezone(_NY)
    day = t.date()
    opened = datetime.combine(day, _OPEN, _NY)
    closes = datetime.combine(day, _CLOSE, _NY)
    if not (opened < t <= closes):
        return None
    return (t - opened).total_seconds() / 60, (closes - t).total_seconds() / 60


@dataclass
class Trade:
    symbol: str
    entry_time: str
    entry_price: float
    qty: float
    exit_time: str = ""
    exit_price: float = 0.0
    reason_out: str = ""

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.qty

    @property
    def closed(self) -> bool:
        return bool(self.exit_time)


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    starting_equity: float
    ending_equity: float
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)

    @property
    def closed_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.closed]

    @property
    def num_trades(self) -> int:
        return len(self.closed_trades)

    @property
    def win_rate(self) -> float:
        closed = self.closed_trades
        return sum(t.pnl > 0 for t in closed) / len(closed) * 100.0 if closed else 0.0

    @property
    def total_return_pct(self) -> float:
        return (self.ending_equity - self.starting_equity) / self.starting_equity * 100.0

    @property
    def max_drawdown_pct(self) -> float:
        peak, max_dd = self.starting_equity, 0.0
        for eq in self.equity_curve:
            peak = max(peak, eq)
            max_dd = max(max_dd, (peak - eq) / peak * 100.0 if peak else 0.0)
        return max_dd

    def summary(self) -> str:
        return (f"{self.symbol:<8} {self.strategy:<18} trades={self.num_trades:<3} "
                f"win%={self.win_rate:5.1f} return%={self.total_return_pct:7.2f} "
                f"maxDD%={self.max_drawdown_pct:6.2f}")


def backtest_symbol(symbol: str, strategy_name: str, bars: list[Bar], crypto: bool = False,
                    starting_equity: float = 100_000.0, eod_mode: str | None = None,
                    bar_minutes: int | None = None) -> BacktestResult:
    """eod_mode (mean reversion on equities): None = no session rules; otherwise the live rules
    (no entries in the first 30 / last 30 minutes) plus 'all' | 'winners' | 'off' at the close.
    bar_minutes: bar length, needed for the session rules."""
    broker = MockBroker(starting_equity)
    session_rules = eod_mode is not None and not crypto and bar_minutes
    strat = build_strategy(strategy_name, config.STRATEGY_PARAMS)
    res = BacktestResult(symbol, strategy_name, starting_equity, starting_equity)
    open_trade: Trade | None = None

    for i, bar in enumerate(bars):
        window = bars[: i + 1]
        broker.set_price(symbol, bar.close)

        session = _session_minutes(bar, bar_minutes) if session_rules else None
        if session_rules and session is None:
            res.equity_curve.append(broker.get_account().equity)
            continue  # outside regular hours: the live loop doesn't trade equities then

        # A resting stop can be hit intrabar before any new signal; a gap fills at the open.
        if open_trade:
            hit = broker.trigger_stops(symbol, bar.low, bar_open=bar.open)
            if hit:
                open_trade.exit_time = bar.timestamp.isoformat()
                open_trade.exit_price = hit[0].filled_price
                open_trade.reason_out = "hard stop hit"
                open_trade = None
                broker.set_price(symbol, bar.close)

        sig = strat.generate_signal(symbol, window, open_trade is not None)
        if session:
            since_open, to_close = session
            if (open_trade and sig.action != Action.EXIT and eod_mode != "off"
                    and to_close <= config.eod_flat_min()
                    and (eod_mode == "all" or bar.close > open_trade.entry_price)):
                sig.action, sig.reason = Action.EXIT, "eod flat"
            if sig.action == Action.ENTER_LONG and (since_open < config.opening_delay_min()
                                                    or to_close <= config.eod_no_entry_min()):
                sig.action = Action.HOLD
        if sig.action == Action.EXIT and open_trade:
            broker.close_position(symbol)
            open_trade.exit_time, open_trade.exit_price = bar.timestamp.isoformat(), bar.close
            open_trade.reason_out = sig.reason
            open_trade = None
        elif sig.action == Action.ENTER_LONG and not open_trade:
            d = size_entry(symbol, bar.close, window, broker.get_account(), broker.get_positions(),
                           allow_fractional=crypto)
            if d.approved:
                broker.submit_order(Order(symbol=symbol, side=Side.BUY, qty=d.plan.qty))
                broker.submit_order(Order(symbol=symbol, side=Side.SELL, qty=d.plan.qty,
                                          type=OrderType.STOP, stop_price=d.plan.stop_price))
                open_trade = Trade(symbol, bar.timestamp.isoformat(), bar.close, d.plan.qty)
                res.trades.append(open_trade)

        res.equity_curve.append(broker.get_account().equity)

    res.ending_equity = broker.get_account().equity
    return res
