"""Continuous loop: tick alignment, brief timing (exchange clock), state, issue delivery."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import logbuch
from brief.deliver import post_brief
from brief.schedule import BriefState, brief_due, load_state, save_state
from broker.mock import MockBroker
from models import Clock
from tests import fixtures as fx
from run_loop import loop_once, next_tick_after

NY = timezone(timedelta(hours=-4))


def clock(h: int, m: int, is_open: bool, day: int = 25) -> Clock:
    ts = datetime(2026, 9, day, h, m, tzinfo=NY)
    session_open = datetime(2026, 9, day, 9, 30, tzinfo=NY)
    next_open = session_open if ts < session_open else session_open + timedelta(days=1)
    return Clock(timestamp=ts, is_open=is_open, next_open=next_open,
                 next_close=datetime(2026, 9, day, 16, 0, tzinfo=NY))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("BRIEF_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


def test_ticks_align_to_quarter_hours_after_bar_close():
    t = datetime(2026, 9, 25, 13, 31, 5, tzinfo=timezone.utc)
    assert next_tick_after(t, 15) == datetime(2026, 9, 25, 13, 45, 20, tzinfo=timezone.utc)
    t = datetime(2026, 9, 25, 23, 50, tzinfo=timezone.utc)
    assert next_tick_after(t, 15) == datetime(2026, 9, 26, 0, 0, 20, tzinfo=timezone.utc)


def test_morning_brief_45_min_before_open_not_earlier():
    s = BriefState()
    assert brief_due(clock(8, 30, False), s) is None
    assert brief_due(clock(8, 45, False), s) == "morning"


def test_morning_brief_at_first_tick_when_loop_starts_late():
    assert brief_due(clock(11, 0, True), BriefState()) == "morning"


def test_night_brief_once_after_close_only_on_trading_days():
    s = BriefState(morning="2026-09-25")
    assert brief_due(clock(15, 45, True), s) is None
    assert brief_due(clock(16, 15, False), s) == "night"
    assert brief_due(clock(16, 15, False), BriefState(morning="2026-09-25", night="2026-09-25")) is None
    # weekend / holiday: no morning brief that day -> no night brief either
    assert brief_due(clock(16, 15, False, day=26), BriefState(morning="2026-09-25")) is None


def test_no_night_brief_before_the_open():
    assert brief_due(clock(9, 15, False), BriefState(morning="2026-09-25")) is None


def test_state_roundtrip():
    save_state(BriefState(morning="2026-09-25"))
    assert load_state().morning == "2026-09-25"


def test_loop_once_publishes_due_brief_and_records_it():
    b = MockBroker()
    b.fixed_clock = clock(9, 0, False)
    sent = []
    state = BriefState()
    flat = lambda inst: fx.flat_series()
    _, kind = loop_once(b, state, build=lambda k, _: f"{k} text",
                        publisher=lambda k, t: sent.append((k, t)), fetch=flat, notify=sent.append)
    assert kind == "morning" and sent == [("morning", "morning text")]
    assert load_state().morning == "2026-09-25"
    _, kind = loop_once(b, state, build=lambda k, _: "x", publisher=lambda k, t: sent.append(k), fetch=flat,
                        notify=sent.append)
    assert kind is None and len(sent) == 1


def test_prune_drops_old_records(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    with open(tmp_path / "log.jsonl", "w") as f:
        f.write(json.dumps({"run_id": "a", "evaluated_at": old}, separators=(",", ":")) + "\n")
        f.write(json.dumps({"run_id": "b", "evaluated_at": new}, separators=(",", ":")) + "\n")
    assert logbuch.prune(14) == 1
    assert '"run_id":"b"' in (tmp_path / "log.jsonl").read_text()


def test_post_brief_is_noop_outside_actions():
    assert post_brief("text", "morning") is False


def test_post_brief_creates_issue_once_then_comments(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, req.url.path))
        if req.method == "GET":
            return httpx.Response(200, json=[{"number": 7, "title": "Something else"}])
        if req.url.path.endswith("/issues"):
            return httpx.Response(201, json={"number": 8})
        return httpx.Response(201, json={})

    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    assert post_brief("hello", "night", client=client) is True
    assert calls == [("GET", "/repos/o/r/issues"), ("POST", "/repos/o/r/issues"),
                     ("POST", "/repos/o/r/issues/8/comments")]


def test_trade_events_are_posted_for_orders_not_holds(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    b = MockBroker()
    b.fixed_clock = clock(11, 0, True)
    b.set_price("SPY", 90.0)
    posted = []
    fetch = lambda inst: fx.flat_then_drop() if inst.symbol == "SPY" else fx.flat_series()
    loop_once(b, BriefState(morning="2026-09-25"), build=lambda k, _: "x", publisher=lambda k, t: None,
              fetch=fetch, notify=posted.append)
    assert len(posted) == 1
    assert "**SPY** submitted: buy" in posted[0] and "stop" in posted[0] and "(paper)" in posted[0]
    assert "QQQ" not in posted[0]  # holds are not reported


def test_no_trade_post_when_nothing_happens():
    b = MockBroker()
    b.fixed_clock = clock(11, 0, True)
    posted = []
    loop_once(b, BriefState(morning="2026-09-25"), build=lambda k, _: "x", publisher=lambda k, t: None,
              fetch=lambda inst: fx.flat_series(), notify=posted.append)
    assert posted == []
