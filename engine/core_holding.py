"""ETF core holding: the buy-and-hold yardstick every strategy has to beat.

Owner's decision of 24.11.2026: the paper account is a rehearsal for real money, and over long
periods no tested trading rule beat simply holding broad ETFs after costs. So the account holds
one broad ETF (MSCI World, US listing URTH as the stand-in for the iShares Core MSCI World UCITS
ETF) at CORE_TARGET_PCT of equity, without a stop, and never sells it for rebalancing.

Once per trading day, from 10:00 New York, idle cash is put into the ETF up to the target. Only
cash is used, never margin, so cash freed by other strategies (for example the last
earnings-drift sales) is invested the next day.

Note for the comparison: Alpaca's paper account books no dividends, so the paper result of the
core lags the real ETF by its dividend yield (about 1.5 % a year for MSCI World).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config
import i18n
import logbuch
from models import AssetClass, Order, OrderStatus, OrderType, Side, TradeRecord

log = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
STRATEGY = "core_holding"
MIN_MINUTES_AFTER_OPEN = 30
MIN_MINUTES_BEFORE_CLOSE = 20
MIN_ORDER_PCT = 0.02     # top up only when the gap is at least 2 % of the target
CASH_BUFFER = 0.99       # leave 1 % of the cash: a market buy is priced with a small buffer


def is_due(clock, state) -> bool:
    if not config.core_enabled() or not clock.is_open:
        return False
    now = clock.timestamp.astimezone(NY)
    opened = (now - now.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() / 60
    to_close = (clock.next_close - clock.timestamp).total_seconds() / 60
    return (opened >= MIN_MINUTES_AFTER_OPEN and to_close >= MIN_MINUTES_BEFORE_CLOSE
            and state.core_day != now.date().isoformat())


def _price(symbol: str) -> float | None:
    try:
        from data import latest_price
        return latest_price(symbol, AssetClass.EQUITY)
    except Exception as e:  # noqa: BLE001
        log.warning("No live price for %s (%s)", symbol, e)
        return None


def run(broker, state, clock, price_of=None) -> list[str]:
    """Top the core ETF up to its target with idle cash. Returns German trade-log lines."""
    state.core_day = clock.timestamp.astimezone(NY).date().isoformat()
    sym, dry = config.CORE_SYMBOL, config.dry_run()
    account = broker.get_account()
    held = broker.get_positions().get(sym)
    held_value = held.market_value if held else 0.0
    target = account.equity * config.CORE_TARGET_PCT
    price = (price_of or _price)(sym)
    if not price or price <= 0:
        return [f"- ⚠️ **{sym}** ETF-Grundlage: kein aktueller Kurs, Kauf auf morgen verschoben"]
    budget = min(target - held_value, max(0.0, account.cash) * CASH_BUFFER)
    if target - held_value < target * MIN_ORDER_PCT or budget < price:
        log.info("core %s: held %.0f of target %.0f, cash %.0f — nothing to buy", sym, held_value, target, account.cash)
        return []
    qty = math.floor(budget / price)
    share = (held_value + qty * price) / account.equity * 100
    if dry:
        _record(sym, "skipped:dry_run(entry)", dry)
        return [f"- 🟢 **{sym}** würde gekauft: {i18n.qty(qty)} Stück zu ca. {i18n.usd(price)} "
                f"(ETF-Grundlage MSCI World) — Testlauf"]
    order = broker.submit_order(Order(symbol=sym, side=Side.BUY, qty=qty, type=OrderType.MARKET,
                                      client_order_id=f"core-{state.core_day}-{sym}"))
    if order.status == OrderStatus.REJECTED:
        _record(sym, "rejected", dry)
        return [f"- ⚠️ **{sym}** Kauforder für die ETF-Grundlage vom Broker abgelehnt"]
    _record(sym, "submitted", dry)
    return [f"- 🟢 **{sym}** gekauft: {i18n.qty(qty)} Stück zu ca. {i18n.usd(price)} (ETF-Grundlage MSCI World, "
            f"ohne Stop, langfristig gehalten; danach etwa {share:.0f} % des Kontos)"]


def _record(symbol: str, action: str, dry: bool) -> None:
    logbuch.append_record(TradeRecord(run_id="core", evaluated_at=datetime.now(timezone.utc).isoformat(),
                                      symbol=symbol, strategy=STRATEGY, action_taken=action, dry_run=dry))
