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
| Germany | `EWG` (iShares MSCI Germany ETF) | Trend following | 4 hour |
| DAX | `DAX` (Global X DAX Germany ETF) | Trend following | 4 hour |
| SAP | `SAP` (NYSE) | Mean reversion | 15 min |
| Deutsche Bank | `DB` (NYSE) | Mean reversion | 15 min |
| BioNTech | `BNTX` (Nasdaq) | Momentum breakout | 1 hour |

**German assets:** Alpaca has no access to Xetra or Frankfurt. The German assets are therefore
their **US listings**. They trade in USD during US market hours, but track the same companies
and indices.

**Risk controls applied to every trade:**
- **Protective stop.** It is never placed more than 1% below the entry price
  (`MAX_STOP_PCT`), and it stays active across days (`gtc`). ETF entries carry the stop as a
  linked (OTO) order, which becomes active only once the buy fills. Bitcoin can't use OTO on
  Alpaca, so its stop is placed as a stop-limit order right after the fill. On every tick, a
  safety check also adds a stop to any open position that doesn't have one.
- **Volatility-based sizing.** Each trade risks 0.5% of equity (`RISK_PER_TRADE_PCT`). The stop
  distance comes from ATR, and position size is also capped at 25% of equity
  (`MAX_POSITION_PCT`) and by available buying power.
- **Correlation filter.** SPY and QQQ can't both be long at the same time, and neither can
  EWG and DAX.

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

## Scheduling

In the repo settings, add these **secrets**: `ALPACA_API_KEY`, `ALPACA_API_SECRET` and
`ANTHROPIC_API_KEY`. Orders are placed only once the repository **variable** `DRY_RUN` is `false`.

GitHub's own schedule is unreliable on quiet repos (runs were hours late or skipped). So the
workflows are started by an **external timer**, [cron-job.org](https://cron-job.org) (free),
through the GitHub API. Each run takes about 1 minute, which comes to roughly 1,400 minutes a
month, within the 2,000 free minutes of a private repo. `trade-loop.yml` keeps a sparse hourly
fallback during the US session in case the timer fails.

### 1. Create a GitHub token
github.com → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** →
Generate new token:
- Repository access: **Only select repositories** → `multi-market-paper-bot`
- Permissions → Repository permissions → **Actions: Read and write**
- Expiration: up to 1 year (put a reminder in your calendar to renew it)

### 2. Set up cron-job.org jobs
Each job uses the same request (Advanced tab):
- **URL:** `https://api.github.com/repos/florianbrimmers-glitch/multi-market-paper-bot/actions/workflows/<WORKFLOW>/dispatches`
- **Method:** `POST`
- **Headers:** `Authorization: Bearer <TOKEN>`, `Accept: application/vnd.github+json`,
  `Content-Type: application/json`
- **Time zone:** `America/New_York`. This way the times follow the US exchange, including
  daylight saving time.

| Job | `<WORKFLOW>` | Body | Schedule (New York time) |
|---|---|---|---|
| Trading, US session | `trade-loop.yml` | `{"ref":"main"}` | Mon–Fri, hours 9–16, minutes 0/15/30/45 |
| Trading, Bitcoin at night | `trade-loop.yml` | `{"ref":"main"}` | daily, hours 0–8 and 17–23, minute 0 |
| Morning brief | `brief.yml` | `{"ref":"main","inputs":{"kind":"morning"}}` | Mon–Fri 09:00 |
| Night brief | `brief.yml` | `{"ref":"main","inputs":{"kind":"night"}}` | Mon–Fri 16:30 |

A successful call returns **HTTP 204**. The run then appears under Actions as `workflow_dispatch`.

### Receiving the briefs as a Claude task
You can also set up a scheduled Claude task (Routine) on the same schedule. It would run
`python run_brief.py morning` or `night` in this repo and post the result into a Claude session.
Or extend `run_brief.py` to send the text by email or Slack.
