"""When is a brief due? Driven by the exchange clock, so US daylight-saving shifts are automatic.

- morning: once per trading day, from 45 min before the open (or at the first tick while open)
- night:   once per trading day, at the first tick after the close — only on days that had a
           morning brief, so weekends and holidays stay quiet
State is a tiny JSON file carried between loop jobs in the Actions cache.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

from pydantic import BaseModel

from models import Clock

logger = logging.getLogger(__name__)

MORNING_LEAD = timedelta(minutes=45)


class BriefState(BaseModel):
    """Loop state carried between jobs: brief dates plus the positions seen at the last tick."""

    morning: str = ""  # New York date (YYYY-MM-DD) of the last morning brief
    night: str = ""
    positions: dict[str, list[float]] = {}  # symbol -> [qty, avg entry] after the last tick
    own_exits: list[str] = []  # symbols the bot closed itself in the last tick


def state_path() -> str:
    return os.environ.get("BRIEF_STATE_PATH", "brief_state.json")


def load_state() -> BriefState:
    try:
        with open(state_path(), encoding="utf-8") as f:
            return BriefState.model_validate(json.load(f))
    except (OSError, ValueError):
        return BriefState()


def save_state(state: BriefState) -> None:
    try:
        with open(state_path(), "w", encoding="utf-8") as f:
            f.write(state.model_dump_json())
    except OSError as e:
        logger.error("Brief state not writable: %s", e)


def brief_due(clock: Clock, state: BriefState) -> str | None:
    today = clock.timestamp.date().isoformat()  # clock timestamps are New York local time
    opens_soon = (clock.next_open.date().isoformat() == today
                  and clock.next_open - clock.timestamp <= MORNING_LEAD)
    if state.morning != today and (clock.is_open or opens_soon):
        return "morning"
    session_over = not clock.is_open and clock.next_open.date().isoformat() > today
    if state.morning == today and state.night != today and session_over:
        return "night"
    return None
