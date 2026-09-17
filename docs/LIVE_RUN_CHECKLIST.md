# Live-run checklist

Everything marked `[VERIFY LIVE]` in FINDINGS.md, as a run order. Do these in
sequence and capture a screenshot at each numbered step — the screenshots are
part of the submission.

## 0. Setup (time this)

Start a stopwatch when you open metricai.co.in. Stop it at your first metered
call appearing in the dashboard. **Record the number.** Note every place you had
to guess.

1. Sign up, create a project, copy the project API key into `.env`.
2. Fill in Azure endpoint, key, API version and the two deployment names.
3. `pip install -r requirements.txt`

## 1. Smoke test — one ticket

```bash
python -m src.run baseline --reset 2>&1 | head -5
```

Screenshot: the dashboard showing that first call. Check it carries `agent_id`,
`user_id`, `session_id`, `node_id`. **If any dimension is missing from the UI
even though the SDK accepts it, that is a finding.**

## 2. Baseline run

```bash
python -m src.run baseline --reset && python -m src.run report
```

Screenshots to capture:
- total spend for the run
- breakdown by `node_id` (does the UI support this at all?)
- breakdown by `user_id` — the three tenants
- a single session drill-down for one ticket

Then answer:
- Can you see what **one execution** cost, without exporting anything?
- Can you see that `draft` ran twice on some sessions?
- Does MetricAI's total match `out/report.md`? Record both numbers and the gap.
- What FX rate did it use? Is it visible anywhere?

## 3. Budget cap probe — the important one

```bash
python -m src.run runaway
```

Record:
- Which call number was refused, and the exact error (type, HTTP status, body).
- The overshoot past the ₹2 cap in `out/report.md`.
- **Did the refused call still reach Azure?** Check the Azure portal metrics for
  that deployment. If the request was forwarded and only *then* rejected, you
  were billed by Microsoft for a call MetricAI told you it blocked. This is the
  single most important thing to verify in the whole exercise.

## 4. Model-swap

```bash
python -m src.run model-swap && python -m src.run report
```

Screenshot the two runs side by side in the dashboard. Can you compare two runs
in the UI, or did you need the local ledger to do it?

## 5. Latency overhead

Run a handful of identical prompts direct to Azure (bypassing the proxy) and
through MetricAI, and compare median and p95. A proxy on the request path needs
to justify itself. Record the delta.

## 6. Data retention

Find out — from the docs, the dashboard, or by asking — whether prompt and
response **content** is stored, or only token counts and metadata. If it is
stored, is that configurable? Note what you found and how hard it was to find.

## 7. Fill in FINDINGS.md

Replace every `[VERIFY LIVE]` with what you actually observed. Where something
worked well, say so specifically — the feedback is more credible when it is not
uniformly negative.
