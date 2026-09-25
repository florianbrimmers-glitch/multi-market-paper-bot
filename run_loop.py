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
from engine import run_tick

log = logging.getLogger("run_loop")
BERLIN = ZoneInfo("Europe/Berlin")
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
               "trend_following": "Trendfolge"}


def trade_events(records) -> list[str]:
    """German lines for real orders, rejections, exits and errors (not holds/skips)."""
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
                       f"Stop {i18n.usd(r.plan.stop_price)} ({strat}: {reason})")
        elif r.action_taken == "rejected":
            out.append(f"- ⚠️ **{r.symbol}** Kauforder vom Broker abgelehnt ({strat}: {reason})")
        elif r.action_taken == "exit":
            out.append(f"- 🔴 **{r.symbol}** verkauft ({strat}: {reason})")
    return out


def broker_closed_events(state, current: dict) -> list[str]:
    """Positions that vanished since the last tick without the bot closing them — i.e. the
    protective stop (or another order at the broker) closed them. The bot never sees those fills."""
    out = []
    for sym, (qty, entry) in state.positions.items():
        if sym not in current and sym not in state.own_exits:
            out.append(f"- 🛑 **{sym}** vom Schutz-Stop beim Broker verkauft: {i18n.qty(qty)} Stück, "
                       f"Einstieg {i18n.usd(entry)}")
    return out


COOLDOWN_BARS = 6  # after a stop-out, wait this many bars of the instrument's timeframe
_TF_MINUTES = {"15Min": 15, "1Hour": 60, "4Hour": 240}


def cooldown_until(symbol: str, now: datetime) -> str:
    import config
    inst = next((i for i in config.INSTRUMENTS if i.position_symbol == symbol), None)
    minutes = _TF_MINUTES.get(inst.timeframe.value, 60) if inst else 60
    return (now + timedelta(minutes=COOLDOWN_BARS * minutes)).isoformat()


def loop_once(broker, state, build=None, publisher=publish, fetch=None,
              notify=lambda body: post_comment(TRADE_LOG_TITLE, body),
              price_of=None) -> tuple[list, str | None]:
    """One tick + trade notifications + a brief if due. Collaborators are injectable for tests."""
    now = datetime.now(timezone.utc)
    current = broker.get_positions()
    stopped = broker_closed_events(state, current)
    # No immediate re-entry after a stop-out (it re-bought USO in the same tick it was stopped).
    for sym in state.positions:
        if sym not in current and sym not in state.own_exits:
            state.cooldown[sym] = cooldown_until(sym, now)
    state.cooldown = {s: u for s, u in state.cooldown.items() if u > now.isoformat()}
    kwargs = {"blocked": set(state.cooldown)}
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
    events = stopped + trade_events(records)
    state.positions = {s: [p.qty, p.avg_entry_price] for s, p in broker.get_positions().items()}
    state.own_exits = [r.symbol.replace("/", "") for r in records if r.action_taken == "exit"]
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
