"""Earnings drift: buy mid and small US stocks that jumped on earnings, hold ten trading days.

Research (run_earnings_study.py, 2 years, all liquid US stocks): after a move of >= 6 % on
earnings headlines, with at least twice the usual volume and a close in the top quarter of the
day's range, mid and small caps kept rising for days (5-day trade +0.6..0.8 % vs +0.25 % on
ordinary days, both halves positive); large caps showed nothing. The edge was in buying at that
day's close — at the next open it was mostly gone — so the bot buys 15 minutes before the close.

Once per trading day, in the tick 5..20 minutes before the close:
  1. universe: liquid US stocks (price, IEX dollar volume) minus the top third (large caps)
     and minus the bot's own instruments;
  2. today's move / range position / volume from snapshots, then earnings headlines;
  3. buy with an OTO stop DRIFT_STOP_PCT below, risk DRIFT_RISK_PCT each, all drift positions
     together at most DRIFT_MAX_EXPOSURE_PCT of equity (mean reversion keeps the rest);
  4. sell positions that reached DRIFT_HOLD_DAYS trading days.
Positions it holds are listed in the loop state (state.drift) so nothing else touches them.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

import config
import i18n
import logbuch
from models import Order, OrderStatus, OrderType, Side, TradeRecord

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
STRATEGY = "earnings_drift"
_universe_cache: dict = {}


def add_trading_days(d: date, n: int) -> date:
    """d plus n weekdays (exchange holidays are ignored: the exit then just comes a day later)."""
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def build_universe(today: str, symbols=None, bars_fn=None) -> dict[str, dict]:
    """Liquid mid and small caps: {symbol: {"avg_vol": 20-day average volume}} (cached per day)."""
    if _universe_cache.get("day") == today:
        return _universe_cache["data"]
    from data.universe import daily_bars, us_equities
    symbols = symbols if symbols is not None else us_equities()
    own = {i.position_symbol for i in config.INSTRUMENTS}
    bars = (bars_fn or daily_bars)([s for s in symbols if s not in own], 60)
    liquid = {}
    for s, b in bars.items():
        b = [x for x in b if x.timestamp.astimezone(NY).date().isoformat() < today]  # full days only
        if len(b) < 21 or b[-1].close < config.DRIFT_MIN_PRICE:
            continue
        dv = median(x.close * x.volume for x in b[-40:])
        if dv >= config.DRIFT_MIN_IEX_DOLLARS:
            liquid[s] = {"dv": dv, "avg_vol": sum(x.volume for x in b[-20:]) / 20}
    if liquid:
        cut = sorted(v["dv"] for v in liquid.values())[2 * len(liquid) // 3]  # top third = large caps
        liquid = {s: v for s, v in liquid.items() if v["dv"] < cut}
    _universe_cache.update(day=today, data=liquid)
    log.info("earnings drift universe: %d mid/small caps", len(liquid))
    return liquid


def candidates(universe: dict, snaps: dict, today: str) -> list[tuple[str, dict, float]]:
    """(symbol, snapshot, move %) for today's strong up-moves, biggest move first."""
    out = []
    for s, info in universe.items():
        q = snaps.get(s)
        if not q or q["day"] != today or not q["prev_close"]:
            continue
        move = (q["c"] / q["prev_close"] - 1) * 100
        rng = q["h"] - q["l"]
        pos = (q["c"] - q["l"]) / rng if rng > 0 else 1.0
        if (move >= config.DRIFT_MIN_MOVE_PCT and pos >= config.DRIFT_MIN_CLOSE_POS
                and q["v"] >= config.DRIFT_VOL_MULT * info["avg_vol"]):
            out.append((s, q, move))
    return sorted(out, key=lambda x: -x[2])


def is_due(clock, state) -> bool:
    if not clock.is_open:
        return False
    to_close = (clock.next_close - clock.timestamp).total_seconds() / 60
    return 5 <= to_close <= 20 and state.drift_day != clock.timestamp.astimezone(NY).date().isoformat()


def run(broker, state, clock, universe_fn=build_universe, snapshots_fn=None, news_fn=None) -> list[str]:
    """One earnings-drift pass. Returns German trade-log lines; updates state.drift/drift_day."""
    today = clock.timestamp.astimezone(NY).date()
    state.drift_day = today.isoformat()
    dry = config.dry_run()
    events: list[str] = []
    positions = broker.get_positions()
    # Forget positions that are gone (stopped out; the loop reports those).
    state.drift = {s: v for s, v in state.drift.items() if s in positions}

    # 1. Exits after the holding period.
    for sym, info in list(state.drift.items()):
        if today.isoformat() >= info["exit_on"]:
            pos = positions[sym]
            if dry:
                events.append(f"- 🔴 **{sym}** würde verkauft (Earnings-Drift: {config.DRIFT_HOLD_DAYS} Tage um) — Testlauf")
                continue
            order = broker.close_position(sym)
            ok = order is not None and order.status != OrderStatus.REJECTED
            fill = broker.last_sell_fill(sym, since=clock.timestamp, wait=5.0) if ok else None
            from run_loop import result_text
            events.append(f"- 🔴 **{sym}** verkauft (Earnings-Drift: {config.DRIFT_HOLD_DAYS} Handelstage um) — "
                          f"{result_text(pos.avg_entry_price, fill)}" if ok else
                          f"- ⚠️ **{sym}** Verkauf (Earnings-Drift) FEHLGESCHLAGEN — neuer Versuch morgen")
            if ok:
                state.drift.pop(sym)
                state.own_exits.append(sym)
            _record(sym, "exit" if ok else "exit_failed", dry)

    # 2. Safety net: every drift position keeps a stop.
    if not dry:
        protected = {o.symbol for o in broker.get_open_orders() if o.side == Side.SELL}
        for sym in state.drift:
            if sym not in protected and sym in positions:
                p = positions[sym]
                broker.submit_order(Order(symbol=sym, side=Side.SELL, qty=p.qty, type=OrderType.STOP,
                                          stop_price=round(p.avg_entry_price * (1 - config.DRIFT_STOP_PCT), 2)))
                log.warning("Placed missing earnings-drift stop for %s", sym)

    # 3. New entries.
    if not config.drift_enabled():
        return events
    universe = universe_fn(today.isoformat())
    if snapshots_fn is None or news_fn is None:
        from data.universe import news_for, snapshots
        snapshots_fn, news_fn = snapshots_fn or snapshots, news_fn or news_for
    cands = [c for c in candidates(universe, snapshots_fn(sorted(universe)), today.isoformat())
             if c[0] not in positions and c[0] not in config.paused_symbols()]
    if not cands:
        return events
    end = clock.timestamp.astimezone(timezone.utc)
    start = datetime.combine(today - timedelta(days=1), datetime.min.time(), NY).replace(hour=16)
    news: dict = {}
    syms = [c[0] for c in cands]
    for k in range(0, len(syms), 50):
        news.update(news_fn(syms[k:k + 50], start.astimezone(timezone.utc), end))
    account = broker.get_account()
    exposure = sum(abs(positions[s].market_value) for s in state.drift if s in positions)
    room = account.equity * config.DRIFT_MAX_EXPOSURE_PCT - exposure
    for sym, q, move in cands:
        heads = [n for n in news.get(sym, []) if n.is_earnings]
        if not heads:
            continue
        price = q["price"]
        qty = account.equity * config.DRIFT_RISK_PCT / (price * config.DRIFT_STOP_PCT)
        qty = math.floor(min(qty, room / price, account.buying_power * 0.97 / price))
        if qty < 1:
            log.info("earnings drift: %s skipped, drift budget (%.0f%% of equity) used up",
                     sym, config.DRIFT_MAX_EXPOSURE_PCT * 100)
            _record(sym, "skipped:drift budget full", dry)
            continue
        stop = round(price * (1 - config.DRIFT_STOP_PCT), 2)
        reason = f"+{move:.1f} % nach Quartalszahlen („{heads[0].headline[:80]}“)"
        if dry:
            events.append(f"- 🟢 **{sym}** würde gekauft: {i18n.qty(qty)} Stück zu ca. {i18n.usd(price)} "
                          f"(Earnings-Drift: {reason}) — Testlauf")
            _record(sym, "skipped:dry_run(entry)", dry)
            continue
        order = broker.submit_order(Order(symbol=sym, side=Side.BUY, qty=qty, type=OrderType.MARKET,
                                          stop_loss=stop, client_order_id=f"drift-{today}-{sym}"))
        if order.status == OrderStatus.REJECTED:
            events.append(f"- ⚠️ **{sym}** Kauforder vom Broker abgelehnt (Earnings-Drift: {reason})")
            _record(sym, "rejected", dry)
            continue
        room -= qty * price
        account.buying_power -= qty * price
        state.drift[sym] = {"entry_on": today.isoformat(),
                            "exit_on": add_trading_days(today, config.DRIFT_HOLD_DAYS).isoformat()}
        events.append(f"- 🟢 **{sym}** gekauft: {i18n.qty(qty)} Stück zu ca. {i18n.usd(price)}, Stop "
                      f"{i18n.usd(stop)} (Earnings-Drift: {reason}; Verkauf spätestens am "
                      f"{date.fromisoformat(state.drift[sym]['exit_on']).strftime('%d.%m.')})")
        _record(sym, "submitted", dry)
    return events


def _record(symbol: str, action: str, dry: bool) -> None:
    logbuch.append_record(TradeRecord(run_id="drift", evaluated_at=datetime.now(timezone.utc).isoformat(),
                                      symbol=symbol, strategy=STRATEGY, action_taken=action, dry_run=dry))
