from __future__ import annotations

from datetime import date

from data.sec import EPS, REVENUE, yoy_growth


def _fact(start, end, filed, val):
    return {"start": start, "end": end, "filed": filed, "val": val, "form": "10-Q"}


FACTS = {"facts": {"us-gaap": {
    "Revenues": {"units": {"USD": [
        _fact("2025-07-01", "2025-09-30", "2025-11-05", 100.0),
        _fact("2026-07-01", "2026-09-30", "2026-11-04", 120.0),  # 10-Q a week after the release
        _fact("2026-01-01", "2026-12-31", "2027-02-20", 400.0),  # a year, not a quarter
    ]}},
    "EarningsPerShareDiluted": {"units": {"USD/shares": [
        _fact("2025-07-01", "2025-09-30", "2025-11-05", 0.50),
        _fact("2026-07-01", "2026-09-30", "2026-11-04", 0.40),
    ]}},
}}}


def test_growth_of_the_announced_quarter_against_a_year_earlier():
    assert round(yoy_growth(FACTS, REVENUE, date(2026, 10, 28)), 1) == 20.0
    assert round(yoy_growth(FACTS, EPS, date(2026, 10, 28)), 1) == -20.0


def test_no_numbers_when_the_quarter_was_filed_long_before():
    assert yoy_growth(FACTS, REVENUE, date(2027, 2, 15)) is None
