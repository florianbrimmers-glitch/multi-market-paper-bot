from __future__ import annotations

import pytest

from indicators import atr
from models import Account, Position, Side
from risk import size_entry, stop_distance
from tests import fixtures as fx

GROUPS = {"SPY": "us_equity_index", "QQQ": "us_equity_index", "GLD": None}


def _account(equity: float = 100_000.0) -> Account:
    return Account(equity=equity, cash=equity, buying_power=equity, last_equity=equity)


def test_flat_series_atr_is_known():
    assert atr(fx.flat_series(), 14) == pytest.approx(0.2)


def test_hard_stop_caps_distance_at_one_percent():
    wide = [fx.bar(100.0, i, high=110.0, low=90.0) for i in range(20)]
    assert stop_distance(100.0, wide) == pytest.approx(1.0)


def test_sizing_uses_atr_and_notional_cap():
    dec = size_entry("SPY", 100.0, fx.flat_series(), _account(), positions={})
    assert dec.approved
    # risk $500 / stop 0.3 = 1666 shares, capped by 25% notional => 250 shares
    assert dec.plan.qty == 250
    assert dec.plan.stop_price == 99.70
    assert dec.plan.risk_amount == pytest.approx(75.0)
    assert dec.plan.side == Side.BUY


def test_risk_budget_binds_when_stop_is_wide():
    wide = [fx.bar(100.0, i, high=110.0, low=90.0) for i in range(20)]
    dec = size_entry("SPY", 100.0, wide, _account(), positions={})
    # stop capped at $1 -> $500 / $1 = 500 shares, but 25% cap = 250; risk <= 0.5% of equity
    assert dec.plan.risk_amount <= 500.0


def test_correlation_filter_blocks_second_index_long():
    positions = {"SPY": Position(symbol="SPY", qty=10, avg_entry_price=100.0)}
    dec = size_entry("QQQ", 100.0, fx.flat_series(), _account(), positions,
                     correlation_group="us_equity_index", group_of=GROUPS)
    assert not dec.approved
    assert "correlation filter" in dec.reason


def test_uncorrelated_instrument_is_allowed():
    positions = {"SPY": Position(symbol="SPY", qty=10, avg_entry_price=100.0)}
    assert size_entry("GLD", 100.0, fx.flat_series(), _account(), positions, group_of=GROUPS).approved


def test_already_long_is_rejected():
    positions = {"SPY": Position(symbol="SPY", qty=10, avg_entry_price=100.0)}
    assert size_entry("SPY", 100.0, fx.flat_series(), _account(), positions).reason == "already long"


def test_buying_power_limits_size():
    acct = Account(equity=100_000.0, cash=1_000.0, buying_power=1_000.0)
    dec = size_entry("SPY", 100.0, fx.flat_series(), acct, positions={})
    assert dec.plan.qty == 10


def test_crypto_allows_fractional_qty():
    dec = size_entry("BTCUSD", 50_000.0, fx.flat_series(level=50_000.0), _account(), {},
                     allow_fractional=True)
    assert dec.plan.qty == pytest.approx(0.5)
