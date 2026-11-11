"""run_drift_detail.py: the earnings-drift study with live costs, headline classes and stop widths."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import run_drift_detail as dd
from models import Bar


def _bars(closes, lows=None, opens=None, volume=1_000_000):
    start = datetime(2026, 1, 5, 5, 0, tzinfo=timezone.utc)
    out = []
    for i, c in enumerate(closes):
        low = lows[i] if lows else c * 0.995
        o = opens[i] if opens else c
        out.append(Bar(timestamp=start + timedelta(days=i), open=o, high=max(o, c) * 1.002, low=min(low, o, c),
                       close=c, volume=volume))
    return out


@pytest.mark.parametrize("headline, expected", [
    ("Gap Q4 EPS $0.49 Beats $0.23 Estimate, Sales $4.30B Beat $4.22B Estimate", "earnings"),
    ("Morgan Stanley Upgrades Roku to Overweight, Raises Price Target to $95", "analyst_up"),
    ("JP Morgan Downgrades Acme to Neutral, Lowers Price Target to $20", "analyst_down"),
    ("Acme Announces $500M Share Repurchase Program", "buyback"),
    ("Acme Set To Join S&P MidCap 400", "index"),
    ("Acme To Be Acquired By Omega For $45 Per Share In Cash", "merger"),
    ("Acme CEO Buys 50,000 Shares In Open Market", "insider"),
    ("Acme Prices $300M Public Offering Of Common Stock", "offering"),
    ("Acme Announces Positive Topline Data From Phase 3 Study", "clinical"),
    ("Why Acme Shares Are Trading Higher Today", "other_news"),
])
def test_headline_classes(headline, expected):
    assert dd.classify([(headline, 1)]) == expected


def test_roundups_and_missing_headlines_do_not_count():
    assert dd.classify([]) == "no_headline"
    assert dd.classify([("Stocks Rise; Acme, Beta, Gamma, Delta Report Q3 Earnings", 4)]) == "no_headline"
    assert dd.classify([("Why Acme Shares Are Trading Higher", 1), ("Acme Q3 EPS Beats", 1)]) == "earnings"


def test_earnings_class_is_exactly_the_live_pattern():
    from data.news import NewsItem
    for h in ["Earnings Preview: Acme", "Acme Declares Quarterly Dividend", "Acme Wins Contract For Fiscal 2027",
              "Acme Q3 Sales Beat", "Acme wins contract", "Acme To Host Webcast To Discuss Topline Results"]:
        live = NewsItem(datetime.now(timezone.utc), h, ["ACME"]).is_earnings
        assert (dd.classify([(h, 1)]) == "earnings") == live, h


@pytest.mark.parametrize("headlines, kind", [
    (["Acme Q3 2026 Earnings Call Transcript"], "transcript"),
    (["Acme Q3 EPS $1.20 Beats $1.05 Estimate, Sales $2.1B Beat $2.0B Estimate"], "report"),
    (["Earnings Preview: Acme"], "preview"),
    (["Acme Likely To Report Higher Q3 Earnings; Here Are The Recent Forecast Changes"], "preview"),
    (["Acme To Announce Third Quarter Results On November 12"], "date"),
    (["Acme Declares Quarterly Dividend Of $0.25"], "dividend"),
    (["Earnings Preview: Acme", "Acme Reports Third Quarter Results"], "report"),   # a real report wins
    (["Keybanc Maintains Overweight on Acme After Q3, Raises Price Target to $60"], "analyst"),
])
def test_earnings_kinds(headlines, kind):
    assert dd.earnings_kind([(h, 1) for h in headlines]) == kind


def test_trade_costs_match_the_live_measurements():
    flat = _bars([100.0] * 12)
    gross, stopped = dd.trade(flat, 0, 3.0, costs=False)
    assert gross == pytest.approx(0.0) and not stopped
    net, _ = dd.trade(flat, 0, 3.0)
    assert net == pytest.approx((100 * 0.9995 / (100 * 1.0012) - 1) * 100)      # -0.17 %: both sides
    # stop touched intraday on day 3: filled 0.40 % below the stop price of 97
    lows = [99.5] * 12
    lows[3] = 96.0
    net, stopped = dd.trade(_bars([100.0] * 12, lows=lows), 0, 3.0)
    assert stopped and net == pytest.approx((97 * 0.996 / 100.12 - 1) * 100)    # -3.50 %, as live
    # an opening gap below the stop fills at the open
    opens = [100.0] * 12
    opens[2] = 90.0
    closes = [100.0, 100.0] + [90.0] * 10
    net, stopped = dd.trade(_bars(closes, opens=opens), 0, 3.0)
    assert stopped and net == pytest.approx((90 * 0.996 / 100.12 - 1) * 100)
    # without a stop the same trade is held to day 10
    net, stopped = dd.trade(_bars(closes, opens=opens), 0, None)
    assert not stopped and net == pytest.approx((90 * 0.9995 / 100.12 - 1) * 100)


def test_t_statistic_clusters_by_day():
    same_day = [("d1", 1.0), ("d1", 1.0), ("d1", 1.0), ("d2", -1.0), ("d2", -1.0), ("d2", -1.0)]
    n, m, t = dd.stats(same_day)
    assert (n, m) == (6, 0.0)
    spread = [(f"d{i}", v) for i, v in enumerate([2.0, 1.0, 3.0, 2.0, 1.5, 2.5])]
    assert dd.stats(spread)[2] > 5
    # six trades on two days carry no more information than two observations
    two_days = [("d1", 2.0), ("d1", 2.1), ("d1", 1.9), ("d2", 1.0), ("d2", 1.1), ("d2", 0.9)]
    six_days = [(f"d{i}", v) for i, (_, v) in enumerate(two_days)]
    assert dd.stats(two_days)[2] < dd.stats(six_days)[2]
    assert dd.stats([])[0] == 0


def test_candidates_use_price_and_liquidity_from_before_the_event():
    # 70 quiet days at 8 $, a +10 % day on triple volume, then a rise to 30 $ by the end.
    closes = [8.0] * 70 + [8.8] + [8.8 + i for i in range(1, 22)]
    bars = _bars(closes)
    bars[70] = bars[70].model_copy(update={"volume": 3_000_000, "low": 8.0, "high": 8.82})
    kwargs = dict(t_small=1_000_000, t_large=10**12)
    assert dd.find_candidates({"UP": bars}, **kwargs) == []                       # under 10 $ on the day
    old = dd.find_candidates({"UP": bars}, point_in_time=False, **kwargs)
    assert [c["i"] for c in old] == [70]                                          # the old study took it


def test_report_prints_every_table(capsys):
    closes = [20.0] * 70 + [21.6] + [22.0] * 15
    bars = _bars(closes)
    bars[70] = bars[70].model_copy(update={"volume": 3_000_000, "low": 20.0, "high": 21.7})
    cands = dd.find_candidates({"ABC": bars}, 1_000_000, 10**12)
    assert len(cands) == 1
    cands[0].update({"class": "earnings", "kind": "preview", "heads": ["Earnings Preview: ABC"]})
    base = [(bars[i].timestamp, dd.trade(bars, i, 3.0)[0]) for i in range(60, 70, 5)]
    dd.report({"ABC": bars}, cands, base, n_end_of_sample=1, days=730)
    out = capsys.readouterr().out
    for title in ("## 1. The live rule (1 trades", "## 2. Every strong up-day", "## 3. Earnings headlines by kind",
                  "## 4. Stop width", "example preview: ABC", "| none |"):
        assert title in out
