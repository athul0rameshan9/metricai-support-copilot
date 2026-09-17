"""A local shadow ledger.

MetricAI is the system of record; this is a deliberate second opinion. Keeping
our own per-call record lets us answer two questions the dashboard alone can't:
  1. Does MetricAI's cost agree with ours? (pricing tables and FX drift)
  2. What would this have cost under a different model/plan? (counterfactuals)
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator, Optional

LEDGER_PATH = os.getenv("LEDGER_PATH", "out/ledger.jsonl")


@dataclass
class CallRecord:
    run_id: str
    scenario: str            # baseline | model-swap | runaway
    ticket_id: str
    tenant_id: str
    plan: str
    session_id: str
    node_id: str               # workflow step: triage | draft | critique | summarise
    attempt: int               # 1 = first try, 2+ = retry (this is the waste signal)
    tier: str                  # cheap | smart
    deployment: str
    input_tokens: int
    output_tokens: int
    cost_inr_local: float      # our computed cost
    latency_ms: int
    ok: bool
    error: Optional[str] = None
    metricai_request_id: Optional[str] = None
    ts: float = field(default_factory=time.time)


class Ledger:
    def __init__(self, path: str = LEDGER_PATH) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.run_id = uuid.uuid4().hex[:12]

    def write(self, rec: CallRecord) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec)) + "\n")

    @staticmethod
    def read(path: str = LEDGER_PATH) -> list[dict[str, Any]]:
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    @staticmethod
    def reset(path: str = LEDGER_PATH) -> None:
        if os.path.exists(path):
            os.remove(path)
