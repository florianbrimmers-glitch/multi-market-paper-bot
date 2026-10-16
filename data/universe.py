"""Market-wide data for research and screening: the tradable US stock list, daily bars for many
symbols per request, and news for many symbols per request. Read-only."""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import config
from data.market_data import _get, _parse_bars
from data.news import NewsItem
from models import Bar


def _client(base_url: str):
    import httpx

    return httpx.Client(base_url=base_url, timeout=60.0,
                        headers={"APCA-API-KEY-ID": config.alpaca_api_key(),
                                 "APCA-API-SECRET-KEY": config.alpaca_api_secret()})


def us_equities() -> list[str]:
    """Active, tradable US-listed stocks and ETFs (symbols without '.' or '/' only)."""
    with _client(config.alpaca_trading_url()) as c:
        r = _get(c, "/v2/assets", {"status": "active", "asset_class": "us_equity"})
        r.raise_for_status()
    return sorted(a["symbol"] for a in r.json()
                  if a.get("tradable") and a.get("exchange") in ("NYSE", "NASDAQ", "AMEX", "ARCA", "BATS")
                  and "." not in a["symbol"] and "/" not in a["symbol"])


def daily_bars(symbols: list[str], days: int, batch: int = 200) -> dict[str, list[Bar]]:
    """Split-adjusted daily bars for many symbols, chronological per symbol."""
    start = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    raw: dict[str, list[dict]] = defaultdict(list)
    with _client(config.alpaca_data_url()) as c:
        for i in range(0, len(symbols), batch):
            params: dict[str, object] = {"symbols": ",".join(symbols[i:i + batch]), "timeframe": "1Day",
                                         "start": start, "limit": 10_000, "feed": "iex", "adjustment": "all"}
            while True:
                time.sleep(0.35)
                r = _get(c, "/v2/stocks/bars", params)
                r.raise_for_status()
                body = r.json()
                for sym, bars in (body.get("bars") or {}).items():
                    raw[sym].extend(bars)
                if not body.get("next_page_token"):
                    break
                params["page_token"] = body["next_page_token"]
    return {s: sorted(_parse_bars(b), key=lambda x: x.timestamp) for s, b in raw.items()}


def news_for(symbols: list[str], start: datetime, end: datetime) -> dict[str, list[NewsItem]]:
    """All headlines for a set of symbols in a time window, grouped by symbol."""
    out: dict[str, list[NewsItem]] = defaultdict(list)
    params: dict[str, object] = {"symbols": ",".join(symbols), "start": start.isoformat(),
                                 "end": end.isoformat(), "limit": 50, "include_content": "false"}
    wanted = set(symbols)
    with _client(config.alpaca_data_url()) as c:
        while True:
            time.sleep(0.35)
            r = _get(c, "/v1beta1/news", params)
            r.raise_for_status()
            body = r.json()
            for n in body.get("news") or []:
                item = NewsItem(datetime.fromisoformat(n["created_at"].replace("Z", "+00:00")),
                                n.get("headline") or "", n.get("symbols") or [])
                for s in item.symbols:
                    if s in wanted:
                        out[s].append(item)
            if not body.get("next_page_token"):
                break
            params["page_token"] = body["next_page_token"]
    return out
