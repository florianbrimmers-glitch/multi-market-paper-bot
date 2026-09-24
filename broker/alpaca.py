"""Alpaca PAPER trading adapter (REST via httpx). Hard-guarded to the paper endpoint."""
from __future__ import annotations

import logging

import httpx

import config
from broker.base import BrokerAdapter
from models import Account, Order, OrderStatus, OrderType, Position, Side

logger = logging.getLogger(__name__)


def _pos_symbol(symbol: str) -> str:
    return symbol.replace("/", "")


class AlpacaBroker(BrokerAdapter):
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

    def submit_order(self, order: Order) -> Order:
        crypto = "/" in order.symbol
        payload: dict[str, object] = {
            "symbol": order.symbol, "side": order.side.value, "type": order.type.value,
            # Alpaca: crypto supports gtc/ioc; fractional equity orders must be day.
            "time_in_force": "gtc" if crypto else "day",
            "qty": str(order.qty),
        }
        if order.type == OrderType.STOP and order.stop_price is not None:
            payload["stop_price"] = str(order.stop_price)
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

    def close_position(self, symbol: str) -> Order | None:
        sym = _pos_symbol(symbol)
        pos = self.get_positions().get(sym)
        if not pos:
            return None
        # Cancel resting orders (e.g. the protective stop) so they don't fire after the exit.
        r = self._client.get("/v2/orders", params={"status": "open", "symbols": symbol})
        if r.status_code < 400:
            for o in r.json():
                self._client.delete(f"/v2/orders/{o['id']}")
        r = self._client.delete(f"/v2/positions/{sym}")
        if r.status_code >= 400:
            logger.error("Close position failed (%s): %s", r.status_code, r.text)
            return Order(symbol=symbol, side=Side.SELL, qty=pos.qty, status=OrderStatus.REJECTED)
        return Order(symbol=symbol, side=Side.SELL, qty=abs(pos.qty), id=r.json().get("id"))
