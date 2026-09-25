"""German output formatting for everything the bot posts (briefs, trade log)."""
from __future__ import annotations


def num(x: float, digits: int = 2) -> str:
    """1234.5 -> '1.234,50' (German thousands/decimal separators)."""
    s = f"{x:,.{digits}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def usd(x: float) -> str:
    return f"{num(x)} $"


def pct(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}{num(abs(x))} %"


def qty(x: float) -> str:
    return num(x, 0) if float(x).is_integer() else num(x, 4).rstrip("0").rstrip(",")
