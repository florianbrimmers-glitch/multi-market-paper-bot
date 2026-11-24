"""ETF core holding (owner's decision of 24.11.2026): idle cash goes into MSCI World, no stop."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import config
from brief.schedule import BriefState
from broker.mock import MockBroker
from engine import core_holding as core
from models import Clock, OrderType

NY = ZoneInfo("America/New_York")


def _clock(h=10, m=0, day=(2026, 11, 24), close_h=16):
    ts = datetime(*day, h, m, tzinfo=NY)
    return Clock(timestamp=ts, is_open=True, next_open=ts + timedelta(days=1),
                 next_close=datetime(*day, close_h, 0, tzinfo=NY))


@pytest.fixture(autouse=True)
def _core_on(monkeypatch):
    monkeypatch.setenv("CORE_ENABLED", "true")


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "log.jsonl"))


def test_due_once_a_day_from_ten_to_twenty_minutes_before_the_close(monkeypatch):
    state = BriefState()
    assert not core.is_due(_clock(9, 45), state)              # opening phase
    assert core.is_due(_clock(10, 0), state)
    assert core.is_due(_clock(15, 40), state)
    assert not core.is_due(_clock(15, 45), state)              # last 20 minutes
    assert not core.is_due(_clock(12, 45, close_h=13), state)  # half day: 15 minutes before a 13:00 close
    state.core_day = "2026-11-24"
    assert not core.is_due(_clock(11, 0), state)               # once per day
    state.core_day = ""
    monkeypatch.setenv("CORE_ENABLED", "false")
    assert not core.is_due(_clock(11, 0), state)
    closed = _clock(11, 0).model_copy(update={"is_open": False})
    monkeypatch.setenv("CORE_ENABLED", "true")
    assert not core.is_due(closed, state)


def test_buys_the_etf_with_idle_cash_and_no_stop(live):
    b = MockBroker()
    b.set_price("URTH", 180.0)
    state = BriefState()
    events = core.run(b, state, _clock(), price_of=lambda s: 180.0)
    pos = b.get_positions()["URTH"]
    assert pos.qty == int(100_000 * core.CASH_BUFFER // 180)            # 550 shares, cash only
    assert not [o for o in b.resting if o.symbol == "URTH" and o.type == OrderType.STOP]
    assert "**URTH** gekauft: 550 Stück" in events[0] and "ohne Stop" in events[0]
    assert state.core_day == "2026-11-24"


def test_uses_only_cash_and_tops_up_when_other_positions_free_it(live):
    b = MockBroker()
    b.set_price("MBGL", 20.0)
    from models import Order, Side
    b.submit_order(Order(symbol="MBGL", side=Side.BUY, qty=1000, stop_loss=19.4))   # 20,000 $ elsewhere
    b.set_price("URTH", 100.0)
    core.run(b, BriefState(), _clock(), price_of=lambda s: 100.0)
    assert b.get_positions()["URTH"].qty == int(80_000 * core.CASH_BUFFER // 100)   # 792, no margin
    b.close_position("MBGL")                                                         # cash freed
    events = core.run(b, BriefState(), _clock(day=(2026, 11, 25)), price_of=lambda s: 100.0)
    assert b.get_positions()["URTH"].qty > 792 and "gekauft" in events[0]


def test_nothing_to_do_when_the_target_is_reached_or_no_price(live):
    b = MockBroker()
    b.set_price("URTH", 100.0)
    core.run(b, BriefState(), _clock(), price_of=lambda s: 100.0)
    assert core.run(b, BriefState(), _clock(day=(2026, 11, 25)), price_of=lambda s: 100.0) == []
    events = core.run(MockBroker(), BriefState(), _clock(), price_of=lambda s: None)
    assert "kein aktueller Kurs" in events[0]


def test_dry_run_places_no_order(monkeypatch, tmp_path):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "log.jsonl"))
    b = MockBroker()
    events = core.run(b, BriefState(), _clock(), price_of=lambda s: 100.0)
    assert "URTH" not in b.get_positions() and "Testlauf" in events[0]


def test_the_loop_runs_the_top_up_and_the_stop_safety_net_leaves_the_etf_alone(live, monkeypatch):
    """Through the real loop: URTH is bought, gets no protective stop at the next tick, and is not
    reported as a stop-out."""
    import run_loop
    from tests import fixtures as fx
    b = MockBroker()
    b.set_price("URTH", 100.0)
    b.fixed_clock = _clock(11, 0)
    monkeypatch.setattr(core, "_price", lambda s: 100.0)
    posts = []
    state = BriefState()
    kw = dict(fetch=lambda inst: fx.flat_series(), notify=posts.append, publisher=lambda *a: None,
              build=lambda *a: "", price_of=lambda inst: None, drift=None)
    monkeypatch.setattr(run_loop, "save_state", lambda s: None)
    run_loop.loop_once(b, state, **kw)
    assert "URTH" in b.get_positions() and "**URTH** gekauft" in posts[0]
    run_loop.loop_once(b, state, **kw)          # same day: no second buy, no stop, no stop-out line
    assert len(posts) == 1
    assert not [o for o in b.resting if o.symbol == "URTH"]
    assert config.CORE_SYMBOL == "URTH"
