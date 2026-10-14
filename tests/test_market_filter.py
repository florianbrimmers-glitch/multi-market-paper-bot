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


def test_live_tick_halves_mean_reversion_on_a_sell_off(monkeypatch, tmp_path):
    import engine.trader
    from broker.mock import MockBroker
    from engine import run_tick
    from tests import fixtures as fx
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("DRY_RUN", "false")
    now = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)  # 11:00 New York
    monkeypatch.setattr(engine.trader, "_now", lambda: now)
    spy_day = _spy_day([99.5, 99.0, 98.8, 98.6, 98.5, 98.4])

    def fetch(inst):
        if inst.symbol == "SPY":
            return spy_day
        return fx.flat_then_drop() if inst.symbol == "XLE" else fx.flat_series()

    def qty(threshold):
        monkeypatch.setenv("MARKET_FILTER_PCT", threshold)
        b = MockBroker()
        b.set_price("XLE", 90.0)
        rec = next(r for r in run_tick(b, fetch=fetch, run_id="t") if r.symbol == "XLE")
        assert rec.action_taken == "submitted"
        return rec.plan

    off, on = qty("0"), qty("1.0")
    assert on.qty == off.qty // 2 and "market sell-off" in on.reason
