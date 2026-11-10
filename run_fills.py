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
    stops = {o.symbol: o.stop_price for o in broker.get_open_orders()
             if o.side.value == "sell" and o.stop_price}
    for sym, p in sorted(broker.get_positions().items()):
        price = p.market_value / p.qty if p.qty else 0.0
        stop = stops.get(sym)
        gap = (f", Stop {i18n.usd(stop)} ({i18n.pct((stop / price - 1) * 100)} vom Kurs, "
               f"Risiko bis Stop {i18n.signed_usd((stop - price) * p.qty)})" if stop and price else ", KEIN STOP")
        out.append(f"- offen **{sym}**: {i18n.qty(p.qty)} Stück, Einstand {i18n.usd(p.avg_entry_price)}, "
                   f"Kurs {i18n.usd(price)}, unrealisiert {i18n.signed_usd(p.unrealized_pl)}{gap}")
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


def _activities(broker, kind: str | None = None) -> list[dict]:
    """All account activities, oldest first (paged by activity id)."""
    path = "/v2/account/activities" + (f"/{kind}" if kind else "")
    out: list[dict] = []
    token = None
    while True:
        params: dict[str, object] = {"direction": "asc", "page_size": 100}
        if token:
            params["page_token"] = token
        r = broker._client.get(path, params=params)
        r.raise_for_status()
        page = r.json()
        out += page
        if len(page) < 100:
            return out
        token = page[-1]["id"]


def ledger(fills: list[dict]) -> tuple[list[tuple[str, str, float]], dict[str, tuple[float, float]]]:
    """Average-cost bookkeeping over executions (oldest first): every sell is booked against the
    symbol's average cost. Returns [(New York date, symbol, realised $)] and the open lots
    {symbol: (qty, average cost)}."""
    lots: dict[str, list[float]] = {}
    realised: list[tuple[str, str, float]] = []
    for f in fills:
        sym, qty, price = f["symbol"].replace("/", ""), float(f["qty"]), float(f["price"])
        lot = lots.setdefault(sym, [0.0, 0.0])
        if f["side"] == "buy":
            lot[1] = (lot[0] * lot[1] + qty * price) / (lot[0] + qty)
            lot[0] += qty
        else:
            day = _parse_ts(f["transaction_time"]).astimezone(NY).date().isoformat()
            realised.append((day, sym, (price - lot[1]) * qty))
            lot[0] = max(0.0, lot[0] - qty)
    return realised, {s: (q, c) for s, (q, c) in lots.items() if q > 1e-9}


def pnl(since: str = "2026-10-12") -> None:
    """Realised and open result by strategy and symbol, from the broker's executions — the
    Trade-Log is not complete enough for this (partial stops, results never followed up)."""
    import config
    with AlpacaBroker() as broker:
        acts = _activities(broker)
        positions = broker.get_positions()
        account = broker.get_account()
    fills = [a for a in acts if a.get("activity_type") == "FILL"]
    realised, lots = ledger(fills)
    mr = {i.position_symbol for i in config.INSTRUMENTS if i.strategy == "mean_reversion"}
    own = {i.position_symbol for i in config.INSTRUMENTS}
    group = lambda s: "Mean Reversion" if s in mr else "andere eigene Märkte" if s in own else "Earnings Drift"
    print(f"Ergebnis nach Strategie (Papiergeld), aus {len(fills)} Ausführungen bei Alpaca\n")
    print(f"| Strategie | realisiert seit Start | realisiert seit {since[8:]}.{since[5:7]}. | offen (Buchgewinn) | Verkäufe seit {since[8:]}.{since[5:7]}. |")
    print("|---|---|---|---|---|")
    tot_all = tot_since = tot_open = 0.0
    for g in ("Mean Reversion", "Earnings Drift", "andere eigene Märkte"):
        a = sum(v for d, s, v in realised if group(s) == g)
        b = [v for d, s, v in realised if group(s) == g and d >= since]
        o = sum(p.unrealized_pl for s, p in positions.items() if group(s) == g)
        tot_all, tot_since, tot_open = tot_all + a, tot_since + sum(b), tot_open + o
        print(f"| {g} | {i18n.signed_usd(a)} | {i18n.signed_usd(sum(b))} | {i18n.signed_usd(o)} | {len(b)} |")
    print(f"| **Summe** | **{i18n.signed_usd(tot_all)}** | **{i18n.signed_usd(tot_since)}** | **{i18n.signed_usd(tot_open)}** | |")
    other: dict[str, float] = {}
    for a in acts:
        if a.get("activity_type") != "FILL":
            other[a.get("activity_type") or "?"] = other.get(a.get("activity_type") or "?", 0.0) + float(a.get("net_amount") or 0)
    print("\nSonstige Buchungen seit Start: " + (", ".join(f"{k} {i18n.signed_usd(v)}" for k, v in sorted(other.items())) or "keine"))
    start = 100_000.0
    explained = start + tot_all + tot_open + sum(v for k, v in other.items() if k not in ("JNLC", "CSD"))
    print(f"Konto jetzt: {i18n.usd(account.equity)}; 100.000 $ + realisiert + offen + sonstige = {i18n.usd(explained)} "
          f"(Differenz {i18n.signed_usd(account.equity - explained)})")
    for g in ("Mean Reversion", "Earnings Drift"):
        per: dict[str, list[float]] = {}
        for d, s, v in realised:
            if group(s) == g and d >= since:
                per.setdefault(s, []).append(v)
        print(f"\n{g} seit {since[8:]}.{since[5:7]}. nach Markt (realisiert, Anzahl Verkäufe): "
              + ", ".join(f"{s} {i18n.signed_usd(sum(v))} ({len(v)})" for s, v in sorted(per.items(), key=lambda kv: sum(kv[1]))))
    by_day: dict[str, dict[str, float]] = {}
    for d, s, v in realised:
        if d >= since:
            by_day.setdefault(d, {}).setdefault(group(s), 0.0)
            by_day[d][group(s)] += v
    print("\n| Tag | Mean Reversion | Earnings Drift |")
    print("|---|---|---|")
    for d in sorted(by_day):
        print(f"| {d[8:]}.{d[5:7]}. | {i18n.signed_usd(by_day[d].get('Mean Reversion', 0.0))} | "
              f"{i18n.signed_usd(by_day[d].get('Earnings Drift', 0.0))} |")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "pnl":
        pnl(*sys.argv[2:3])
    elif len(sys.argv) > 1 and sys.argv[1] == "history":
        history()
    else:
        main(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(NY).date())
