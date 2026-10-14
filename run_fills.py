"""Account snapshot plus the day's filled PAPER buys and sells with their realised result (read-only).

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


def account_snapshot(broker) -> list[str]:
    """Current equity, open positions and the last days' closing equity (all read-only)."""
    acct = broker.get_account()
    out = [f"Konto jetzt: {i18n.usd(acct.equity)} (Cash {i18n.usd(acct.cash)}, "
           f"Vortag {i18n.usd(acct.last_equity)})"]
    for sym, p in sorted(broker.get_positions().items()):
        out.append(f"- offen **{sym}**: {i18n.qty(p.qty)} Stück, Einstand {i18n.usd(p.avg_entry_price)}, "
                   f"unrealisiert {i18n.signed_usd(p.unrealized_pl)}")
    h = broker._client.get("/v2/account/portfolio/history", params={"period": "1W", "timeframe": "1D"})
    h.raise_for_status()
    hist = h.json()
    for ts, eq in zip(hist.get("timestamp") or [], hist.get("equity") or []):
        if eq is not None:
            out.append(f"- Schlusswert {datetime.fromtimestamp(ts, NY).strftime('%d.%m.')}: {i18n.usd(eq)}")
    return out


def main(day: date) -> None:
    with AlpacaBroker() as broker:
        print("\n".join(account_snapshot(broker)) + "\n")
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
            if ts.astimezone(NY).date() == day:
                lines.append(f"- {ts.astimezone(BERLIN).strftime('%H:%M')} **{sym}** gekauft: "
                             f"{i18n.qty(qty)} Stück zu {i18n.usd(price)}")
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
    print(f"Käufe und Verkäufe am {day.strftime('%d.%m.%Y')} (Papiergeld)")
    print("\n".join(lines) or "- keine")
    print(f"Summe realisiert: **{i18n.signed_usd(total)}**")


if __name__ == "__main__":
    main(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(NY).date())
