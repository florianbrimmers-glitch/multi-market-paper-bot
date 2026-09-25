"""Append-only JSONL decision log — one line per instrument evaluated, in every mode."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import config
from models import TradeRecord

logger = logging.getLogger(__name__)


def prune(keep_days: int = 14) -> int:
    """Drop records older than `keep_days` so the cached log doesn't grow forever. Returns lines kept."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
    path = config.decision_log_path()
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return 0
    kept = [ln for ln in lines if ln.split('"evaluated_at":"', 1)[-1][:32] >= cutoff]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(kept)
    return len(kept)


def append_record(record: TradeRecord) -> None:
    try:
        with open(config.decision_log_path(), "a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")
    except OSError as e:
        logger.error("Trade decision log not writable: %s", e)
