from datetime import datetime, timedelta, timezone

from engine.market_filter import MarketMove, size_factor
from models import Bar


def _spy_day(closes, day=datetime(2026, 10, 14, 13, 30, tzinfo=timezone.utc)):  # 09:30 New York
    return [Bar(timestamp=day + timedelta(minutes=15 * i), open=100.0 if i == 0 else closes[i - 1],
                high=max(c, 100.0), low=min(c, 99.0), close=c, volume=1.0) for i, c in enumerate(closes)]


def test_move_since_the_open_without_look_ahead():
    bars = _spy_day([99.8, 99.5, 98.7, 98.0])
    m = MarketMove(bars)
    # At 10:00 New York the bars of 09:30 and 09:45 have closed; the 10:00 bar has not.
    assert round(m.pct(bars[2].timestamp), 2) == -0.5
    assert round(m.pct(bars[3].timestamp), 2) == -1.3
    assert round(m.pct(bars[3].timestamp, price=98.5), 2) == -1.5


def test_no_open_bar_means_no_filter():
    bars = _spy_day([99.0])[1:]  # the 09:30 bar is missing
    assert MarketMove(bars).pct(datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)) is None
    assert size_factor(None, 1.0) == 1.0


def test_half_size_only_below_the_threshold():
    assert size_factor(-1.2, 1.0) == 0.5
    assert size_factor(-0.8, 1.0) == 1.0
    assert size_factor(-3.0, 0.0) == 1.0  # 0 = filter off


def test_filter_comparison_runs_offline(monkeypatch, capsys):
    import run_screen
    from tests import fixtures as fx
    monkeypatch.setattr(run_screen, "fetch_bars", lambda *a, **k: fx.flat_then_drop())
    run_screen.compare_market_filter(["SPY", "QQQ"], 30, [1.0])
    out = capsys.readouterr().out
    assert "filter  0.0%: P/L" in out and "filter  1.0%: P/L" in out
