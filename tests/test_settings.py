"""Settings: how values from the environment are read, and which repository variables arrive.

A repository variable that does not exist reaches the loop job as an empty string, and a value
typed by hand may carry blanks, a decimal comma or a German yes/no. None of that may place
orders by accident, switch a strategy silently, or stop every tick with an exception.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx
import pytest

import config
from models import Account

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_warnings():
    config._warned.clear()


@pytest.mark.parametrize("value", [None, "", "  ", "true", "true ", " TRUE\n", "1", "yes", "on", "ja", "y", "maybe"])
def test_dry_run_unless_an_explicit_no(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("DRY_RUN", raising=False)
    else:
        monkeypatch.setenv("DRY_RUN", value)
    assert config.dry_run() is True


@pytest.mark.parametrize("value", ["false", "False ", "0", "no", "nein", "off"])
def test_orders_only_on_an_explicit_no(monkeypatch, value):
    monkeypatch.setenv("DRY_RUN", value)
    assert config.dry_run() is False


@pytest.mark.parametrize("value, enabled", [
    (None, True), ("", True), ("true", True), ("ja", True), ("an", True),
    ("false", False), (" FALSE ", False), ("0", False), ("off", False), ("nein", False), ("aus", False),
])
def test_drift_switch(monkeypatch, value, enabled):
    if value is None:
        monkeypatch.delenv("DRIFT_ENABLED", raising=False)
    else:
        monkeypatch.setenv("DRIFT_ENABLED", value)
    assert config.drift_enabled() is enabled


def test_unrecognised_switch_keeps_the_default_and_is_logged_once(monkeypatch, caplog):
    monkeypatch.setenv("DRIFT_ENABLED", "vielleicht")
    with caplog.at_level(logging.WARNING, logger="config"):
        assert config.drift_enabled() is True
        assert config.drift_enabled() is True
    assert [r.getMessage() for r in caplog.records] == [
        "setting DRIFT_ENABLED='vielleicht' is neither yes nor no — using the default (True)"]


@pytest.mark.parametrize("name, read, value, expected", [
    ("MAX_POSITION_PCT", config.max_position_pct, "", 0.5),          # unset repository variable
    ("MAX_POSITION_PCT", config.max_position_pct, "0,25", 0.25),     # German decimal comma
    ("MAX_POSITION_PCT", config.max_position_pct, " 0.3 ", 0.3),
    ("MAX_POSITION_PCT", config.max_position_pct, "50", 0.5),        # meant as a percentage: refused
    ("MAX_POSITION_PCT", config.max_position_pct, "nan", 0.5),
    ("MAX_POSITION_PCT", config.max_position_pct, "viel", 0.5),
    ("OVERNIGHT_EXPOSURE_CAP", config.overnight_exposure_cap, "", 2.0),
    ("OVERNIGHT_EXPOSURE_CAP", config.overnight_exposure_cap, "1,5", 1.5),
    ("OVERNIGHT_EXPOSURE_CAP", config.overnight_exposure_cap, "-1", 2.0),
    ("OVERNIGHT_EXPOSURE_CAP", config.overnight_exposure_cap, "0", 0.0),
    ("RISK_PER_TRADE_PCT", config.risk_per_trade_pct, "0.5", 0.005),  # 50 % risk per trade: refused
    ("OPENING_DELAY_MIN", config.opening_delay_min, "abc", 30),
])
def test_numbers_never_raise_and_stay_in_range(monkeypatch, name, read, value, expected):
    monkeypatch.setenv(name, value)
    assert read() == pytest.approx(expected)


def test_pause_lists_are_normalised(monkeypatch, caplog):
    monkeypatch.setenv("PAUSED_STRATEGIES", "trend-following, Momentum_Breakout,tippfehler")
    monkeypatch.setenv("PAUSED_SYMBOLS", "db, bntx")
    with caplog.at_level(logging.WARNING, logger="config"):
        assert config.paused_strategies() == {"trend_following", "momentum_breakout", "tippfehler"}
        assert config.paused_symbols() == {"DB", "BNTX"}
    assert any("PAUSED_STRATEGIES='tippfehler'" in r.getMessage() for r in caplog.records)
    monkeypatch.setenv("PAUSED_STRATEGIES", "")
    assert config.paused_strategies() == set()  # an explicit empty list still means: nothing paused


def test_settings_line_names_what_is_in_force(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("DRIFT_ENABLED", "false")
    monkeypatch.setenv("OVERNIGHT_EXPOSURE_CAP", "1,5")
    monkeypatch.delenv("PAUSED_STRATEGIES")
    monkeypatch.delenv("PAUSED_SYMBOLS")
    line = config.describe_settings()
    assert "dry_run=False drift_enabled=False eod_flat_mode=off overnight_exposure_cap=1.5" in line
    assert "paused_strategies=['momentum_breakout', 'trend_following'] paused_symbols=['DB']" in line


# --- what the loop workflow hands to the bot --------------------------------
def _loop_env() -> dict[str, str]:
    """The `env:` block of the loop job in trade-loop.yml (no YAML library in the requirements)."""
    lines = (ROOT / ".github/workflows/trade-loop.yml").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "env:")
    indent = len(lines[start]) - len(lines[start].lstrip())
    env: dict[str, str] = {}
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip()) <= indent:
            break
        key, _, value = line.strip().partition(":")
        env[key] = value.strip()
    return env


def test_every_repository_variable_the_readme_names_reaches_the_bot():
    env = _loop_env()
    readme = (ROOT / "README.md").read_text()
    named = set(re.findall(r"Repo-Variable `([A-Z_]+)`", readme))
    listed = re.search(r"Der Bot liest nur\s+(.*?)\.", readme, re.S)
    named |= set(re.findall(r"`([A-Z_]+)`", listed.group(1)))
    assert named == {"DRY_RUN", "EOD_FLAT_MODE", "DRIFT_ENABLED", "OVERNIGHT_EXPOSURE_CAP"}
    for name in named:
        assert f"vars.{name}" in env.get(name, ""), f"{name} is documented but not passed to the loop job"


def test_the_loop_job_passes_nothing_config_does_not_read():
    source = (ROOT / "config.py").read_text() + (ROOT / "brief/deliver.py").read_text()
    for name in _loop_env():
        assert f'"{name}"' in source, f"{name} is passed to the loop job but nothing reads it"


# --- margin figures for the buying-power measurement ------------------------
def test_alpaca_account_carries_the_margin_figures(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "secret")
    from broker.alpaca import AlpacaBroker
    payload = {"equity": "97000.5", "cash": "-190000", "buying_power": "42000", "last_equity": "97500",
               "long_market_value": "289150.25", "maintenance_margin": "86745.07", "initial_margin": "144575.1",
               "daytrading_buying_power": "42000", "regt_buying_power": "0", "multiplier": "4"}
    broker = AlpacaBroker()
    broker._client = httpx.Client(base_url=broker.base_url, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=json.dumps(payload))))
    account = broker.get_account()
    assert account.long_market_value == 289150.25 and account.multiplier == 4.0
    assert account.margin_line() == (
        "equity=97000.50 buying_power=42000.00 cash=-190000.00 long=289150.25 maintenance=86745.07 "
        "initial=144575.10 dtbp=42000.00 regt_bp=0.00 multiplier=4.00")


def test_margin_line_leaves_out_what_the_broker_did_not_report():
    assert Account(equity=100.0, cash=50.0, buying_power=50.0).margin_line() == (
        "equity=100.00 buying_power=50.00 cash=50.00")


def test_account_is_logged_at_tick_start_and_after_each_buy(monkeypatch, tmp_path, caplog):
    """The reads are measurement only: a failing one must not disturb the tick."""
    from broker.mock import MockBroker
    from engine import run_tick
    from tests import fixtures as fx

    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("DECISION_LOG_PATH", str(tmp_path / "log.jsonl"))
    broker = MockBroker()
    broker.set_price("SPY", 90.0)
    broker.set_price("QQQ", 90.0)
    calls = {"n": 0}
    real = broker.get_account

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:  # the first read after a buy fails
            raise RuntimeError("timeout")
        return real()

    monkeypatch.setattr(broker, "get_account", flaky)
    with caplog.at_level(logging.INFO, logger="engine.trader"):
        records = run_tick(broker, fetch=lambda inst: (
            fx.flat_then_drop() if inst.symbol in ("SPY", "QQQ") else fx.flat_series()))
    bought = [r for r in records if r.action_taken == "submitted"]
    assert len(bought) == 2
    messages = [r.getMessage() for r in caplog.records]
    assert sum(m.startswith("account at tick start: equity=") for m in messages) == 1
    assert sum(m.startswith("could not read the account after buying") for m in messages) == 1
    assert sum(m.startswith("account after buying") for m in messages) == len(bought) - 1
