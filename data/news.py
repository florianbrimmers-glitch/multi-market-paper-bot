"""News via Alpaca's news API (Benzinga feed). Read-only; httpx imported lazily for offline tests."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime

import config

_EARNINGS = re.compile(r"\b(earnings|eps|revenue|results|guidance|outlook|q[1-4]|quarter(ly)?|"
                       r"beats?|miss(es)?|forecast)\b", re.IGNORECASE)


@dataclass
class NewsItem:
    created_at: datetime
    headline: str
    symbols: list[str]

    @property
    def is_earnings(self) -> bool:
        return bool(_EARNINGS.search(self.headline))


def fetch_news(symbol: str, start: datetime, end: datetime, limit: int = 50) -> list[NewsItem]:
    """Headlines for one symbol between start and end (newest first), up to `limit` items."""
    import httpx

    headers = {"APCA-API-KEY-ID": config.alpaca_api_key(),
               "APCA-API-SECRET-KEY": config.alpaca_api_secret()}
    params: dict[str, object] = {"symbols": symbol, "start": start.isoformat(), "end": end.isoformat(),
                                 "limit": min(limit, 50), "sort": "desc", "include_content": "false"}
    items: list[NewsItem] = []
    with httpx.Client(base_url=config.alpaca_data_url(), headers=headers, timeout=30.0) as client:
        while len(items) < limit:
            for attempt in range(6):  # the data API allows ~200 requests/min; back off on 429
                r = client.get("/v1beta1/news", params=params)
                if r.status_code != 429:
                    break
                time.sleep(2 ** attempt)
            r.raise_for_status()
            body = r.json()
            for n in body.get("news") or []:
                items.append(NewsItem(datetime.fromisoformat(n["created_at"].replace("Z", "+00:00")),
                                      n.get("headline") or "", n.get("symbols") or []))
            token = body.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
    return items[:limit]
