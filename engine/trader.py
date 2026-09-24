"""Live paper-trading tick.

1. Safety net: every long position without a resting protective sell order gets a stop.
2. Per instrument: fetch bars -> strategy signal -> (entry) risk sizing + correlation filter ->
   submit paper order, or log only in DRY_RUN. One JSONL record per instrument.

ETF entries carry their stop as a linked OTO order (active only once the entry fills). Crypto
can't use OTO on Alpaca, so its stop (a stop-limit) is placed by the safety net after the fill.
ETFs are only traded while the US session is open; nothing is sent while an order is pending.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

import config
import logbuch
from broker.base import BrokerAdapter
from config import Instrument
from models import Action, AssetClass, Bar, Order, OrderStatus, OrderType, Side, TradeRecord
from risk import size_entry
from strategies import build_strategy

logger = logging.getLogger(__name__)

_GROUP_OF = {i.position_symbol: i.correlation_group for i in config.INSTRUMENTS}
_BY_POS_SYMBOL = {i.position_symbol: i for i in config.INSTRUMENTS}
CRYPTO_STOP_LIMIT_SLIPPAGE = 0.005  # stop-limit floor 0.5% below the trigger


def _fetch(inst: Instrument) -> list[Bar]:
    from data import fetch_bars
    return fetch_bars(inst.symbol, inst.asset_class, inst.timeframe)


def protective_stop_order(inst: Instrument, qty: float, avg_entry: float) -> Order:
    stop = round(avg_entry * (1 - config.max_stop_pct()), 2)
    if inst.asset_class == AssetClass.CRYPTO:
        return Order(symbol=inst.symbol, side=Side.SELL, qty=qty, type=OrderType.STOP_LIMIT,
                     stop_price=stop, limit_price=round(stop * (1 - CRYPTO_STOP_LIMIT_SLIPPAGE), 2))
    return Order(symbol=inst.symbol, side=Side.SELL, qty=qty, type=OrderType.STOP, stop_price=stop)


def ensure_protective_stops(broker: BrokerAdapter, positions, open_orders: list[Order]) -> list[str]:
    """Place a stop for any long position that has no resting sell order. Returns symbols fixed."""
    protected = {o.symbol.replace("/", "") for o in open_orders if o.side == Side.SELL}
    fixed: list[str] = []
    for sym, pos in positions.items():
        inst = _BY_POS_SYMBOL.get(sym)
        if inst is None or not pos.is_long or sym in protected:
            continue
        o = broker.submit_order(protective_stop_order(inst, pos.qty, pos.avg_entry_price))
        if o.status != OrderStatus.REJECTED:
            fixed.append(sym)
            logger.warning("Placed missing protective stop for %s @ %s", sym, o.stop_price)
    return fixed


def run_tick(
    broker: BrokerAdapter,
    fetch: Callable[[Instrument], list[Bar]] = _fetch,
    run_id: str | None = None,
) -> list[TradeRecord]:
    """Evaluate every instrument once. `fetch` is injectable for tests."""
    run_id = run_id or uuid.uuid4().hex[:12]
    dry = config.dry_run()
    account = broker.get_account()
    positions = broker.get_positions()
    open_orders = broker.get_open_orders()
    market_open = broker.is_market_open()
    if not dry:
        ensure_protective_stops(broker, positions, open_orders)
    pending_buys = {o.symbol.replace("/", "") for o in open_orders if o.side == Side.BUY}
    records: list[TradeRecord] = []

    for inst in config.INSTRUMENTS:
        sym = inst.position_symbol
        rec = TradeRecord(run_id=run_id, evaluated_at=datetime.now(timezone.utc).isoformat(),
                          symbol=inst.symbol, strategy=inst.strategy, dry_run=dry)
        try:
            bars = fetch(inst)
            in_position = sym in positions and positions[sym].is_long
            signal = build_strategy(inst.strategy, config.STRATEGY_PARAMS).generate_signal(
                inst.symbol, bars, in_position)
            rec.signal = signal
            crypto = inst.asset_class == AssetClass.CRYPTO
            session_ok = crypto or market_open

            if not signal.is_actionable or (signal.action == Action.EXIT) != in_position:
                rec.action_taken = "hold"
            elif not session_ok:
                rec.action_taken = "skipped:market closed"
            elif sym in pending_buys:
                rec.action_taken = "skipped:order pending"

            elif signal.action == Action.EXIT:
                if dry:
                    rec.action_taken = "skipped:dry_run(exit)"
                else:
                    order = broker.close_position(inst.symbol)
                    rec.order_id = order.id if order else None
                    rec.action_taken = "exit"

            else:  # ENTER_LONG, flat
                decision = size_entry(
                    symbol=sym, entry_price=signal.ref_price, bars=bars, account=account,
                    positions=positions, correlation_group=inst.correlation_group, group_of=_GROUP_OF,
                    allow_fractional=crypto,
                )
                rec.plan = decision.plan
                if not decision.approved:
                    rec.action_taken = f"skipped:{decision.reason}"
                elif dry:
                    rec.action_taken = "skipped:dry_run(entry)"
                else:
                    plan = decision.plan
                    order = broker.submit_order(Order(
                        symbol=inst.symbol, side=Side.BUY, qty=plan.qty, type=OrderType.MARKET,
                        stop_loss=None if crypto else plan.stop_price,
                        client_order_id=f"{run_id}-{sym}"))
                    rec.order_id = order.id
                    rec.action_taken = "rejected" if order.status == OrderStatus.REJECTED else "submitted"
                    if rec.action_taken == "submitted":
                        pending_buys.add(sym)
                        # Count the new long so a correlated instrument later in this tick is filtered.
                        positions = broker.get_positions()
                        if crypto:  # no OTO for crypto: protect as soon as it has filled
                            ensure_protective_stops(broker, {sym: positions[sym]} if sym in positions else {},
                                                    broker.get_open_orders())
        except Exception as e:  # noqa: BLE001 — one instrument failing must not abort the tick
            rec.error = f"{type(e).__name__}: {e}"
            logger.exception("Error evaluating %s", inst.symbol)

        logbuch.append_record(rec)
        records.append(rec)
    return records
