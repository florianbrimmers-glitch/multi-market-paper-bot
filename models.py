"""Core domain models — pydantic v2, shared across strategies, broker and engine."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AssetClass(str, Enum):
    EQUITY = "equity"  # ETFs traded during US market hours (SPY, QQQ, GLD, USO)
    CRYPTO = "crypto"  # 24/7 (BTC/USD)


class Timeframe(str, Enum):
    M15 = "15Min"
    H1 = "1Hour"
    H4 = "4Hour"


class Bar(BaseModel):
    """A single OHLCV candle."""

    model_config = ConfigDict(extra="ignore")

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class Action(str, Enum):
    ENTER_LONG = "enter_long"
    EXIT = "exit"
    HOLD = "hold"


class Signal(BaseModel):
    """A strategy's view on one instrument at one point in time."""

    symbol: str
    action: Action = Action.HOLD
    reason: str = ""
    ref_price: float = 0.0  # last close the signal was computed on
    strategy: str = ""

    @property
    def is_actionable(self) -> bool:
        return self.action in (Action.ENTER_LONG, Action.EXIT)


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"  # Alpaca crypto supports stop_limit but not plain stop


class OrderStatus(str, Enum):
    NEW = "new"
    ACCEPTED = "accepted"
    PENDING_NEW = "pending_new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


class Order(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbol: str
    side: Side
    qty: float
    type: OrderType = OrderType.MARKET
    stop_price: float | None = None
    limit_price: float | None = None
    # Entry only: protective stop attached as a linked (OTO) order that activates on fill.
    stop_loss: float | None = None
    client_order_id: str | None = None
    status: OrderStatus = OrderStatus.NEW
    filled_price: float | None = None
    id: str | None = None


class Position(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbol: str
    qty: float
    avg_entry_price: float
    side: Side = Side.BUY
    market_value: float = 0.0
    unrealized_pl: float = 0.0

    @property
    def is_long(self) -> bool:
        return self.qty > 0


class Account(BaseModel):
    model_config = ConfigDict(extra="ignore")

    equity: float
    cash: float
    buying_power: float
    last_equity: float = 0.0  # equity at previous close — used for day P&L

    @property
    def day_pl(self) -> float:
        return self.equity - self.last_equity if self.last_equity else 0.0

    @property
    def day_pl_pct(self) -> float:
        if not self.last_equity:
            return 0.0
        return (self.equity - self.last_equity) / self.last_equity * 100.0


class TradePlan(BaseModel):
    """A risk-approved order the engine intends to place."""

    symbol: str
    side: Side
    qty: float
    entry_price: float
    stop_price: float
    risk_amount: float  # equity at risk if the stop is hit
    reason: str = ""


class TradeRecord(BaseModel):
    """One JSONL line per instrument evaluated on a tick."""

    run_id: str
    evaluated_at: str
    symbol: str
    strategy: str
    signal: Signal | None = None
    plan: TradePlan | None = None
    action_taken: str = "none"  # "submitted", "exit", "hold", "skipped:<reason>"
    dry_run: bool = True
    order_id: str | None = None
    error: str | None = None


class BriefData(BaseModel):
    """Everything the brief generator needs to write a morning/night message."""

    kind: str  # "morning" | "night"
    generated_at: str
    equity: float
    cash: float
    day_pl: float
    day_pl_pct: float
    positions: list[Position] = Field(default_factory=list)
    market_moves: dict[str, float] = Field(default_factory=dict)  # symbol -> % change
    recent_actions: list[str] = Field(default_factory=list)
