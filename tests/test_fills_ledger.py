from __future__ import annotations

from run_fills import ledger


def _f(side, qty, price, sym="SPY", t="2026-10-30T13:31:00Z"):
    return {"symbol": sym, "side": side, "qty": str(qty), "price": str(price), "transaction_time": t}


def test_partial_sells_are_booked_once_each_against_the_average_cost():
    """30.10.2026: SPY's stop filled 14 and then 18 of 32 shares; the Trade-Log counted the loss twice."""
    fills = [_f("buy", 13, 768.05, t="2026-10-29T17:45:00Z"), _f("buy", 19, 768.07, t="2026-10-29T17:45:00Z"),
             _f("sell", 14, 759.30), _f("sell", 18, 762.02, t="2026-10-30T13:45:00Z")]
    realised, lots = ledger(fills)
    assert [(d, s) for d, s, _ in realised] == [("2026-10-30", "SPY")] * 2
    cost = (13 * 768.05 + 19 * 768.07) / 32
    assert round(sum(v for _, _, v in realised), 2) == round(14 * (759.30 - cost) + 18 * (762.02 - cost), 2)
    assert lots == {}


def test_open_lot_keeps_quantity_and_average_cost():
    realised, lots = ledger([_f("buy", 10, 100.0, "XLK"), _f("buy", 10, 102.0, "XLK"), _f("sell", 5, 103.0, "XLK")])
    assert round(realised[0][2], 2) == 10.0 and lots == {"XLK": (15.0, 101.0)}
