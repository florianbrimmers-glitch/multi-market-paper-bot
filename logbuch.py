"""Append-only JSONL decision log — one line per instrument evaluated, in every mode."""
from __future__ import annotations

import logging

import config
from models import TradeRecord

logger = logging.getLogger(__name__)


def append_record(record: TradeRecord) -> None:
    try:
        with open(config.decision_log_path(), "a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")
    except OSError as e:
        logger.error("Trade decision log not writable: %s", e)
