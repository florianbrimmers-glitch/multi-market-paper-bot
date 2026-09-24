"""Daily briefs — morning (what's happening + plan) and night (how the portfolio did).

gather_brief_data() collects the numbers; render_brief() writes prose with Claude and falls
back to a plain template if the SDK/key is unavailable, so the pipeline never hard-fails.
"""
from __future__ import annotations

import logging

import config
from broker.base import BrokerAdapter
from models import Bar, BriefData

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a concise trading-desk assistant for a PAPER (simulated-money) multi-market bot. "
    "Write a short, factual brief in plain language. Never give financial advice, never promise "
    "returns, and make clear this is simulated paper trading. Use only the numbers provided."
)


def _pct_change(bars: list[Bar]) -> float:
    if len(bars) < 2 or not bars[0].close:
        return 0.0
    return (bars[-1].close - bars[0].close) / bars[0].close * 100.0


def gather_brief_data(kind: str, broker: BrokerAdapter, generated_at: str,
                      market_bars: dict[str, list[Bar]] | None = None,
                      recent_actions: list[str] | None = None) -> BriefData:
    account = broker.get_account()
    return BriefData(
        kind=kind, generated_at=generated_at, equity=account.equity, cash=account.cash,
        day_pl=account.day_pl, day_pl_pct=account.day_pl_pct,
        positions=list(broker.get_positions().values()),
        market_moves={s: _pct_change(b) for s, b in (market_bars or {}).items()},
        recent_actions=recent_actions or [],
    )


def fallback_text(data: BriefData) -> str:
    lines = [
        f"[{data.kind.upper()} BRIEF — PAPER TRADING] {data.generated_at}",
        f"Equity: ${data.equity:,.2f} (cash ${data.cash:,.2f})",
        f"Day P&L: ${data.day_pl:,.2f} ({data.day_pl_pct:+.2f}%)",
    ]
    if data.market_moves:
        lines.append("Market moves: " + ", ".join(f"{s} {p:+.2f}%" for s, p in data.market_moves.items()))
    lines.append("Open positions: " + (", ".join(
        f"{p.symbol} {p.qty:g}@{p.avg_entry_price:.2f} (uPL ${p.unrealized_pl:,.2f})"
        for p in data.positions) or "none"))
    if data.recent_actions:
        lines.append("Recent actions: " + "; ".join(data.recent_actions))
    lines.append("Reminder: simulated money, not financial advice.")
    return "\n".join(lines)


def render_brief(data: BriefData) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=config.anthropic_api_key())
        focus = ("Cover overnight/opening market moves, what to watch today, current positions and equity."
                 if data.kind == "morning" else
                 "Cover how the portfolio performed today (day P&L), notable position moves and actions taken.")
        msg = client.messages.create(
            model=config.CLAUDE_MODEL, max_tokens=600, system=_SYSTEM,
            messages=[{"role": "user", "content":
                       f"Write the {data.kind} brief in a few short sentences. {focus}\n\n"
                       f"Facts (JSON):\n{data.model_dump_json(indent=2)}"}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        return text or fallback_text(data)
    except Exception as e:  # noqa: BLE001 — ImportError, missing key, API errors
        logger.warning("Claude brief unavailable (%s); using template", e)
        return fallback_text(data)
