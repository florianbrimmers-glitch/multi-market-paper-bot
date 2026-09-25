"""Post briefs and trade events as comments on repo issues ("Daily Briefs", "Trade Log"), so
GitHub notifies the owner (web, e-mail, GitHub mobile app) — and they stay readable while a long
loop job is still running (Actions logs only appear once a job ends). Uses the workflow's
GITHUB_TOKEN; a no-op outside Actions."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

ISSUE_TITLE = "Daily Briefs"
TRADE_LOG_TITLE = "Trade Log"
API = "https://api.github.com"
_ISSUE_BODIES = {
    ISSUE_TITLE: "Morning and night briefs from the paper-trading bot are posted here as comments. "
                 "Simulated money only — not financial advice.",
    TRADE_LOG_TITLE: "Every paper order, rejection, exit and error from the trading loop is posted here. "
                     "Simulated money only — not financial advice.",
}


def post_brief(text: str, kind: str, client=None) -> bool:
    heading = "☀️ Morning brief" if kind == "morning" else "🌙 Night brief"
    return post_comment(ISSUE_TITLE, f"### {heading}\n\n{text}", client)


def post_comment(title: str, body: str, client=None) -> bool:
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return False
    import httpx

    own = client is None
    client = client or httpx.Client(base_url=API, timeout=20.0, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    try:
        r = client.get(f"/repos/{repo}/issues", params={"state": "open", "per_page": 100})
        r.raise_for_status()
        issue = next((i for i in r.json() if i.get("title") == title and "pull_request" not in i), None)
        if issue is None:
            r = client.post(f"/repos/{repo}/issues", json={"title": title, "body": _ISSUE_BODIES.get(title, "")})
            r.raise_for_status()
            issue = r.json()
        r = client.post(f"/repos/{repo}/issues/{issue['number']}/comments", json={"body": body})
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 — delivery must never break trading
        logger.error("Could not post to issue %r: %s", title, e)
        return False
    finally:
        if own:
            client.close()
