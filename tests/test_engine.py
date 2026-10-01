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


def test_entry_and_stop_use_live_price_not_stale_bar_close(log_path, monkeypatch):
    """Regression (2026-09-25): USO signal close 148.78 but fill 149.96 -> stop was 1.8% away."""
    monkeypatch.setenv("DRY_RUN", "false")
    broker = MockBroker()
    broker.set_price("USO", 149.96)
    fetch = lambda inst: fx.uptrend(start=100.0) if inst.symbol == "USO" else fx.flat_series()
    recs = run_tick(broker, fetch=fetch, run_id="t", price_of=lambda inst: 149.96 if inst.symbol == "USO" else None)
    uso = next(r for r in recs if r.symbol == "USO")
    assert uso.plan.entry_price == 149.96
    assert uso.plan.stop_price == pytest.approx(149.96 * 0.99, abs=0.01)  # within the 1% cap of the real price


def test_mean_reversion_waits_out_the_opening_phase(log_path, monkeypatch):
    from datetime import datetime, timezone

    import engine.trader
    monkeypatch.setenv("DRY_RUN", "false")
    # 13:45 UTC = 09:45 New York: 15 minutes into the session
    monkeypatch.setattr(engine.trader, "_now", lambda: datetime(2026, 9, 28, 13, 45, tzinfo=timezone.utc))
    broker = MockBroker()
    broker.set_price("SPY", 90.0)
    broker.set_price("GLD", 100.0)
    fetch = lambda inst: (fx.flat_then_drop() if inst.symbol == "SPY"
                          else fx.uptrend() if inst.symbol == "GLD" else fx.flat_series())
    records = {r.symbol: r for r in run_tick(broker, fetch=fetch, run_id="t")}
    assert records["SPY"].action_taken == "skipped:opening phase"
    assert records["GLD"].action_taken == "submitted"  # trend following is not delayed
    assert "SPY" not in broker.get_positions()


def test_minutes_since_us_open_handles_new_york_time():
    from datetime import datetime, timezone

    from engine.trader import minutes_since_us_open
    assert minutes_since_us_open(datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc)) == 0  # EDT
    assert minutes_since_us_open(datetime(2026, 12, 1, 15, 0, tzinfo=timezone.utc)) == 30  # EST


def _closing_clock(minutes_left: float):
    from datetime import datetime, timedelta, timezone

    from models import Clock
    now = datetime(2026, 9, 29, 19, 45, tzinfo=timezone.utc)
    return Clock(timestamp=now, is_open=True, next_open=now + timedelta(hours=18),
                 next_close=now + timedelta(minutes=minutes_left))


def _held(broker, symbol, entry, now_price):
    from models import Order, Side
    broker.set_price(symbol, entry)
    broker.submit_order(Order(symbol=symbol, side=Side.BUY, qty=10, stop_loss=round(entry * 0.99, 2)))
    broker.set_price(symbol, now_price)


@pytest.mark.parametrize("mode,winner_sold,loser_sold", [("all", True, True), ("winners", True, False),
                                                         ("off", False, False)])
def test_mean_reversion_is_flattened_before_the_close(log_path, monkeypatch, mode, winner_sold, loser_sold):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("EOD_FLAT_MODE", mode)
    broker = MockBroker()
    broker.fixed_clock = _closing_clock(14.7)
    _held(broker, "SPY", 100.0, 99.5)   # below the mean (no exit signal of its own), in profit? no: loser
    _held(broker, "SAP", 100.0, 100.5)  # in profit
    _held(broker, "GLD", 100.0, 99.5)   # trend following: never flattened
    fetch = lambda inst: (fx.flat_then_drop(drop_to=99.0) if inst.symbol in ("SPY", "SAP")
                          else fx.uptrend() if inst.symbol == "GLD" else fx.flat_series())
    records = {r.symbol: r for r in run_tick(broker, fetch=fetch, run_id="t")}
    assert (records["SAP"].action_taken == "exit") is winner_sold
    assert (records["SPY"].action_taken == "exit") is loser_sold
    assert records["GLD"].action_taken != "exit" and "GLD" in broker.get_positions()
    if winner_sold:
        assert records["SAP"].signal.reason == "Glattstellung vor Börsenschluss"


def test_no_mean_reversion_entry_in_the_last_30_minutes(log_path, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    broker = MockBroker()
    broker.fixed_clock = _closing_clock(29.7)
    broker.set_price("SPY", 90.0)
    fetch = lambda inst: fx.flat_then_drop() if inst.symbol == "SPY" else fx.flat_series()
    spy = next(r for r in run_tick(broker, fetch=fetch, run_id="t") if r.symbol == "SPY")
    assert spy.action_taken == "skipped:closing phase"


def test_crypto_stop_uses_the_planned_distance_from_the_real_fill(log_path, monkeypatch):
    """Regression (2026-09-30): the BTC stop was placed at the 1% cap, not the planned 0.76%."""
    from models import OrderType
    monkeypatch.setenv("DRY_RUN", "false")
    broker = MockBroker()
    broker.set_price("BTC/USD", 110.0)
    fetch = lambda inst: fx.breakout_with_volume() if inst.symbol == "BTC/USD" else fx.flat_series()
    btc = next(r for r in run_tick(broker, fetch=fetch, run_id="t",
                                   price_of=lambda inst: 109.0 if inst.symbol == "BTC/USD" else None)
               if r.symbol == "BTC/USD")
    assert btc.action_taken == "submitted"
    stop = next(o for o in broker.resting if o.symbol == "BTC/USD")
    assert stop.type == OrderType.STOP_LIMIT
    assert btc.plan.entry_price == 110.0  # the real fill, not the 109 estimate
    assert stop.stop_price == btc.plan.stop_price < 110.0
    assert 110.0 - stop.stop_price < 110.0 * 0.01  # the planned (ATR) distance, not the 1% cap


def test_correlation_filter_counts_unfilled_entries(log_path, monkeypatch):
    """Regression (2026-10-01): SPY's entry hadn't filled yet, so QQQ was bought in the same tick."""
    monkeypatch.setenv("DRY_RUN", "false")
    broker = MockBroker()
    broker.fill_market = False
    broker.set_price("SPY", 90.0)
    broker.set_price("QQQ", 90.0)
    records = {r.symbol: r for r in run_tick(broker, fetch=_fetch(("SPY", "QQQ")), run_id="t")}
    assert records["SPY"].action_taken == "submitted"
    assert records["QQQ"].action_taken.startswith("skipped:correlation filter")
