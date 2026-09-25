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

import logbuch
from brief.deliver import TRADE_LOG_TITLE, post_brief, post_comment
from brief.schedule import brief_due, load_state, save_state
from broker import AlpacaBroker
from engine import run_tick

log = logging.getLogger("run_loop")
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


def trade_events(records) -> list[str]:
    """Human-readable lines for real orders, rejections, exits and errors (not holds/skips)."""
    out = []
    for r in records:
        if r.error:
            out.append(f"- ⚠️ **{r.symbol}** error: `{r.error}`")
        elif r.action_taken in NOTABLE:
            reason = r.signal.reason if r.signal else ""
            if r.plan and r.action_taken != "exit":
                out.append(f"- **{r.symbol}** {r.action_taken}: buy {r.plan.qty:g} @ ~{r.plan.entry_price:.2f}, "
                           f"stop {r.plan.stop_price:.2f} ({r.strategy}: {reason})")
            else:
                out.append(f"- **{r.symbol}** {r.action_taken} ({r.strategy}: {reason})")
    return out


def loop_once(broker, state, build=None, publisher=publish, fetch=None,
              notify=lambda body: post_comment(TRADE_LOG_TITLE, body)) -> tuple[list, str | None]:
    """One tick + trade notifications + a brief if due. Collaborators are injectable for tests."""
    records = run_tick(broker, fetch=fetch) if fetch else run_tick(broker)
    for r in records:
        if r.action_taken != "hold" or r.error:
            log.info("%-8s %s %s", r.symbol, r.action_taken, r.error or (r.signal.reason if r.signal else ""))
    events = trade_events(records)
    if events:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        notify(f"**{stamp}** ({'DRY RUN' if records[0].dry_run else 'paper'})\n" + "\n".join(events))
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
