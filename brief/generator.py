"""Daily briefs — morning (what's happening + plan) and night (how the portfolio did).

gather_brief_data() collects the numbers; render_brief() writes prose with Claude and falls
back to a plain template if the SDK/key is unavailable, so the pipeline never hard-fails.
"""
from __future__ import annotations

import logging

import config
import i18n
from broker.base import BrokerAdapter
from models import Bar, BriefData

logger = logging.getLogger(__name__)

_SYSTEM = (
    "Du bist ein knapper Trading-Assistent für einen PAPER-Trading-Bot (simuliertes Geld) auf "
    "mehreren Märkten. Schreib ein kurzes, sachliches Briefing auf Deutsch in einfacher Sprache. "
    "Nutze deutsche Zahlenformate (z. B. 1.234,56 $ und −0,70 %). Gib niemals Anlageberatung, "
    "versprich keine Renditen und mach klar, dass es sich um simuliertes Papiergeld handelt. "
    "Verwende ausschließlich die gelieferten Zahlen."
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
    title = "MORGEN-BRIEFING" if data.kind == "morning" else "ABEND-BRIEFING"
    lines = [
        f"[{title} — PAPER-TRADING] {data.generated_at}",
        f"Kontowert: {i18n.usd(data.equity)} (davon Cash {i18n.usd(data.cash)})",
        f"Tagesergebnis: {i18n.usd(data.day_pl)} ({i18n.pct(data.day_pl_pct)})",
    ]
    if data.market_moves:
        lines.append("Marktbewegung: " + ", ".join(f"{s} {i18n.pct(p)}" for s, p in data.market_moves.items()))
    lines.append("Offene Positionen: " + (", ".join(
        f"{p.symbol} {i18n.qty(p.qty)} Stück zu {i18n.usd(p.avg_entry_price)} "
        f"(unrealisiert {i18n.usd(p.unrealized_pl)})"
        for p in data.positions) or "keine"))
    if data.recent_actions:
        lines.append("Aktionen heute: " + "; ".join(data.recent_actions))
    lines.append("Hinweis: simuliertes Papiergeld, keine Anlageberatung.")
    return "\n".join(lines)


def render_brief(data: BriefData) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=config.anthropic_api_key())
        focus = ("Behandle die Marktbewegungen über Nacht bzw. zur Eröffnung, worauf heute zu achten ist, "
                 "die offenen Positionen und den Kontowert."
                 if data.kind == "morning" else
                 "Behandle, wie das Portfolio heute abgeschnitten hat (Tagesergebnis), auffällige "
                 "Positionsbewegungen und die ausgeführten Aktionen.")
        msg = client.messages.create(
            model=config.CLAUDE_MODEL, max_tokens=600, system=_SYSTEM,
            messages=[{"role": "user", "content":
                       f"Schreib das {'Morgen' if data.kind == 'morning' else 'Abend'}-Briefing in "
                       f"wenigen kurzen Sätzen auf Deutsch. {focus}\n\n"
                       f"Fakten (JSON):\n{data.model_dump_json(indent=2)}"}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        return text or fallback_text(data)
    except Exception as e:  # noqa: BLE001 — ImportError, missing key, API errors
        logger.warning("Claude brief unavailable (%s); using template", e)
        return fallback_text(data)
