"""Configuration: env accessors, safety guards, instrument registry and strategy params.

Nothing here ever touches real money: the Alpaca trading URL is pinned to the paper endpoint.
"""
from __future__ import annotations

import os

from pydantic import BaseModel

from models import AssetClass, Timeframe

CLAUDE_MODEL = "claude-opus-4-8"

# Alpaca endpoints — PAPER ONLY. Do not change to a live URL.
ALPACA_PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
ALPACA_DATA_URL = "https://data.alpaca.markets"


def _env_bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).lower() in ("true", "1", "yes")


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


def dry_run() -> bool:
    """Default true (safe for local runs); set false to actually place paper orders."""
    return _env_bool("DRY_RUN", "true")


def decision_log_path() -> str:
    return os.environ.get("DECISION_LOG_PATH", "trade_decisions.jsonl")


def alpaca_api_key() -> str:
    # strip(): pasted secrets often carry a trailing newline or space.
    return os.environ["ALPACA_API_KEY"].strip()


def alpaca_api_secret() -> str:
    return os.environ["ALPACA_API_SECRET"].strip()


def describe_alpaca_key() -> str:
    """Non-secret hint for auth errors: key prefix and lengths only, never the key itself."""
    key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET", "").strip()
    kind = {"PK": "paper key", "AK": "LIVE key — not valid on the paper endpoint"}.get(key[:2], "unrecognised prefix")
    return (f"ALPACA_API_KEY starts with {key[:2]!r} ({kind}), length {len(key)}; "
            f"ALPACA_API_SECRET length {len(secret)}")


def alpaca_trading_url() -> str:
    """Always a paper endpoint; anything else is rejected."""
    url = os.environ.get("ALPACA_TRADING_URL", ALPACA_PAPER_TRADING_URL)
    if not url.startswith("https://paper-api.alpaca.markets"):
        raise ValueError(f"Refusing non-paper Alpaca trading URL: {url!r}. This project is paper-only.")
    return url


def alpaca_data_url() -> str:
    return os.environ.get("ALPACA_DATA_URL", ALPACA_DATA_URL)


def anthropic_api_key() -> str:
    return os.environ["ANTHROPIC_API_KEY"]


# --- Risk parameters ---------------------------------------------------------
def risk_per_trade_pct() -> float:
    """Fraction of equity risked per trade (default 0.5%)."""
    return _env_float("RISK_PER_TRADE_PCT", 0.005)


def max_stop_pct() -> float:
    """Hard stop-loss cap: a stop is never further than this fraction from entry (default 1%)."""
    return _env_float("MAX_STOP_PCT", 0.01)


def atr_stop_mult() -> float:
    """Intended stop distance = this multiple of ATR, before the hard cap."""
    return _env_float("ATR_STOP_MULT", 1.5)


def max_position_pct() -> float:
    """No single position may exceed this fraction of equity in notional (default 25%)."""
    return _env_float("MAX_POSITION_PCT", 0.25)


# --- Strategy parameters -----------------------------------------------------
class StrategyParams(BaseModel):
    # mean reversion
    mr_lookback: int = 20
    mr_entry_z: float = -2.0  # enter long when z-score <= this (oversold)
    mr_exit_z: float = 0.0  # exit once price is back at/above the mean
    # momentum breakout
    mb_channel_lookback: int = 20  # Donchian high to break out of
    mb_vol_lookback: int = 20
    mb_vol_mult: float = 1.5  # breakout volume must exceed avg * this
    mb_exit_lookback: int = 10  # trailing exit on a Donchian-low break
    # trend following
    tf_fast_ma: int = 20
    tf_slow_ma: int = 50
    atr_period: int = 14


STRATEGY_PARAMS = StrategyParams()


# --- Instrument registry -----------------------------------------------------
class Instrument(BaseModel):
    symbol: str  # Alpaca order symbol
    market: str
    asset_class: AssetClass
    strategy: str  # key into strategies registry
    timeframe: Timeframe
    correlation_group: str | None = None  # members of one group can't both be long

    @property
    def position_symbol(self) -> str:
        """Alpaca reports crypto positions without the slash (BTC/USD -> BTCUSD)."""
        return self.symbol.replace("/", "")


INSTRUMENTS: list[Instrument] = [
    Instrument(symbol="SPY", market="S&P 500", asset_class=AssetClass.EQUITY,
               strategy="mean_reversion", timeframe=Timeframe.M15, correlation_group="us_equity_index"),
    Instrument(symbol="QQQ", market="NASDAQ", asset_class=AssetClass.EQUITY,
               strategy="mean_reversion", timeframe=Timeframe.M15, correlation_group="us_equity_index"),
    Instrument(symbol="BTC/USD", market="Bitcoin", asset_class=AssetClass.CRYPTO,
               strategy="momentum_breakout", timeframe=Timeframe.H1),
    Instrument(symbol="GLD", market="Gold", asset_class=AssetClass.EQUITY,
               strategy="trend_following", timeframe=Timeframe.H4),
    Instrument(symbol="USO", market="Oil", asset_class=AssetClass.EQUITY,
               strategy="trend_following", timeframe=Timeframe.H4),
    # German exposure. Alpaca has no Xetra access, so these are the US listings (USD, US hours).
    Instrument(symbol="EWG", market="Germany (iShares MSCI Germany ETF)", asset_class=AssetClass.EQUITY,
               strategy="trend_following", timeframe=Timeframe.H4, correlation_group="germany_index"),
    Instrument(symbol="DAX", market="DAX (Global X DAX Germany ETF)", asset_class=AssetClass.EQUITY,
               strategy="trend_following", timeframe=Timeframe.H4, correlation_group="germany_index"),
    Instrument(symbol="SAP", market="SAP (NYSE ADR)", asset_class=AssetClass.EQUITY,
               strategy="mean_reversion", timeframe=Timeframe.M15),
    Instrument(symbol="DB", market="Deutsche Bank (NYSE)", asset_class=AssetClass.EQUITY,
               strategy="mean_reversion", timeframe=Timeframe.M15),
    Instrument(symbol="BNTX", market="BioNTech (Nasdaq ADR)", asset_class=AssetClass.EQUITY,
               strategy="momentum_breakout", timeframe=Timeframe.H1),
]
