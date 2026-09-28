"""Put the repo root on sys.path so `import config` etc. work from any pytest invocation."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _midday_clock(monkeypatch):
    """Pin the engine clock to mid-session so the opening-phase rule doesn't depend on when tests run."""
    from datetime import datetime, timezone

    import engine.trader
    monkeypatch.setattr(engine.trader, "_now", lambda: datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc))
