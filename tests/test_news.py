from datetime import datetime, timedelta, timezone

from data.news import NewsItem
from models import Bar


def test_earnings_headlines_are_recognised():
    t = datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert NewsItem(t, "Netflix Q3 EPS Beats Estimates", ["NFLX"]).is_earnings
    assert NewsItem(t, "Apple raises full-year guidance", ["AAPL"]).is_earnings
    assert not NewsItem(t, "Tesla recalls 50,000 vehicles", ["TSLA"]).is_earnings


def test_study_finds_a_news_jump_and_measures_the_drift(monkeypatch, capsys):
    import run_news_study as study
    day0 = datetime(2026, 1, 5, 5, 0, tzinfo=timezone.utc)
    closes = [100.0] * 30 + [106.0] + [107.0 + i for i in range(12)]
    bars = [Bar(timestamp=day0 + timedelta(days=i), open=c, high=c * 1.01, low=c * 0.995, close=c,
                volume=5_000.0 if i == 30 else 1_000.0) for i, c in enumerate(closes)]
    monkeypatch.setattr(study, "fetch_bars", lambda *a, **k: bars)
    monkeypatch.setattr(study, "fetch_news",
                        lambda sym, start, end, limit=50: [NewsItem(end, "XYZ Q3 earnings beat", [sym])])
    study.main(["365", "XYZ"])
    out = capsys.readouterr().out
    assert "XYZ: 1 events" in out
    assert "| up | earnings | 1 |" in out
