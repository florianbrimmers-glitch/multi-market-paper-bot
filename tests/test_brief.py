from __future__ import annotations

from brief import fallback_text, gather_brief_data, render_brief
from broker.mock import MockBroker
from models import Order, Side
from tests import fixtures as fx


def test_gather_brief_data_reports_positions_and_pl():
    b = MockBroker(100_000.0)
    b.mark_day_close()
    b.set_price("SPY", 100.0)
    b.submit_order(Order(symbol="SPY", side=Side.BUY, qty=10))
    b.set_price("SPY", 105.0)
    data = gather_brief_data("night", b, "2024-01-01T00:00:00Z",
                             market_bars={"SPY": fx.uptrend(5)}, recent_actions=["SPY: submitted"])
    assert data.day_pl == 50.0
    assert data.positions[0].symbol == "SPY"
    assert data.market_moves["SPY"] > 0


def test_fallback_text_is_labelled_paper():
    text = fallback_text(gather_brief_data("morning", MockBroker(50_000.0), "2024-01-01T00:00:00Z"))
    assert "PAPER-TRADING" in text and "50.000,00 $" in text and "keine Anlageberatung" in text


def test_render_falls_back_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    data = gather_brief_data("morning", MockBroker(), "2024-01-01T00:00:00Z")
    assert render_brief(data) == fallback_text(data)


def test_german_number_formatting():
    import i18n
    assert i18n.usd(99304.44) == "99.304,44 $"
    assert i18n.pct(-0.7) == "−0,70 %" and i18n.pct(1.6) == "+1,60 %"
    assert i18n.qty(168) == "168" and i18n.qty(0.5) == "0,5"
