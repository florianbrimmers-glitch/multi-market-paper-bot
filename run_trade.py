"""Run one paper-trading tick against Alpaca paper.

  DRY_RUN=true  python run_trade.py   # log intended orders only (default)
  DRY_RUN=false python run_trade.py   # place paper orders
"""
from __future__ import annotations

import logging

import config
from broker import AlpacaBroker
from engine import run_tick


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("run_trade")
    log.info("Starting tick (DRY_RUN=%s)", config.dry_run())
    with AlpacaBroker() as broker:
        records = run_tick(broker)
    for r in records:
        action = r.signal.action.value if r.signal else "n/a"
        log.info("%-8s %-18s %-11s -> %s %s", r.symbol, r.strategy, action, r.action_taken,
                 f"({r.signal.reason})" if r.signal else r.error or "")


if __name__ == "__main__":
    main()
