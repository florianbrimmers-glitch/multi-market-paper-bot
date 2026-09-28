"""Move the protective stop of one open PAPER position to a fixed distance below its entry.

  python run_adjust_stop.py SPY 0.005   # stop 0.5% below the average entry price

Run manually via the "Adjust Stop" workflow; the loop's safety net keeps working either way.
"""
from __future__ import annotations

import sys

import i18n
from broker import AlpacaBroker
from models import OrderType, Side


def main(symbol: str, pct: float) -> None:
    if not 0 < pct <= 0.05:
        sys.exit(f"Abstand {pct} unplausibel (erlaubt: über 0 bis 5 %)")
    sym = symbol.replace("/", "").upper()
    with AlpacaBroker() as broker:
        pos = broker.get_positions().get(sym)
        if pos is None or not pos.is_long:
            sys.exit(f"Keine offene Long-Position in {sym}")
        stops = [o for o in broker.get_open_orders()
                 if o.symbol == sym and o.side == Side.SELL and o.type in (OrderType.STOP, OrderType.STOP_LIMIT)]
        if len(stops) != 1:
            sys.exit(f"Erwartet genau einen Stop für {sym}, gefunden: {len(stops)}")
        old = stops[0]
        if old.type == OrderType.STOP_LIMIT:
            sys.exit("Stop-Limit (Krypto) wird hier nicht verschoben")
        new_stop = round(pos.avg_entry_price * (1 - pct), 2)
        new_id = broker.replace_stop(old.id, new_stop)
        print(f"{sym}: Stop von {i18n.usd(old.stop_price)} auf {i18n.usd(new_stop)} verschoben "
              f"({i18n.pct(-pct * 100)} unter Einstand {i18n.usd(pos.avg_entry_price)}), Order {new_id}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], float(sys.argv[2]))
