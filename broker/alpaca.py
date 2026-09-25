"""Alpaca PAPER trading adapter (REST via httpx). Hard-guarded to the paper endpoint."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

import httpx

import config
from broker.base import BrokerAdapter
from models import Account, Clock, Order, OrderStatus, OrderType, Position, Side

logger = logging.getLogger(__name__)


def _parse_ts(value: str) -> datetime:
    """Alpaca sends nanosecond fractions ('...:03.123456789-04:00'); Python accepts at most 6 digits."""
    return datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", value).replace("Z", "+00:00"))


def _pos_symbol(symbol: str) -> str:
    return symbol.replace("/", "")


class AlpacaBroker(BrokerAdapter):
    poll_interval = 0.5

    def __init__(self, timeout: float = 20.0) -> None:
        self.base_url = config.alpaca_trading_url()  # raises unless paper
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"APCA-API-KEY-ID": config.alpaca_api_key(),
                     "APCA-API-SECRET-KEY": config.alpaca_api_secret()},
            timeout=timeout,
        )
        logger.info("AlpacaBroker connected to PAPER endpoint %s", self.base_url)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AlpacaBroker":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_account(self) -> Account:
        r = self._client.get("/v2/account")
        if r.status_code in (401, 403):
            raise PermissionError(
                f"Alpaca paper API rejected the credentials (HTTP {r.status_code}). "
                f"{config.describe_alpaca_key()}. Use keys generated under the PAPER account; "
                "key ID and secret must not be swapped.")
        r.raise_for_status()
        d = r.json()
        return Account(equity=float(d["equity"]), cash=float(d["cash"]),
                       buying_power=float(d["buying_power"]),
                       last_equity=float(d.get("last_equity") or 0))

    def get_positions(self) -> dict[str, Position]:
        r = self._client.get("/v2/positions")
        r.raise_for_status()
        out: dict[str, Position] = {}
        for d in r.json():
            qty = float(d["qty"])
            sym = _pos_symbol(d["symbol"])
            out[sym] = Position(symbol=sym, qty=qty, avg_entry_price=float(d["avg_entry_price"]),
                                side=Side.BUY if qty >= 0 else Side.SELL,
                                market_value=float(d.get("market_value") or 0),
                                unrealized_pl=float(d.get("unrealized_pl") or 0))
        return out

    def get_open_orders(self) -> list[Order]:
        r = self._client.get("/v2/orders", params={"status": "open", "nested": "true", "limit": 500})
        r.raise_for_status()
        out: list[Order] = []

        def add(d: dict) -> None:
            try:
                otype = OrderType(d.get("type") or d.get("order_type"))
            except ValueError:
                otype = OrderType.MARKET
            out.append(Order(
                symbol=_pos_symbol(d["symbol"]), side=Side(d["side"]), qty=float(d.get("qty") or 0),
                type=otype, stop_price=float(d["stop_price"]) if d.get("stop_price") else None,
                id=d.get("id"), client_order_id=d.get("client_order_id")))

        for d in r.json():
            add(d)
            for leg in d.get("legs") or []:  # OTO stop legs
                add(leg)
        return out

    def clock(self) -> Clock:
        r = self._client.get("/v2/clock")
        r.raise_for_status()
        d = r.json()
        return Clock(timestamp=_parse_ts(d["timestamp"]), is_open=bool(d["is_open"]),
                     next_open=_parse_ts(d["next_open"]), next_close=_parse_ts(d["next_close"]))

    def submit_order(self, order: Order) -> Order:
        crypto = "/" in order.symbol
        payload: dict[str, object] = {
            "symbol": order.symbol, "side": order.side.value, "type": order.type.value,
            # gtc so a protective stop never silently expires at the end of the day.
            "time_in_force": "gtc",
            "qty": str(order.qty),
        }
        if order.stop_price is not None:
            payload["stop_price"] = str(order.stop_price)
        if order.limit_price is not None:
            payload["limit_price"] = str(order.limit_price)
        if order.stop_loss is not None and not crypto:
            # OTO: the stop only becomes active once the entry fills — no stop-before-fill, no accidental short.
            payload["order_class"] = "oto"
            payload["stop_loss"] = {"stop_price": str(order.stop_loss)}
        if order.client_order_id:
            payload["client_order_id"] = order.client_order_id

        r = self._client.post("/v2/orders", json=payload)
        if r.status_code >= 400:
            order.status = OrderStatus.REJECTED
            logger.error("Order rejected (%s): %s", r.status_code, r.text)
            return order
        d = r.json()
        order.id = d.get("id")
        try:
            order.status = OrderStatus(d.get("status", "new"))
        except ValueError:
            order.status = OrderStatus.NEW
        fp = d.get("filled_avg_price")
        order.filled_price = float(fp) if fp else None
        return order

    _DONE = {"canceled", "filled", "expired", "rejected", "replaced"}

    def _wait_until_done(self, order_ids: list[str], timeout: float) -> None:
        """Cancels are asynchronous: until they complete, the shares stay 'held_for_orders' and
        a close is rejected with 403 'insufficient qty available'."""
        deadline = time.monotonic() + timeout
        pending = list(order_ids)
        while pending and time.monotonic() < deadline:
            r = self._client.get(f"/v2/orders/{pending[0]}")
            if r.status_code >= 400 or r.json().get("status") in self._DONE:
                pending.pop(0)
            else:
                time.sleep(self.poll_interval)

    def close_position(self, symbol: str, retries: int = 4) -> Order | None:
        sym = _pos_symbol(symbol)
        pos = self.get_positions().get(sym)
        if not pos:
            return None
        # Cancel resting orders (e.g. the protective stop) so they can't fire after the exit —
        # and wait for the cancels to settle, otherwise the close is rejected.
        ids = [o.id for o in self.get_open_orders() if o.symbol == sym and o.id]
        for oid in ids:
            self._client.delete(f"/v2/orders/{oid}")
        self._wait_until_done(ids, timeout=10.0)
        for attempt in range(retries):
            r = self._client.delete(f"/v2/positions/{sym}")
            if r.status_code < 400:
                return Order(symbol=symbol, side=Side.SELL, qty=abs(pos.qty), id=r.json().get("id"))
            logger.error("Close position failed (%s, attempt %d/%d): %s", r.status_code, attempt + 1,
                         retries, r.text)
            if r.status_code != 403:
                break
            time.sleep(self.poll_interval * 2)
        return Order(symbol=symbol, side=Side.SELL, qty=pos.qty, status=OrderStatus.REJECTED)
