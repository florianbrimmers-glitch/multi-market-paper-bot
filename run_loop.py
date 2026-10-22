"""Long-running trading loop for one GitHub Actions job.

  python run_loop.py --minutes 340   # tick every 15 min for ~5h40, then exit
  python run_loop.py --minutes 0     # a single tick (for manual tests)

Each tick trades (see engine/trader.py), then posts the morning/night brief when one is due
(see brief/schedule.py). Ticks are aligned to quarter hours plus a short delay so the latest
15-minute bar has closed. The workflow re-dispatches itself when this exits, so the loop runs
around the clock; one failing tick never ends the loop.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import i18n
import logbuch
from brief.deliver import TRADE_LOG_TITLE, post_brief, post_comment
from brief.schedule import brief_due, load_state, save_state
from broker import AlpacaBroker
from engine import earnings_drift, run_tick

log = logging.getLogger("run_loop")
BERLIN = ZoneInfo("Europe/Berlin")
NY = ZoneInfo("America/New_York")
BAR_CLOSE_DELAY = timedelta(seconds=20)


def next_tick_after(now: datetime, interval_min: int) -> datetime:
    base = now.replace(second=0, microsecond=0)
    minutes = (base.minute // interval_min + 1) * interval_min
    return base.replace(minute=0) + timedelta(minutes=minutes) + BAR_CLOSE_DELAY


def publish(kind: str, text: str) -> None:
    print(f"\n===== {kind.upper()} BRIEF =====\n{text}\n", flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"## {kind.capitalize()} brief\n\n{text}\n\n")
    post_brief(text, kind)


NOTABLE = ("submitted", "rejected", "exit")


STRATEGY_DE = {"mean_reversion": "Mean Reversion", "momentum_breakout": "Momentum-Ausbruch",
               "trend_following": "Trendfolge", "earnings_drift": "Earnings-Drift"}


def result_text(entry: float, fill: tuple[float, float] | None) -> str:
    """'Verkauf 151,20 $ — Ergebnis: +201,60 $ (+0,80 %)' from the entry and the sell fill."""
    if not fill:
        return "Ergebnis noch unbekannt (Ausführung beim Broker nicht bestätigt)"
    qty, price = fill
    pl = (price - entry) * qty
    return (f"Verkauf {i18n.usd(price)} — **Ergebnis: {i18n.signed_usd(pl)}** "
            f"({i18n.pct((price - entry) / entry * 100 if entry else 0)})")


def trade_events(records, results: dict[str, str] | None = None) -> list[str]:
    """German lines for real orders, rejections, exits and errors (not holds/skips).
    results: per-symbol P/L text for exits (see result_text)."""
    results = results or {}
    out = []
    for r in records:
        strat = STRATEGY_DE.get(r.strategy, r.strategy)
        reason = r.signal.reason if r.signal else ""
        if r.error:
            out.append(f"- ⚠️ **{r.symbol}** Fehler: `{r.error}`")
        elif r.action_taken == "exit_failed":
            out.append(f"- ⚠️ **{r.symbol}** Verkauf beim Broker FEHLGESCHLAGEN — neuer Versuch beim nächsten "
                       f"Check (der Schutz-Stop wird solange neu gesetzt)")
        elif r.action_taken == "submitted" and r.plan:
            out.append(f"- 🟢 **{r.symbol}** gekauft: {i18n.qty(r.plan.qty)} Stück zu ca. {i18n.usd(r.plan.entry_price)}, "
                       f"Stop {i18n.usd(r.plan.stop_price)} ({strat}: {reason})"
                       + (" — **halbe Größe** (Gesamtmarkt fällt)" if "market sell-off" in r.plan.reason
                          else " — **halbe Größe** (verwandter Markt offen oder kürzlich ausgestoppt)"
                          if "half size" in r.plan.reason else ""))
        elif r.action_taken == "rejected":
            out.append(f"- ⚠️ **{r.symbol}** Kauforder vom Broker abgelehnt ({strat}: {reason})")
        elif r.action_taken == "exit":
            res = f" — {results[r.symbol]}" if r.symbol in results else ""
            out.append(f"- 🔴 **{r.symbol}** verkauft ({strat}: {reason}){res}")
    return out


def _order_symbol(position_symbol: str) -> str:
    import config
    inst = next((i for i in config.INSTRUMENTS if i.position_symbol == position_symbol), None)
    return inst.symbol if inst else position_symbol


def broker_closed_events(state, current: dict, broker=None) -> list[str]:
    """Positions that vanished since the last tick without the bot closing them — i.e. the
    protective stop (or another order at the broker) closed them. The fill is looked up at the
    broker for the P/L. Also covers entries that had not filled yet when the last tick ended
    (state.pending) but filled and were stopped out before this one — on 2026-10-22 XLK and XLE
    went that way unnoticed and were bought again 15 minutes later."""
    return [line for _, line in stopped_out(state, current, broker)]


def stopped_out(state, current: dict, broker=None) -> list[tuple[str, str]]:
    out = []
    for sym, (qty, entry) in state.positions.items():
        if sym not in current and sym not in state.own_exits:
            fill = broker.last_sell_fill(_order_symbol(sym)) if broker else None
            out.append((sym, f"- 🛑 **{sym}** vom Schutz-Stop beim Broker verkauft: {i18n.qty(qty)} Stück, "
                             f"Einstieg {i18n.usd(entry)}, {result_text(entry, fill)}"))
    for sym, (qty, entry, since) in state.pending.items():
        if sym in current or sym in state.positions or sym in state.own_exits or broker is None:
            continue
        fill = broker.last_sell_fill(_order_symbol(sym), since=datetime.fromisoformat(since))
        if fill:  # filled after the last tick and already sold by its stop
            out.append((sym, f"- 🛑 **{sym}** vom Schutz-Stop beim Broker verkauft (zwischen zwei Checks gekauft "
                             f"und ausgestoppt): {i18n.qty(fill[0])} Stück, Einstieg ca. {i18n.usd(entry)}, "
                             f"{result_text(entry, fill)}"))
    return out


COOLDOWN_BARS = 6  # after a stop-out, wait this many bars of the instrument's timeframe
_TF_MINUTES = {"15Min": 15, "1Hour": 60, "4Hour": 240, "1Day": 1440}


def cooldown_until(symbol: str, now: datetime) -> str:
    import config
    inst = next((i for i in config.INSTRUMENTS if i.position_symbol == symbol), None)
    minutes = _TF_MINUTES.get(inst.timeframe.value, 60) if inst else 60
    return (now + timedelta(minutes=COOLDOWN_BARS * minutes)).isoformat()


def cooldown_symbols(symbol: str) -> list[str]:
    """The stopped-out symbol plus its correlation-group partners: a stop on QQQ means the move
    is market-wide (2026-10-08). The symbol itself is blocked; partners only buy at half size."""
    import config
    inst = next((i for i in config.INSTRUMENTS if i.position_symbol == symbol), None)
    group = inst.correlation_group if inst else None
    if not group:
        return [symbol]
    return [symbol] + [i.position_symbol for i in config.INSTRUMENTS
                       if i.correlation_group == group and i.position_symbol != symbol]


def manual_closes(broker, state, clock) -> list[str]:
    """Sell positions listed in config.MANUAL_CLOSES on their date (15+ min after the open)."""
    import config
    from models import OrderStatus
    if not clock.is_open:
        return []
    ny = clock.timestamp.astimezone(NY)
    if (ny - ny.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() < 15 * 60:
        return []
    positions = broker.get_positions()
    out = []
    for sym, day, why in config.MANUAL_CLOSES:
        if day != ny.date().isoformat() or sym not in positions:
            continue
        pos = positions[sym]
        order = broker.close_position(sym)
        if order is None or order.status == OrderStatus.REJECTED:
            out.append(f"- ⚠️ **{sym}** manueller Verkauf FEHLGESCHLAGEN — neuer Versuch beim nächsten Check")
            continue
        fill = broker.last_sell_fill(sym, since=clock.timestamp, wait=5.0)
        out.append(f"- 🔴 **{sym}** verkauft (auf Wunsch geschlossen: {why}) — "
                   f"{result_text(pos.avg_entry_price, fill)}")
        state.own_exits.append(sym)
        state.drift.pop(sym, None)
    return out


def loop_once(broker, state, build=None, publisher=publish, fetch=None,
              notify=lambda body: post_comment(TRADE_LOG_TITLE, body),
              price_of=None, drift=earnings_drift) -> tuple[list, str | None]:
    """One tick + trade notifications + a brief if due. Collaborators are injectable for tests."""
    now = datetime.now(timezone.utc)
    current = broker.get_positions()
    closed = stopped_out(state, current, broker)
    stopped = [line for _, line in closed]
    # No immediate re-entry after a stop-out (it re-bought USO in the same tick it was stopped).
    for sym, _ in closed:
        state.cooldown[sym] = cooldown_until(sym, now)
    state.cooldown = {s: u for s, u in state.cooldown.items() if u > now.isoformat()}
    halved = {p for s in state.cooldown for p in cooldown_symbols(s)[1:]} - set(state.cooldown)
    kwargs = {"blocked": set(state.cooldown), "halved": halved}
    if fetch:
        kwargs["fetch"] = fetch
    if price_of is None and fetch is None:
        from engine.trader import live_price as price_of
    if price_of:
        kwargs["price_of"] = price_of
    records = run_tick(broker, **kwargs)
    for r in records:
        if r.action_taken != "hold" or r.error:
            log.info("%-8s %s %s", r.symbol, r.action_taken, r.error or (r.signal.reason if r.signal else ""))
    results = {}
    for r in records:
        pos = current.get(r.symbol.replace("/", ""))
        if r.action_taken == "exit" and pos:
            fill = broker.last_sell_fill(r.symbol, since=now, wait=5.0)
            results[r.symbol] = result_text(pos.avg_entry_price, fill)
    events = stopped + trade_events(records, results)
    state.own_exits = [r.symbol.replace("/", "") for r in records if r.action_taken == "exit"]
    clock = broker.clock()
    events += manual_closes(broker, state, clock)
    if drift is not None and drift.is_due(clock, state):
        try:
            events += drift.run(broker, state, clock)
        except Exception as e:  # noqa: BLE001 — the drift pass must never break the tick
            log.exception("earnings drift pass failed")
            events.append(f"- ⚠️ Earnings-Drift: Prüfung fehlgeschlagen (`{type(e).__name__}: {str(e)[:200]}`)")
    final = broker.get_positions()
    # Stopped out while this tick was running (2026-10-22: EWG's stop fired during a 7-minute tick
    # and was never reported, so EWG was re-bought without a cooldown).
    from brief.schedule import BriefState
    during = BriefState(positions={s: [p.qty, p.avg_entry_price] for s, p in current.items()},
                        own_exits=state.own_exits)
    for sym, line in stopped_out(during, final, broker):
        if sym not in {s for s, _ in closed}:
            events.append(line)
            state.cooldown[sym] = cooldown_until(sym, now)
    state.positions = {s: [p.qty, p.avg_entry_price] for s, p in final.items()}
    # Entries not filled yet: remembered so a fill + stop-out before the next tick is still seen.
    state.pending = {r.symbol.replace("/", ""): [r.plan.qty, r.plan.entry_price, now.isoformat()]
                     for r in records if r.action_taken == "submitted" and r.plan
                     and r.symbol.replace("/", "") not in state.positions}
    save_state(state)
    if events:
        stamp = datetime.now(BERLIN).strftime("%d.%m.%Y %H:%M Uhr")
        mode = "Testlauf, keine Orders" if records[0].dry_run else "Papiergeld"
        notify(f"**{stamp}** ({mode})\n" + "\n".join(events))
    kind = brief_due(broker.clock(), state)
    if kind:
        if build is None:
            from run_brief import build_brief as build
        publisher(kind, build(kind, broker))
        setattr(state, kind, broker.clock().timestamp.date().isoformat())
        save_state(state)
    if drift is not None:
        drift.warm_up(clock)  # slow (whole-market bars): after the tick's own work is done
    return records, kind


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=340, help="run time budget; 0 = single tick")
    ap.add_argument("--interval", type=int, default=15)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    logbuch.prune()
    deadline = datetime.now(timezone.utc) + timedelta(minutes=args.minutes)
    state = load_state()
    ticks = 0
    while True:
        try:
            with AlpacaBroker() as broker:
                _, kind = loop_once(broker, state)
            ticks += 1
            log.info("tick %d done%s", ticks, f" (+{kind} brief)" if kind else "")
        except Exception:  # noqa: BLE001 — network hiccups etc. must not end the loop
            log.exception("tick failed; continuing")
        nxt = next_tick_after(datetime.now(timezone.utc), args.interval)
        if args.minutes == 0 or nxt > deadline:
            break
        time.sleep(max(0.0, (nxt - datetime.now(timezone.utc)).total_seconds()))
    log.info("loop finished after %d ticks", ticks)


if __name__ == "__main__":
    main()
