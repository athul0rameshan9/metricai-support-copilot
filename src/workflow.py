"""The support copilot workflow.

    triage (cheap)  ->  retrieve (free)  ->  draft (tier by plan)
                                              |
                                        critique (cheap)
                                              |
                              score < bar and revisions left? -> re-draft
                                              |
                                      summarise (cheap)

One ticket = one MetricAI session. Each step carries a distinct node_id, so
"which step eats the budget" is answerable without reading any code.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import kb, settings as S, tools
from .ledger import CallRecord, Ledger
from .llm import BudgetExceeded, LLMResult


@dataclass
class TicketResult:
    ticket_id: str
    tenant_id: str
    plan: str
    session_id: str
    answer: str
    quality_score: Optional[int]
    revisions: int
    calls: int
    cost_inr: float
    wasted_inr: float          # spend on drafts that were thrown away
    budget_blocked: bool = False
    errors: list[str] = field(default_factory=list)


def _json_field(text: str, key: str, default: Any) -> Any:
    """LLMs return almost-JSON. Parse leniently rather than crashing the run."""
    try:
        blob = re.search(r"\{.*\}", text, re.S)
        if blob:
            return json.loads(blob.group(0)).get(key, default)
    except (json.JSONDecodeError, AttributeError):
        pass
    m = re.search(rf'"?{key}"?\s*[:=]\s*"?([A-Za-z0-9_]+)"?', text)
    return m.group(1) if m else default


class SupportCopilot:
    def __init__(self, client, ledger: Ledger, *, scenario: str = "baseline",
                 draft_tier_override: Optional[str] = None):
        self.client = client
        self.ledger = ledger
        self.scenario = scenario
        self.draft_tier_override = draft_tier_override

    # -- one metered call, recorded in both MetricAI and the shadow ledger --
    def _call(self, *, ctx: dict, node_id: str, tier: str, attempt: int,
              messages: list[dict[str, str]], max_tokens: int = 400) -> LLMResult:
        res = self.client.complete(
            tier=tier,
            messages=messages,
            tenant_id=ctx["tenant_id"],
            session_id=ctx["session_id"],
            node_id=node_id,
            budget_cap_inr=ctx["budget_cap_inr"],
            attempt=attempt,
            max_tokens=max_tokens,
        )
        self.ledger.write(CallRecord(
            run_id=self.ledger.run_id,
            scenario=self.scenario,
            ticket_id=ctx["ticket_id"],
            tenant_id=ctx["tenant_id"],
            plan=ctx["plan"],
            session_id=ctx["session_id"],
            node_id=node_id,
            attempt=attempt,
            tier=tier,
            deployment=S.DEPLOYMENTS[tier],
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            cost_inr_local=S.cost_inr(tier, res.input_tokens, res.output_tokens),
            latency_ms=res.latency_ms,
            ok=res.error is None,
            error=res.error,
            metricai_request_id=res.request_id,
        ))
        return res

    # -- one fake tool call, tracked in MetricAI and the shadow ledger --
    def _tool_call(self, *, ctx: dict, name: str, fn, **kwargs) -> dict:
        t0 = time.perf_counter()
        result = fn(**kwargs)
        latency = int((time.perf_counter() - t0) * 1000)
        node_id = f"tool:{name}"
        self.client.track_tool(
            name=name, tenant_id=ctx["tenant_id"], session_id=ctx["session_id"],
            node_id=node_id, latency_ms=latency, success=True, result=result,
        )
        self.ledger.write(CallRecord(
            run_id=self.ledger.run_id, scenario=self.scenario,
            ticket_id=ctx["ticket_id"], tenant_id=ctx["tenant_id"], plan=ctx["plan"],
            session_id=ctx["session_id"], node_id=node_id, attempt=1,
            tier="tool", deployment=name, input_tokens=0, output_tokens=0,
            cost_inr_local=0.0, latency_ms=latency, ok=True,
        ))
        return result

    def run_ticket(self, ticket: dict[str, str]) -> TicketResult:
        tenant = S.TENANTS_BY_ID[ticket["tenant_id"]]
        plan = tenant.plan_obj
        session_id = f"ticket-{ticket['id']}"
        ctx = {
            "ticket_id": ticket["id"],
            "tenant_id": tenant.tenant_id,
            "plan": plan.name,
            "session_id": session_id,
            "budget_cap_inr": plan.budget_cap_inr,
        }

        result = TicketResult(ticket["id"], tenant.tenant_id, plan.name, session_id,
                              answer="", quality_score=None, revisions=0, calls=0,
                              cost_inr=0.0, wasted_inr=0.0)
        spend: list[float] = []
        draft_costs: list[float] = []

        def account(res: LLMResult, tier: str) -> float:
            c = S.cost_inr(tier, res.input_tokens, res.output_tokens)
            spend.append(c)
            result.calls += 1
            if res.error:
                result.errors.append(res.error)
            return c

        try:
            # 1. Triage -- cheap model, tight output.
            triage = self._call(
                ctx=ctx, node_id="triage", tier="cheap", attempt=1, max_tokens=60,
                messages=[
                    {"role": "system", "content":
                     "Classify the support ticket. Reply with JSON only: "
                     '{"category": one of billing|auth|api|outage|data, '
                     '"urgency": one of low|medium|high}.'},
                    {"role": "user", "content": ticket["text"]},
                ])
            account(triage, "cheap")
            category = str(_json_field(triage.text, "category", "billing"))
            urgency = str(_json_field(triage.text, "urgency", "medium"))

            # 1b. Outage tickets: ask the (fake) status page before drafting, so
            # the answer says whether an incident is actually open.
            status_note = ""
            if category == "outage":
                status = self._tool_call(
                    ctx=ctx, name="check_outage_status", fn=tools.check_outage_status,
                    category=category, seed=ticket["id"])
                status_note = (
                    "\n\nLive status check: "
                    + (f"OUTAGE CONFIRMED, ETA {status['eta_minutes']} min."
                       if status["outage"] else "no incident open; all systems operational.")
                )

            # 2. Retrieve -- free, but it inflates the draft prompt.
            n = 2 if plan.use_kb_enrichment else 1
            articles = kb.retrieve(category, ticket["text"], limit=n)
            context = "\n".join(f"[{a['id']}] {a['text']}" for a in articles)

            # 3-4. Draft, critique, and possibly re-draft.
            draft_tier = self.draft_tier_override or plan.draft_tier
            attempt = 1
            answer = ""
            score: Optional[int] = None

            while True:
                feedback = ""
                if attempt > 1 and score is not None:
                    feedback = (f"\n\nA reviewer scored your previous draft {score}/5. "
                                f"Fix it. Previous draft:\n{answer}")

                draft = self._call(
                    ctx=ctx, node_id="draft", tier=draft_tier, attempt=attempt, max_tokens=420,
                    messages=[
                        {"role": "system", "content":
                         "You are a support agent. Answer using ONLY the knowledge base "
                         "context. Cite article ids in square brackets. Be concise and warm."},
                        {"role": "user", "content":
                         f"Ticket ({category}, urgency {urgency}):\n{ticket['text']}\n\n"
                         f"Knowledge base:\n{context}{status_note}{feedback}"},
                    ])
                c = account(draft, draft_tier)
                draft_costs.append(c)
                answer = draft.text or answer

                if plan.max_revisions == 0:
                    break  # free tier ships the first draft, by design

                critique = self._call(
                    ctx=ctx, node_id="critique", tier="cheap", attempt=attempt, max_tokens=120,
                    messages=[
                        {"role": "system", "content":
                         'Grade the draft answer. JSON only: {"score": 1-5, "reason": "..."}. '
                         "Score 5 only if it is accurate, complete and cites the right articles."},
                        {"role": "user", "content":
                         f"Ticket:\n{ticket['text']}\n\nContext:\n{context}\n\nDraft:\n{answer}"},
                    ])
                account(critique, "cheap")
                try:
                    score = int(_json_field(critique.text, "score", S.QUALITY_BAR))
                except (TypeError, ValueError):
                    score = S.QUALITY_BAR

                if score >= S.QUALITY_BAR or attempt > plan.max_revisions:
                    break
                attempt += 1
                result.revisions += 1

            # 5. Internal one-line summary for the ticket log.
            summ = self._call(
                ctx=ctx, node_id="summarise", tier="cheap", attempt=1, max_tokens=80,
                messages=[
                    {"role": "system", "content": "One sentence, internal note. No greeting."},
                    {"role": "user", "content": f"Ticket: {ticket['text']}\nAnswer: {answer}"},
                ])
            account(summ, "cheap")

            result.answer = answer
            result.quality_score = score

        except BudgetExceeded as exc:
            result.budget_blocked = True
            result.errors.append(f"BudgetExceeded: {exc}")

        result.cost_inr = sum(spend)
        # Every draft except the one we shipped is money spent on nothing.
        result.wasted_inr = sum(draft_costs[:-1]) if len(draft_costs) > 1 else 0.0
        return result
