# multi-market-paper-bot

A **paper-trading** bot for five markets, each with its own rules-based strategy, a shared risk
engine, a backtester, and daily morning/night briefs written by Claude. It runs on a free
[Alpaca paper](https://alpaca.markets/) account, so all money is simulated.

> [!WARNING]
> **Paper trading only: simulated money, no financial risk.** This is a learning and research
> tool. It is **not** investment advice and **not** a way to make money. Most automated retail
> trading bots lose money. No code path here reaches a live brokerage account, because the Alpaca
> trading URL is pinned to the paper endpoint and anything else is rejected. Simulated or
> backtested results say nothing about future returns.

## Markets and strategies

| Market | Symbol | Strategy | Timeframe |
|--------|--------|----------|-----------|
| S&P 500 | `SPY` | Mean reversion: buy when the z-score is oversold, exit when price reverts to the mean | 15 min |
| NASDAQ | `QQQ` | Mean reversion | 15 min |
| Bitcoin | `BTC/USD` | Momentum breakout: buy on a Donchian-high break with volume confirmation | 1 hour |
| Gold | `GLD` | Trend following: fast/slow moving-average crossover | 4 hour |
| Oil | `USO` | Trend following | 4 hour |

**Risk controls applied to every trade:**
- **Protective stop.** It is never placed more than 1% below the entry price
  (`MAX_STOP_PCT`), and it stays active across days (`gtc`). ETF entries carry the stop as a
  linked (OTO) order, which becomes active only once the buy fills. Bitcoin can't use OTO on
  Alpaca, so its stop is placed as a stop-limit order right after the fill. On every tick, a
  safety check also adds a stop to any open position that doesn't have one.
- **Volatility-based sizing.** Each trade risks 0.5% of equity (`RISK_PER_TRADE_PCT`). The stop
  distance comes from ATR, and position size is also capped at 25% of equity
  (`MAX_POSITION_PCT`) and by available buying power.
- **Correlation filter.** SPY and QQQ can't both be long at the same time.

**Limitations:**
- The ETFs (SPY, QQQ, GLD, USO) trade only during US market hours. Outside those hours the bot
  records `skipped:market closed` for them. Only BTC trades 24/7.
- While a buy order is still pending, the bot sends no second one (`skipped:order pending`).
- GLD and USO are ETF proxies, not the commodities themselves.
- The strategies are simple illustrations and have not been tuned or optimised.

## Layout

```
config.py        env settings, paper-only guard, instrument registry, strategy parameters
models.py        pydantic models (Bar, Signal, Order, Position, Account, TradePlan, ...)
indicators.py    stdlib-only SMA, z-score, Donchian channel, ATR
strategies/      Strategy base class plus the three strategies
risk/            position sizing, stop cap, correlation filter
data/            Alpaca market-data client
broker/          broker interface, Alpaca paper adapter, in-memory MockBroker
engine/          trader.py (one live tick) and backtest.py (replays history)
brief/           Claude morning/night briefs, with a plain-text fallback
run_trade.py / run_backtest.py / run_brief.py   command-line entry points
```

The live engine and the backtester use the same strategy and risk code.

## Setup

1. Create a free Alpaca **paper** account and generate paper API keys.
2. `cp .env.example .env`, then fill in the keys. Export them into your shell, for example with
   `set -a; . ./.env; set +a`.
3. `pip install -r requirements-dev.txt`

## Usage

```bash
python -m pytest tests/ -v          # offline, no keys needed
DRY_RUN=true  python run_trade.py   # one tick that only logs intended orders (the default)
DRY_RUN=false python run_trade.py   # one tick that places PAPER orders
python run_backtest.py [SPY QQQ ...]
python run_brief.py morning|night
```

Every tick appends one JSONL line per instrument to `trade_decisions.jsonl`, including in
`DRY_RUN`.

## Scheduling (GitHub Actions)

In the repo settings, add these **secrets**: `ALPACA_API_KEY`, `ALPACA_API_SECRET` and
`ANTHROPIC_API_KEY`. Then:
- **`trade-loop.yml`** runs a tick every 15 minutes. It only logs until you set the repository
  **variable** `DRY_RUN=false`.
- **`brief.yml`** runs the morning brief at 13:00 UTC and the night brief at 21:30 UTC on
  weekdays. The text appears in the run summary.

### Receiving the briefs as a Claude task
You can also set up a scheduled Claude task (Routine) on the same schedule. It would run
`python run_brief.py morning` or `night` in this repo and post the result into a Claude session.
Or extend `run_brief.py` to send the text by email or Slack.
