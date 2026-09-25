"""Market data via the Alpaca Data API. Read-only; httpx imported lazily for offline tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import config
from models import AssetClass, Bar, Timeframe


def _parse_bars(raw: list[dict]) -> list[Bar]:
    return [
        Bar(timestamp=datetime.fromisoformat(b["t"].replace("Z", "+00:00")),
            open=b["o"], high=b["h"], low=b["l"], close=b["c"], volume=b.get("v", 0.0))
        for b in raw
    ]


def latest_price(symbol: str, asset_class: AssetClass) -> float | None:
    """Last trade price right now. Bar closes can be hours old for 1h/4h strategies, and sizing a
    stop off a stale close put it too far from (or too close to) the real fill."""
    import httpx

    headers = {"APCA-API-KEY-ID": config.alpaca_api_key(),
               "APCA-API-SECRET-KEY": config.alpaca_api_secret()}
    with httpx.Client(base_url=config.alpaca_data_url(), headers=headers, timeout=15.0) as client:
        if asset_class == AssetClass.CRYPTO:
            r = client.get("/v1beta3/crypto/us/latest/trades", params={"symbols": symbol})
            r.raise_for_status()
            trade = (r.json().get("trades") or {}).get(symbol)
        else:
            r = client.get(f"/v2/stocks/{symbol}/trades/latest", params={"feed": "iex"})
            r.raise_for_status()
            trade = r.json().get("trade")
    return float(trade["p"]) if trade and trade.get("p") else None


def fetch_bars(symbol: str, asset_class: AssetClass, timeframe: Timeframe, limit: int = 200) -> list[Bar]:
    """Return up to `limit` most-recent bars, chronological (oldest first)."""
    import httpx

    headers = {"APCA-API-KEY-ID": config.alpaca_api_key(),
               "APCA-API-SECRET-KEY": config.alpaca_api_secret()}
    start = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    # sort=desc + limit returns the newest bars; we reverse to chronological below.
    params: dict[str, object] = {"timeframe": timeframe.value, "start": start,
                                 "limit": limit, "sort": "desc"}
    with httpx.Client(base_url=config.alpaca_data_url(), headers=headers, timeout=30.0) as client:
        if asset_class == AssetClass.CRYPTO:
            r = client.get("/v1beta3/crypto/us/bars", params={**params, "symbols": symbol})
            r.raise_for_status()
            raw = (r.json().get("bars") or {}).get(symbol, [])
        else:
            r = client.get(f"/v2/stocks/{symbol}/bars", params={**params, "feed": "iex"})
            r.raise_for_status()
            raw = r.json().get("bars") or []
    return list(reversed(_parse_bars(raw)))
