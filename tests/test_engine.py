from __future__ import annotations

import pytest

import config
from broker.mock import MockBroker
from engine import run_tick
from models import OrderType, Side
from tests import fixtures as fx


def _fetch(drop_symbols=("SPY",)):
    return lambda inst: fx.flat_then_drop() if inst.symbol in drop_symbols else fx.flat_series()


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    p = tmp_path / "log.jsonl"
    monkeypatch.setenv("DECISION_LOG_PATH", str(p))
    return p


def test_dry_run_logs_but_places_nothing(log_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    broker = MockBroker()
    records = run_tick(broker, fetch=_fetch(), run_id="t")
    assert len(records) == len(config.INSTRUMENTS)
    spy = next(r for r in records if r.symbol == "SPY")
    assert spy.action_taken == "skipped:dry_run(entry)" and spy.plan is not None
    assert broker.orders == []
    assert len(log_path.read_text().splitlines()) == len(config.INSTRUMENTS)


def test_live_paper_submits_entry_and_protective_stop(log_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    broker = MockBroker()
    broker.set_price("SPY", 90.0)
    spy = next(r for r in run_tick(broker, fetch=_fetch(), run_id="t") if r.symbol == "SPY")
    assert spy.action_taken == "submitted"
    assert "SPY" in broker.get_positions()
    stops = [o for o in broker.resting if o.type == OrderType.STOP]
    assert len(stops) == 1 and stops[0].side == Side.SELL
    assert stops[0].stop_price == spy.plan.stop_price


def test_correlation_filter_applies_within_one_tick(log_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    broker = MockBroker()
    broker.set_price("SPY", 90.0)
    broker.set_price("QQQ", 90.0)
    records = {r.symbol: r for r in run_tick(broker, fetch=_fetch(("SPY", "QQQ")), run_id="t")}
    assert records["SPY"].action_taken == "submitted"
    assert records["QQQ"].action_taken.startswith("skipped:correlation filter")


def test_one_failing_instrument_does_not_abort_tick(log_path):
    def fetch(inst):
        if inst.symbol == "GLD":
            raise RuntimeError("boom")
        return fx.flat_series()
    records = run_tick(MockBroker(), fetch=fetch, run_id="t")
    assert len(records) == len(config.INSTRUMENTS)
    assert "boom" in next(r for r in records if r.symbol == "GLD").error


def test_paper_only_guard_rejects_live_url(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_URL", "https://api.alpaca.markets")
    with pytest.raises(ValueError, match="paper-only"):
        config.alpaca_trading_url()


def test_paper_only_guard_rejects_lookalike(monkeypatch):
    monkeypatch.setenv("ALPACA_TRADING_URL", "https://api.alpaca.markets/?x=paper-api")
    with pytest.raises(ValueError):
        config.alpaca_trading_url()


def test_default_url_is_paper(monkeypatch):
    monkeypatch.delenv("ALPACA_TRADING_URL", raising=False)
    assert config.alpaca_trading_url().startswith("https://paper-api.")


def test_key_hint_never_leaks_key(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "AKSECRETVALUE123\n")
    monkeypatch.setenv("ALPACA_API_SECRET", "s" * 40)
    hint = config.describe_alpaca_key()
    assert "LIVE key" in hint and "SECRETVALUE" not in hint and "length 16" in hint
    assert config.alpaca_api_key() == "AKSECRETVALUE123"


def test_german_index_etfs_share_a_correlation_group(log_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    groups = {i.symbol: i.correlation_group for i in config.INSTRUMENTS}
    assert groups["EWG"] == groups["DAX"] == "germany_index"
    broker = MockBroker()
    broker.set_price("EWG", 100.0)
    broker.set_price("DAX", 100.0)
    fetch = lambda inst: fx.uptrend() if inst.symbol in ("EWG", "DAX") else fx.flat_series()
    records = {r.symbol: r for r in run_tick(broker, fetch=fetch, run_id="t")}
    assert records["EWG"].action_taken == "submitted"
    assert records["DAX"].action_taken.startswith("skipped:correlation filter")
