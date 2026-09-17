"""Fake tools the copilot can call. No network, no cost -- just plausible output.

`check_outage_status` pretends to hit a status page. It answers randomly so the
"is this actually an outage?" branch is exercised on every run, and so the
tool call shows up in MetricAI next to the LLM calls it sits between.
"""
from __future__ import annotations

import random
import time
from typing import Any


def check_outage_status(category: str, seed: str | None = None) -> dict[str, Any]:
    """Return a fake status-page reading for the ticket's category."""
    rng = random.Random(seed)          # seed with the ticket id for repeatable runs
    time.sleep(rng.uniform(0.02, 0.08))  # look like a real HTTP round-trip
    outage = rng.random() < 0.5
    return {
        "tool": "check_outage_status",
        "outage": outage,
        "status": "degraded" if outage else "operational",
        "affected": category if outage else None,
        "eta_minutes": rng.choice([15, 30, 60]) if outage else 0,
        "source": "status.example.com",
    }
