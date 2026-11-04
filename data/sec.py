"""Reported quarterly numbers from SEC EDGAR (XBRL company facts), for research only.

SEC requires a User-Agent naming a person and an email (env SEC_USER_AGENT); requests are
throttled below its 10 per second limit."""
from __future__ import annotations

import os
import time
from datetime import date, timedelta

import httpx

REVENUE = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
           "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
           "RevenuesNetOfInterestExpense")
EPS = ("EarningsPerShareDiluted", "EarningsPerShareBasic")


class Sec:
    def __init__(self, user_agent: str | None = None, pause: float = 0.15) -> None:
        ua = user_agent or os.environ.get("SEC_USER_AGENT", "")
        if "@" not in ua:
            raise RuntimeError("SEC_USER_AGENT must name a person and an email, e.g. 'Jane Doe jane@x.com'")
        self._client = httpx.Client(headers={"User-Agent": ua}, timeout=30.0)
        self._pause = pause
        self._ciks: dict[str, int] | None = None
        self._facts: dict[int, dict | None] = {}

    def _get(self, url: str) -> dict | None:
        for attempt in range(3):
            time.sleep(self._pause)
            r = self._client.get(url)
            if r.status_code == 404:
                return None
            if r.status_code < 400:
                return r.json()
            time.sleep(2 * (attempt + 1))
        return None

    def cik(self, symbol: str) -> int | None:
        if self._ciks is None:
            data = self._get("https://www.sec.gov/files/company_tickers.json") or {}
            self._ciks = {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}
        return self._ciks.get(symbol.upper())

    def facts(self, symbol: str) -> dict | None:
        cik = self.cik(symbol)
        if cik is None:
            return None
        if cik not in self._facts:
            self._facts[cik] = self._get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
        return self._facts[cik]


def _quarters(companyfacts: dict, concepts: tuple[str, ...]) -> list[dict]:
    """Quarter-long facts (80–100 days) of the first concept the company reports."""
    gaap = (companyfacts or {}).get("facts", {}).get("us-gaap", {})
    for c in concepts:
        units = gaap.get(c, {}).get("units", {})
        facts = units.get("USD") or units.get("USD/shares") or []
        out = []
        for f in facts:
            if "start" not in f or "filed" not in f:
                continue
            s, e = date.fromisoformat(f["start"]), date.fromisoformat(f["end"])
            if 80 <= (e - s).days <= 100:
                out.append({"start": s, "end": e, "filed": date.fromisoformat(f["filed"]), "val": f["val"]})
        if out:
            return out
    return []


def yoy_growth(companyfacts: dict, concepts: tuple[str, ...], announced: date) -> float | None:
    """Growth (%) of the quarter announced on `announced` against the same quarter a year earlier.
    The announced quarter is the one ending within 100 days before, first filed on or after the
    announcement (the 10-Q repeats the press-release numbers); None if not found."""
    qs = _quarters(companyfacts, concepts)
    cur = [q for q in qs if announced - timedelta(days=100) <= q["end"] < announced
           and announced - timedelta(days=3) <= q["filed"] <= announced + timedelta(days=60)]
    if not cur:
        return None
    q = min(cur, key=lambda x: x["filed"])
    prev = [p for p in qs if abs((q["end"] - p["end"]).days - 365) <= 15
            and abs((q["start"] - p["start"]).days - 365) <= 15]
    if not prev:
        return None
    base = min(prev, key=lambda x: x["filed"])["val"]
    if not base:
        return None
    return (q["val"] - base) / abs(base) * 100
