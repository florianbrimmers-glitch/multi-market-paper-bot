"""Deliver a brief as a comment on the repo's "Daily Briefs" issue, so GitHub notifies the owner
(web, e-mail, GitHub mobile app). Uses the workflow's GITHUB_TOKEN; a no-op outside Actions."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

ISSUE_TITLE = "Daily Briefs"
API = "https://api.github.com"


def post_brief(text: str, kind: str, client=None) -> bool:
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
        issue = next((i for i in r.json() if i.get("title") == ISSUE_TITLE and "pull_request" not in i), None)
        if issue is None:
            r = client.post(f"/repos/{repo}/issues", json={
                "title": ISSUE_TITLE,
                "body": "Morning and night briefs from the paper-trading bot are posted here as comments. "
                        "Simulated money only — not financial advice."})
            r.raise_for_status()
            issue = r.json()
        heading = "☀️ Morning brief" if kind == "morning" else "🌙 Night brief"
        r = client.post(f"/repos/{repo}/issues/{issue['number']}/comments",
                        json={"body": f"### {heading}\n\n{text}"})
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 — delivery must never break trading
        logger.error("Could not post brief to issue: %s", e)
        return False
    finally:
        if own:
            client.close()
