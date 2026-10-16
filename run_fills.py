"""Account snapshot plus the day's filled PAPER buys and sells with their realised result (read-only).

  python run_fills.py            # today (New York trading day)
  python run_fills.py 2026-09-28

Entry = the most recent filled buy of the same symbol before the sell.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import i18n
from broker import AlpacaBroker
from broker.alpaca import _parse_ts

NY = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")


def account_snapshot(broker, day: date) -> list[str]:
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
    # Everything that moved cash besides fills (fees, interest, transfers, corrections).
    a = broker._client.get("/v2/account/activities", params={"direction": "desc", "page_size": 100})
    a.raise_for_status()
    other = [x for x in a.json() if x.get("activity_type") != "FILL"]
    out.append("Sonstige Buchungen (ohne Käufe/Verkäufe):" if other else "Sonstige Buchungen: keine")
    # Raw activity stream since the previous evening, fills included, to reconcile cash.
    since = f"{(day - timedelta(days=2)).isoformat()}T00:00:00Z"
    r = broker._client.get("/v2/account/activities",
                           params={"after": since, "direction": "asc", "page_size": 100})
    r.raise_for_status()
    flow = 0.0
    out.append(f"Alle Buchungen seit {since}:")
    for x in r.json():
        if x.get("activity_type") == "FILL":
            amt = float(x["qty"]) * float(x["price"]) * (-1 if x["side"] == "buy" else 1)
            out.append(f"- {x['transaction_time'][:19]} FILL {x['side']} {x['symbol']} {x['qty']} @ {x['price']}"
                       f" ({x.get('type', '')}) cash {amt:+.2f}")
        else:
            amt = float(x.get("net_amount") or 0)
            out.append(f"- {x.get('date')} {x.get('activity_type')} {amt:+.2f} {x.get('description') or ''}")
        flow += amt
    out.append(f"Summe Cash-Fluss seit {since}: {flow:+.2f}")
    for x in other[:30]:
        out.append(f"- {x.get('date') or x.get('transaction_time', '')[:16]} {x.get('activity_type')} "
                   f"{x.get('symbol') or ''} {x.get('net_amount') or x.get('qty') or ''} "
                   f"{x.get('description') or ''}".rstrip())
    return out


def main(day: date) -> None:
    with AlpacaBroker() as broker:
        print("\n".join(account_snapshot(broker, day)) + "\n")
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


def history() -> None:
    """Closing equity per trading day since the account started, with daily and total change."""
    with AlpacaBroker() as broker:
        h = broker._client.get("/v2/account/portfolio/history", params={"period": "1A", "timeframe": "1D"})
        h.raise_for_status()
        d = h.json()
    rows = [(ts, eq) for ts, eq in zip(d.get("timestamp") or [], d.get("equity") or []) if eq]
    start = rows[0][1] if rows else 0
    print("| Tag | Schlusswert | Tag % | seit Start % |")
    print("|---|---|---|---|")
    prev = None
    for ts, eq in rows:
        day = i18n.pct((eq / prev - 1) * 100) if prev else "–"
        print(f"| {datetime.fromtimestamp(ts, NY).strftime('%a %d.%m.')} | {i18n.usd(eq)} | {day} | "
              f"{i18n.pct((eq / start - 1) * 100)} |")
        prev = eq


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "history":
        history()
    else:
        main(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(NY).date())
