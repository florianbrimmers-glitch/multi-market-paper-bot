"""List today's filled PAPER sells with their realised result (read-only).

  python run_fills.py            # today (New York trading day)
  python run_fills.py 2026-09-28

Entry = the most recent filled buy of the same symbol before the sell.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import i18n
from broker import AlpacaBroker
from broker.alpaca import _parse_ts

NY = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")


def main(day: date) -> None:
    with AlpacaBroker() as broker:
        r = broker._client.get("/v2/orders", params={"status": "closed", "direction": "asc", "limit": 500,
                                                     "after": f"{day.isoformat()}T00:00:00Z"})
        r.raise_for_status()
        # also look a few days back for the buys behind today's sells
        r0 = broker._client.get("/v2/orders", params={"status": "closed", "direction": "asc", "limit": 500,
                                                      "until": f"{day.isoformat()}T00:00:00Z"})
        r0.raise_for_status()
    fills = [d for d in r0.json() + r.json() if d.get("filled_at") and d.get("filled_avg_price")]
    fills.sort(key=lambda d: _parse_ts(d["filled_at"]))
    last_buy: dict[str, float] = {}
    total = 0.0
    lines = []
    for d in fills:
        sym, price, qty = d["symbol"], float(d["filled_avg_price"]), float(d["filled_qty"])
        ts = _parse_ts(d["filled_at"])
        if d["side"] == "buy":
            last_buy[sym] = price
            continue
        if ts.astimezone(NY).date() != day:
            continue
        entry = last_buy.get(sym)
        kind = "Stop" if d.get("type") in ("stop", "stop_limit") else "Bot"
        when = ts.astimezone(BERLIN).strftime("%H:%M")
        if entry is None:
            lines.append(f"- {when} **{sym}** ({kind}): {i18n.qty(qty)} Stück zu {i18n.usd(price)} — Einstand unbekannt")
            continue
        pl = (price - entry) * qty
        total += pl
        lines.append(f"- {when} **{sym}** ({kind}): {i18n.qty(qty)} Stück, Einstand {i18n.usd(entry)}, "
                     f"Verkauf {i18n.usd(price)} — **{i18n.signed_usd(pl)}** "
                     f"({i18n.pct((price - entry) / entry * 100)})")
    print(f"Verkäufe am {day.strftime('%d.%m.%Y')} (Papiergeld)")
    print("\n".join(lines) or "- keine")
    print(f"Summe realisiert: **{i18n.signed_usd(total)}**")


if __name__ == "__main__":
    main(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(NY).date())
