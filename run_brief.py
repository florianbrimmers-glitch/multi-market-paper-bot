"""Generate a morning or night brief:  python run_brief.py morning|night"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import config
from brief import gather_brief_data, render_brief
from broker import AlpacaBroker
from data import fetch_bars
from models import Timeframe


def todays_actions(now: datetime) -> list[str]:
    """Non-hold actions logged today (UTC) by the trading loop, if the log is available."""
    today = now.date().isoformat()
    out: list[str] = []
    try:
        with open(config.decision_log_path(), encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r["evaluated_at"].startswith(today) and r["action_taken"] not in ("hold", "none"):
                    out.append(f"{r['symbol']}: {r['action_taken']}")
    except (OSError, ValueError, KeyError):
        pass
    return out


def build_brief(kind: str, broker) -> str:
    now = datetime.now(timezone.utc)
    # Last two daily bars: the move from the previous close (24 hourly bars spanned ~3 sessions
    # for ETFs, so the same old drop showed up in several briefs in a row).
    market_bars = {i.symbol: fetch_bars(i.symbol, i.asset_class, Timeframe.D1, limit=2)
                   for i in config.INSTRUMENTS}
    return render_brief(gather_brief_data(kind, broker, now.isoformat(), market_bars, todays_actions(now)))


def main(argv: list[str]) -> None:
    kind = (argv[0] if argv else "morning").lower()
    if kind not in ("morning", "night"):
        raise SystemExit("usage: run_brief.py [morning|night]")
    with AlpacaBroker() as broker:
        print(build_brief(kind, broker))


if __name__ == "__main__":
    main(sys.argv[1:])
