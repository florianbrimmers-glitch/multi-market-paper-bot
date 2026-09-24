"""Live paper-trading tick.

Per instrument: fetch bars -> strategy signal -> (entry) risk sizing + correlation filter ->
submit paper order + protective stop, or log only in DRY_RUN. One JSONL record per instrument.
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


def _fetch(inst: Instrument) -> list[Bar]:
    from data import fetch_bars
    return fetch_bars(inst.symbol, inst.asset_class, inst.timeframe)


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

            if signal.action == Action.EXIT and in_position:
                if dry:
                    rec.action_taken = "skipped:dry_run(exit)"
                else:
                    order = broker.close_position(inst.symbol)
                    rec.order_id = order.id if order else None
                    rec.action_taken = "exit"

            elif signal.action == Action.ENTER_LONG and not in_position:
                decision = size_entry(
                    symbol=sym, entry_price=signal.ref_price, bars=bars, account=account,
                    positions=positions, correlation_group=inst.correlation_group, group_of=_GROUP_OF,
                    allow_fractional=inst.asset_class == AssetClass.CRYPTO,
                )
                if not decision.approved:
                    rec.action_taken = f"skipped:{decision.reason}"
                elif dry:
                    rec.plan = decision.plan
                    rec.action_taken = "skipped:dry_run(entry)"
                else:
                    plan = rec.plan = decision.plan
                    order = broker.submit_order(Order(
                        symbol=inst.symbol, side=Side.BUY, qty=plan.qty, type=OrderType.MARKET,
                        client_order_id=f"{run_id}-{sym}"))
                    rec.order_id = order.id
                    if order.status == OrderStatus.REJECTED:
                        rec.action_taken = "rejected"
                    else:
                        rec.action_taken = "submitted"
                        broker.submit_order(Order(
                            symbol=inst.symbol, side=Side.SELL, qty=plan.qty, type=OrderType.STOP,
                            stop_price=plan.stop_price, client_order_id=f"{run_id}-{sym}-stop"))
                        # Count the new long so a correlated instrument later in this tick is filtered.
                        positions = broker.get_positions()
            else:
                rec.action_taken = "hold"
        except Exception as e:  # noqa: BLE001 — one instrument failing must not abort the tick
            rec.error = f"{type(e).__name__}: {e}"
            logger.exception("Error evaluating %s", inst.symbol)

        logbuch.append_record(rec)
        records.append(rec)
    return records
