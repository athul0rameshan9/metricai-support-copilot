# Support Copilot — a multi-tenant agent instrumented with MetricAI

A small support-ticket agent built to be *measured*, not to be impressive. It
serves three simulated customers on three pricing plans, runs a multi-step
workflow with a quality-gated retry loop, and routes every LLM call through
[MetricAI](https://metricai.co.in) so spend can be attributed by customer,
agent, session and workflow step.

The interesting question this repo tries to answer is not "can an agent answer a
support ticket" — it can — but **what does each ticket cost, where does the money
go, and does that leave a margin.**

## Stack

- **Python 3.10+**, standard library only for the agent itself — no LangChain, no
  LlamaIndex, no framework. The workflow is a plain class with explicit steps, so
  every LLM call is visible at the call site and easy to attribute.
- **[MetricAI](https://metricai.co.in) `metricai==0.8.4`** — BYOK proxy for
  metering, attribution and in-path budget caps.
- **`openai>=1.40`** — Azure OpenAI client for the smart tier (drafting).
- **`google-genai>=1.0`** — Gemini client for the cheap tier (triage, critique,
  summarise).
- **`python-dotenv`** for config, **`tabulate`** for the report tables.
- Storage is files: `out/ledger.jsonl` (shadow ledger) and `out/report.md`. No
  database, no server, no vector store — the KB is a dict in `src/kb.py`.

## The workflow

```
ticket ──▶ triage (cheap model = Gemini)
              │
              ├──▶ check_outage_status (tool, free) — only if category = outage
              │
              ▼
          retrieve (local KB, free)
              │
              ▼
           draft (model tier depends on plan) ◀──┐
              │                                  │
              ▼                                  │ score < 4
          critique (cheap model) ────────────────┘ and revisions left
              │
              ▼
         summarise (cheap model)
```

One ticket = one MetricAI **session**. Each box is a distinct **node_id**, so
"which step is eating the budget" is answerable in the dashboard without reading
any code. The tool call is reported through `mc.track(...)` as
`tool:check_outage_status`, so non-LLM steps sit beside the LLM ones in MetricAI's
Tools tab.

### Plans, and why they differ

| plan | price/ticket | session cap | KB enrichment | draft model | max revisions |
|---|---|---|---|---|---|
| free | ₹0 | ₹2 | no | cheap | 0 |
| pro | ₹3 | ₹25 | yes | smart | 1 |
| enterprise | ₹9 | ₹200 | yes | smart | 2 |

The plans deliberately have different **cost to serve**, not just different
entitlements. That is what makes the margin table meaningful.

## MetricAI integration

BYOK mode: our provider keys (Gemini for the cheap tier, Azure OpenAI for the
smart tier) travel as headers to the MetricAI proxy, which forwards the call,
meters it, and attributes the spend. The vendor SDK calls themselves are
unchanged — only their `base_url` moves.

```python
from metricai import MetricAI

mc = MetricAI(
    api_key=METRICAI_API_KEY,
    mode="byok",
    llm_keys={
        "gemini": GEMINI_API_KEY,
        "azure_openai": AZURE_OPENAI_API_KEY,
        "azure_openai_endpoint": AZURE_OPENAI_ENDPOINT,
        "azure_openai_api_version": AZURE_OPENAI_API_VERSION,
    },
    active_providers=("gemini", "azure_openai"),
    fail_open=True,          # metering must never take the product down
)

# smart tier (drafting) -> Azure; cheap tier -> mc.gemini_sdk(...) with the
# same attribution kwargs, returning a google-genai Client.
client = mc.azure_openai_sdk(
    agent_id="support-copilot",   # which agent
    user_id=tenant_id,            # which paying customer
    session_id=f"ticket-{tid}",   # which ticket
    graph_id="support-triage-v1", # which workflow version
    node_id="draft",              # which step
    budget_cap_inr=plan.budget_cap_inr,
    idempotency_key=...,          # network retries must not double-bill
)

client.chat.completions.create(model=AZURE_DEPLOYMENT, messages=[...])
```

Attribution dimensions in use: `agent_id`, `user_id`, `session_id`, `graph_id`,
`node_id`, plus a per-session `budget_cap_inr`.

### The shadow ledger

Every call is also written to `out/ledger.jsonl` with our own token counts and
our own INR cost. This is deliberate. MetricAI is the system of record; the
local ledger is a second opinion that lets us:

1. **reconcile** — does MetricAI's number match ours? Pricing tables and USD→INR
   conversion are the usual places a gap opens up;
2. **run counterfactuals** — what would these same tickets have cost on the cheap
   model? A dashboard can only show you what you already spent.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in MetricAI + Gemini + Azure OpenAI values

python -m src.run all --mock --reset   # no keys needed, deterministic
python -m src.run baseline             # live, through MetricAI
python -m src.run runaway              # budget-cap probe
python -m src.run model-swap           # cheap-draft counterfactual
python -m src.run report               # writes out/report.md
```

`--mock` runs the entire suite with stubbed responses and a locally simulated
budget cap, so the pipeline and the arithmetic can be verified without spending
anything.

## Scenarios

**baseline** — 15 tickets across 3 tenants (3 of them outage tickets that
exercise the tool call). Produces the cost-per-ticket,
cost-per-step and margin tables.

**runaway** — points repeated expensive calls at one session with a ₹2 cap. The
point is to find out *where* MetricAI stops it, and whether the overshoot is
zero. A non-zero overshoot means the cap is evaluated after the fact rather than
in the request path — which matters, because in-path enforcement is the headline
claim.

**model-swap** — the same 15 tickets with the cheap model forced on the draft
step. Quantifies the saving so the quality trade-off can be argued with a number
attached.

## What the report answers

- cost per individual execution (per ticket, most expensive first)
- which step and which model consume the spend
- cost and gross margin per customer and per plan
- how much was spent on drafts that were thrown away (retry waste)
- what the cheap-model variant would have cost instead
- whether the budget cap held

## Layout

```
src/settings.py   tenants, plans, model tiers, pricing — all economics in one file
src/llm.py        MetricAI-instrumented Azure client, plus a mock client
src/workflow.py   the agent
src/kb.py         tiny knowledge base
src/tickets.py    seeded tickets
src/tools.py      fake non-LLM tool (status-page check)
src/ledger.py     shadow ledger
src/report.py     cost / margin / waste report
src/run.py        scenario runner
docs/FINDINGS.md  integration experience and product feedback
docs/LIVE_RUN_CHECKLIST.md  ordered steps for the live (non-mock) run
```
