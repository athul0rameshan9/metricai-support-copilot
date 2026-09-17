"""Tenants, plans, model tiers and pricing.

Everything that decides *how much a ticket costs* lives here, so the economics
are inspectable in one place rather than scattered through the agent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

Tier = Literal["cheap", "smart"]

METRICAI_API_KEY = os.getenv("METRICAI_API_KEY", "")
METRICAI_BASE_URL = os.getenv("METRICAI_BASE_URL") or None

AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")

DEPLOYMENTS: dict[Tier, str] = {
    "cheap": os.getenv("AZURE_DEPLOYMENT_CHEAP", "gpt-4o-mini"),
    "smart": os.getenv("AZURE_DEPLOYMENT_SMART", "gpt-4o"),
}

USD_INR = float(os.getenv("USD_INR", "88.0"))

# USD per 1M tokens -> INR per token, computed once.
_PRICES_USD_PMT: dict[Tier, tuple[float, float]] = {
    "cheap": (
        float(os.getenv("PRICE_CHEAP_IN_USD_PMT", "0.15")),
        float(os.getenv("PRICE_CHEAP_OUT_USD_PMT", "0.60")),
    ),
    "smart": (
        float(os.getenv("PRICE_SMART_IN_USD_PMT", "2.50")),
        float(os.getenv("PRICE_SMART_OUT_USD_PMT", "10.00")),
    ),
}


def cost_inr(tier: Tier, in_tokens: int, out_tokens: int) -> float:
    """Our own estimate of a call's cost, used to reconcile against MetricAI."""
    p_in, p_out = _PRICES_USD_PMT[tier]
    usd = (in_tokens * p_in + out_tokens * p_out) / 1_000_000
    return usd * USD_INR


@dataclass(frozen=True)
class Plan:
    """A pricing plan. `price_inr_per_ticket` is what we charge; cost is measured."""

    name: str
    price_inr_per_ticket: float
    budget_cap_inr: float          # enforced by MetricAI per session
    use_kb_enrichment: bool        # extra retrieval pass -> more input tokens
    draft_tier: Tier               # cheap tier = margin lever
    max_revisions: int             # how hard we chase quality


PLANS: dict[str, Plan] = {
    "free": Plan("free", price_inr_per_ticket=0.00, budget_cap_inr=2.0,
                 use_kb_enrichment=False, draft_tier="cheap", max_revisions=0),
    "pro": Plan("pro", price_inr_per_ticket=3.00, budget_cap_inr=25.0,
                use_kb_enrichment=True, draft_tier="smart", max_revisions=1),
    "enterprise": Plan("enterprise", price_inr_per_ticket=9.00, budget_cap_inr=200.0,
                       use_kb_enrichment=True, draft_tier="smart", max_revisions=2),
}


@dataclass(frozen=True)
class Tenant:
    """An end customer of our support product. Maps to MetricAI's user_id."""

    tenant_id: str
    display_name: str
    plan: str

    @property
    def plan_obj(self) -> Plan:
        return PLANS[self.plan]


TENANTS: list[Tenant] = [
    Tenant("acme-retail", "Acme Retail", "free"),
    Tenant("globex-saas", "Globex SaaS", "pro"),
    Tenant("initech-bank", "Initech Bank", "enterprise"),
]

TENANTS_BY_ID = {t.tenant_id: t for t in TENANTS}

# MetricAI attribution constants
AGENT_ID = "support-copilot"
GRAPH_ID = "support-triage-v1"

QUALITY_BAR = 4  # critique score (1-5) at or above which we ship the draft
