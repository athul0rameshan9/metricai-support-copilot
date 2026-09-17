# MetricAI evaluation — findings

> **Status:** sections marked `[VERIFY LIVE]` are questions the code is built to
> answer but which need a run against a real MetricAI project. Everything else is
> already observable from the SDK, the docs and the mock run.

---

## 1. Architecture

Twelve support tickets, three tenants, three plans. Each ticket is one MetricAI
session; each workflow step carries its own `node_id`. Attribution dimensions
used: `agent_id`, `user_id` (= customer), `session_id` (= ticket),
`graph_id` (= workflow version), `node_id` (= step), plus `budget_cap_inr`.

Deliberate design choices made to generate *observable* behaviour:

- **A quality-gated retry loop.** A critique step scores each draft; a low score
  triggers a re-draft. This produces genuine waste — money spent on answers the
  user never sees — which is exactly the pathology a cost tool should catch.
- **Tier-dependent model routing.** Free tier drafts on the cheap model,
  paid tiers on the expensive one. Cost to serve therefore differs by customer,
  which is what makes a margin table mean anything.
- **A shadow ledger.** Our own token counts and INR costs, written per call, so
  MetricAI's numbers can be reconciled rather than trusted.
- **A budget-cap probe.** One session, a ₹2 cap, and repeated expensive calls,
  to find out whether enforcement is in-path or after-the-fact.

---

## 2. Integration experience

### What worked

- **BYOK through the proxy is genuinely low-friction.** `mc.azure_openai_sdk(...)`
  hands back a normal `openai.OpenAI` client. Existing `chat.completions.create`
  calls are untouched — only the `base_url` and headers change. Migrating an
  existing codebase is plausibly a one-line change per client construction.
- **Attribution is passed per-call, not per-client-global.** Being able to set
  `node_id` on each step without building a second client is what makes
  step-level cost attribution practical in a workflow.
- **`fail_open=True` is the right default** and is documented as such. Metering
  that can take down the product is worse than no metering.
- **`idempotency_key` exists.** Retry-heavy agents need this or network retries
  become double billing.
- **The dimension vocabulary is well chosen** — `graph_id`/`node_id`/`crew_id`/
  `task_id` map onto how agent frameworks actually decompose work, rather than
  forcing everything into a flat "tag" bag.

### What was difficult or confusing

- **Azure OpenAI is under-documented relative to its support.** The docs page
  says only that Azure works "through standard provider routing… configure your
  Azure keys in environment variables and use the proxy endpoints accordingly."
  The actual contract — that you pass `llm_keys={"azure_openai": ..., 
  "azure_openai_endpoint": ..., "azure_openai_api_version": ...}` and that
  `model=` must be your **deployment name**, not a model name — I worked out by
  reading `metricai/config.py` and `providers/azure_openai_route.py` in the
  installed package. A first-time Azure user without the patience to read the
  wheel would be stuck.
- **Two overlapping ways to do the same thing.** `client.headers(...)` +
  `extra_headers`, `client.azure_openai_sdk(...)`, `MetricAISession`,
  `attribution_scope`, `init(auto_instrument=True)`, and `client.track(...)` all
  exist. It is not obvious which is canonical, or what happens if you combine
  auto-instrumentation with an explicitly constructed SDK client. The quickstart
  should pick one and lead with it.
- **`MetricAISession` in the docs shows a manual `session.track(...)` call right
  after a proxied completion.** If the proxy already metered that call, is that
  a double count? The example does not say. `[VERIFY LIVE]`
- **INR-only costing with an unstated FX rate.** Our ledger uses a configurable
  `USD_INR`. MetricAI converts on its own rate at its own time. For anyone whose
  Azure bill arrives in USD, a reconcilable ledger needs the FX rate and
  timestamp exposed per event, not just the INR figure. `[VERIFY LIVE]`
- **Budget-cap failure mode is not specified.** What does a blocked call raise —
  HTTP 402, 429, `MetricAIQuotaExhaustedError`? Our `_looks_like_budget_block()`
  in `src/llm.py` has to guess across all three, which tells you the contract
  needs documenting. `[VERIFY LIVE]`

### Time to first metered call

`[VERIFY LIVE — record this. It is the single most useful number in this whole
document for the MetricAI team.]`

---

## 3. Value observed

| Question the assignment asks | Answer | Evidence |
|---|---|---|
| Cost of an individual agent execution | Yes | per-ticket table in `out/report.md`; `session_id` per ticket in MetricAI |
| Which step/model consumes the most | Yes | `node_id` breakdown — drafting is ~97% of spend in the baseline |
| Cost per workflow / per customer | Yes | `user_id` + `graph_id`; margin table |
| Retries and inefficient behaviour | **Partly** | our ledger flags `attempt > 1`; MetricAI sees the calls but has no notion of a *superseded* attempt |
| Budgets / preventing surprise spend | `[VERIFY LIVE]` | runaway probe |
| Pricing decisions | Yes | cost/ticket vs price/ticket per plan |
| Margin / unit economics | **Partly** | MetricAI has the cost side; revenue lives elsewhere |

**The finding that mattered most.** In the baseline run, drafting is ~97% of
spend and roughly half of total spend goes on drafts that were discarded by the
critique step. Neither number is visible from an Azure invoice, and neither is
something you would guess — the intuition is that "more calls" is the problem,
when in fact it is "the expensive call, repeated."

**The counterfactual that a dashboard cannot give you.** Forcing the cheap model
on the draft step cuts spend ~92%. MetricAI can tell you what you spent; it
cannot tell you what you *would have* spent. That gap is why the shadow ledger
exists, and it is a product opportunity.

---

## 4. Critical feedback

### Useful

- Per-call attribution with agent/user/session/node dimensions.
- BYOK — no requirement to hand over provider keys to get metering.
- In-path budget caps, *if* they hold. This is the real differentiator versus
  tracing tools like Langfuse or Helicone, which observe but do not enforce.
- INR-native costing and UPI settlement: a genuine wedge for the Indian market
  that US-based competitors do not serve.

### Questionable

- **Invoice generation, payment links, UPI settlement, invoice markup.** These
  turn MetricAI from an infrastructure tool into a billing system. That is a much
  harder sell, a much longer procurement cycle, and puts it against Stripe and
  Chargebee rather than against Langfuse. The metering is the valuable part; I
  would want to see the billing half justified separately.
- **Breadth of provider support as a headline.** Eighteen providers reads as
  impressive on the landing page, but I only needed one, and the one I needed was
  the thinnest documented. Depth beats breadth for a developer tool.
- **"Outcome" and "hybrid" billing modes** are defaults in the SDK
  (`default_billing_mode="hybrid"`) but I could not find an explanation of what
  they change. A default whose meaning is undocumented is a liability.

### Missing

1. **Cost per execution as a first-class view.** Sessions are the right
   primitive, but the question "what did *this one ticket* cost, broken down by
   step, including its retries" should be a page, not a query I assemble.
2. **A notion of a wasted call.** MetricAI can see that `draft` ran twice on one
   session. It cannot know the first result was thrown away. An explicit
   `superseded=true` flag (or an outcome marker on a session) would let the tool
   report waste directly — this is the highest-value thing missing.
3. **Counterfactual / what-if.** "Re-price the last 30 days as if drafting ran on
   gpt-4o-mini." The data is all there; the feature is not.
4. **Quality alongside cost.** Cost data without a quality signal makes every
   optimisation look good. Let callers attach a score to a session and the
   cost-per-*acceptable*-outcome becomes computable.
5. **Revenue in, for real margin.** Plan price per customer is a small amount of
   configuration and it turns a cost dashboard into a margin dashboard. That is a
   much stronger product.
6. **Exportable raw events.** A documented event export (or webhook) so the
   ledger can live in the customer's own warehouse. Finance teams will not accept
   a vendor UI as the only source.
7. **Anomaly alerts, not just caps.** "This agent's cost per session is 3x last
   week's" is more useful than a hard cap, and less dangerous to enable.

### What would make me use it regularly

Per-execution drill-down, a waste/retry view I do not have to build, and budget
caps I have verified actually block in-path. That combination is not available
from a generic APM tool and would be worth a recurring line item.

### What would stop me

- If the cap turns out to be advisory rather than enforced, the core promise is
  gone and this becomes a more expensive Langfuse. `[VERIFY LIVE]`
- Proxy latency or an availability dependency on the request path. `fail_open`
  helps, but a proxy in front of every LLM call is a new single point of failure
  and I would want published p99 overhead and an SLA. `[VERIFY LIVE — measure
  added latency by running the same prompts direct-to-Azure and comparing.]`
- Cost-ledger drift. If MetricAI's INR and my reconstruction disagree by more
  than a rounding error and I cannot see the FX rate and pricing table used, I
  cannot put it in front of finance. `[VERIFY LIVE]`
- Sending prompt/response content through a third party. Whether the proxy
  stores payloads, and whether that is configurable, is a blocker for regulated
  customers — and MetricAI's own pitch targets exactly those (banks, voice).
  `[VERIFY LIVE — check what is retained.]`

---

## 5. Product perspective

**A. What problem is MetricAI solving?**

Not observability — the market has plenty of tracing. The problem is that agent
spend is *unattributable and unbounded*. A provider invoice is one number for
the whole company, while the business needs it split by customer, by feature and
by workflow step, and needs a ceiling that actually holds. As pricing moves from
seats to usage, every AI company has to answer "what does serving this customer
cost" and the provider bill cannot answer it. MetricAI is trying to be the meter
between the two — closer to a utility meter than to an APM.

**B. Who gets the most value?**

Teams selling an AI product to *other businesses*, where per-customer cost
decides pricing and margin, and where one customer's runaway agent can eat a
month's gross profit. Concretely: Indian B2B SaaS and AI-agent startups with
usage-based pricing and 10–500 business customers. They are large enough that
per-tenant economics matter, small enough that nobody has built internal cost
infrastructure, and INR-native billing with UPI is a real advantage for them.
Solo developers do not care — their bill is small and legible. Large enterprises
will build it in-house or already have FinOps.

**C. What should MetricAI build next?**

**Unit economics, not just metering.** Let a customer attach plan and price to a
`user_id`, and turn the cost dashboard into a margin dashboard: gross margin per
customer, per plan, per feature; which customers are unprofitable; what the
cheap-model variant would have saved. The cost half is already captured — the
missing half is one configuration screen and it changes who the buyer is, from
an engineer watching a bill to a founder deciding what to charge.

Underneath that, two enablers: an explicit **waste signal** (mark superseded
attempts and failed sessions so wasted spend is a reported number rather than
something each customer reconstructs), and a **counterfactual re-pricing engine**
over stored events. Both are computable from data MetricAI already holds.

The thing I would *de*-prioritise is going further into invoicing and
settlement. It widens the surface area against strong incumbents and competes
for the same engineering time as the metering depth that is the actual moat.
