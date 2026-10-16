from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from brief.schedule import BriefState
from broker.mock import MockBroker
from data.news import NewsItem
from engine import earnings_drift as drift
from models import Clock, OrderType

NY = ZoneInfo("America/New_York")


def _clock(day=date(2026, 10, 15), h=15, m=45):
    ts = datetime(day.year, day.month, day.day, h, m, tzinfo=NY)
    return Clock(timestamp=ts, is_open=True, next_open=ts + timedelta(days=1),
                 next_close=datetime(day.year, day.month, day.day, 16, 0, tzinfo=NY))


def _snap(prev=20.0, c=21.6, h=21.7, l=20.1, v=300_000, day="2026-10-15"):
    return {"o": 20.2, "h": h, "l": l, "c": c, "v": v, "day": day, "prev_close": prev, "price": c}


UNIVERSE = {"ABC": {"avg_vol": 100_000}, "XYZ": {"avg_vol": 100_000}, "LOW": {"avg_vol": 100_000}}


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("DRIFT_ENABLED", "true")
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "log.jsonl"))


def test_trading_days_skip_weekends():
    assert drift.add_trading_days(date(2026, 10, 15), 10) == date(2026, 10, 29)  # Thu -> Thu two weeks on
    assert drift.add_trading_days(date(2026, 10, 16), 1) == date(2026, 10, 19)   # Fri -> Mon


def test_candidates_need_move_range_and_volume():
    snaps = {"ABC": _snap(),                       # +8 %, near the high, 3x volume
             "XYZ": _snap(c=21.0, h=21.7),         # +5 %: too small
             "LOW": _snap(c=21.6, h=23.0)}         # +8 % but faded from the high
    assert [c[0] for c in drift.candidates(UNIVERSE, snaps, "2026-10-15")] == ["ABC"]
    assert drift.candidates(UNIVERSE, {"ABC": _snap(v=150_000)}, "2026-10-15") == []


def test_buys_on_earnings_with_stop_and_records_the_exit_date(live):
    b = MockBroker()
    b.set_price("ABC", 21.6)
    b.set_price("XYZ", 21.6)
    state = BriefState()
    news = lambda syms, start, end: {"ABC": [NewsItem(end, "ABC Q3 earnings beat, raises guidance", ["ABC"])],
                                     "XYZ": [NewsItem(end, "XYZ wins contract", ["XYZ"])]}
    events = drift.run(b, state, _clock(), universe_fn=lambda d: UNIVERSE,
                       snapshots_fn=lambda syms: {"ABC": _snap(), "XYZ": _snap()}, news_fn=news)
    assert "ABC" in b.get_positions() and "XYZ" not in b.get_positions()   # no earnings headline
    qty = b.get_positions()["ABC"].qty
    assert qty == int(100_000 * 0.0015 / (21.6 * 0.03))                     # 0.15 % risk, 3 % stop
    stops = [o for o in b.resting if o.type == OrderType.STOP and o.symbol == "ABC"]
    assert stops and stops[0].stop_price == round(21.6 * 0.97, 2)
    assert state.drift["ABC"]["exit_on"] == "2026-10-29" and "Earnings-Drift" in events[0]
    assert not drift.is_due(_clock(), state)                               # once per day


def test_drift_budget_caps_total_exposure(live, monkeypatch):
    monkeypatch.setattr(drift.config, "DRIFT_MAX_EXPOSURE_PCT", 0.06)        # room for about one position
    b = MockBroker()
    universe = {f"S{i}": {"avg_vol": 100_000} for i in range(3)}
    for s in universe:
        b.set_price(s, 21.6)
    state = BriefState()
    drift.run(b, state, _clock(), universe_fn=lambda d: universe,
              snapshots_fn=lambda syms: {s: _snap() for s in syms},
              news_fn=lambda syms, start, end: {s: [NewsItem(end, f"{s} Q3 results", [s])] for s in syms})
    held = sum(p.qty * 21.6 for p in b.get_positions().values())
    assert len(b.get_positions()) >= 1 and held <= 100_000 * 0.06 + 1


def test_sells_after_the_holding_period(live):
    b = MockBroker()
    b.set_price("ABC", 21.6)
    state = BriefState()
    drift.run(b, state, _clock(), universe_fn=lambda d: UNIVERSE,
              snapshots_fn=lambda syms: {"ABC": _snap()},
              news_fn=lambda syms, start, end: {"ABC": [NewsItem(end, "ABC earnings beat", ["ABC"])]})
    b.set_price("ABC", 23.0)
    events = drift.run(b, state, _clock(day=date(2026, 10, 29)), universe_fn=lambda d: {},
                       snapshots_fn=lambda syms: {}, news_fn=lambda *a: {})
    assert "ABC" not in b.get_positions() and state.drift == {}
    assert "10 Handelstage um" in events[0] and "ABC" in state.own_exits


def test_due_only_shortly_before_the_close():
    s = BriefState()
    assert drift.is_due(_clock(h=15, m=45), s)
    assert not drift.is_due(_clock(h=14, m=0), s)
    assert not drift.is_due(_clock(h=15, m=58), s)


def test_universe_drops_large_caps_illiquid_and_own_instruments():
    from models import Bar
    start = datetime(2026, 8, 1, 4, 0, tzinfo=ZoneInfo("UTC"))

    def bars(price, vol):
        return [Bar(timestamp=start + timedelta(days=i), open=price, high=price, low=price, close=price,
                    volume=vol) for i in range(40)]

    data = {"BIG": bars(100, 1_000_000), "MID": bars(50, 100_000), "SMALL": bars(20, 100_000),
            "THIN": bars(20, 1_000), "CHEAP": bars(5, 1_000_000), "SPY": bars(600, 1_000_000)}
    drift._universe_cache.clear()
    u = drift.build_universe("2026-10-16", symbols=list(data), bars_fn=lambda syms, days: {s: data[s] for s in syms})
    assert set(u) == {"MID", "SMALL"}            # BIG = top third; THIN/CHEAP filtered; SPY is ours
    assert u["MID"]["avg_vol"] == 100_000
    drift._universe_cache.clear()
