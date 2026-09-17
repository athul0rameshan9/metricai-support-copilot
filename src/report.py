"""Turn the shadow ledger into the numbers the business actually asks for.

Scenarios are kept apart: the baseline run is the product as shipped, the
model-swap run is the counterfactual, the runaway probe is the budget test.
Averaging them together would be meaningless.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from tabulate import tabulate

from . import settings as S
from .ledger import Ledger


def _fmt(rows, headers):
    return tabulate(rows, headers=headers, tablefmt="github", floatfmt=".4f")


def _pct(x: float) -> str:
    return "n/a" if x != x else f"{x:.1f}%"


def build_report(path: str = "out/ledger.jsonl") -> str:
    recs = Ledger.read(path)
    if not recs:
        return "No ledger records. Run the agent first, e.g. `python -m src.run all --mock`."

    base = [r for r in recs if r.get("scenario", "baseline") == "baseline"]
    swap = [r for r in recs if r.get("scenario") == "model-swap"]
    runaway = [r for r in recs if r.get("scenario") == "runaway"]
    if not base:
        base = recs

    out: list[str] = ["# Cost report (local shadow ledger)", ""]
    total = sum(r["cost_inr_local"] for r in base)
    tickets = {r["ticket_id"] for r in base}
    out += [
        f"Baseline run: **{len(base)} calls** across **{len(tickets)} tickets**, "
        f"**INR {total:.4f}** total, **INR {total/len(tickets):.4f}** mean per ticket.",
        "",
        f"FX used locally: 1 USD = INR {S.USD_INR:.2f}. MetricAI converts on its own "
        "rate -- any gap between the two dashboards starts here.",
        "",
    ]

    # --- per step -----------------------------------------------------------
    by_node: dict[str, list[float]] = defaultdict(list)
    tok_node: dict[str, list[int]] = defaultdict(list)
    for r in base:
        by_node[r["node_id"]].append(r["cost_inr_local"])
        tok_node[r["node_id"]].append(r["input_tokens"] + r["output_tokens"])
    rows = [[n, len(v), sum(v), sum(v) / len(v), 100 * sum(v) / total, sum(tok_node[n])]
            for n, v in sorted(by_node.items(), key=lambda kv: -sum(kv[1]))]
    out += ["## Cost by workflow step", "",
            _fmt(rows, ["step", "calls", "total INR", "mean INR", "% of spend", "tokens"]), ""]

    # --- per model ----------------------------------------------------------
    by_dep: dict[str, list[float]] = defaultdict(list)
    for r in base:
        by_dep[f'{r["deployment"]} ({r["tier"]})'].append(r["cost_inr_local"])
    rows = [[d, len(v), sum(v), 100 * sum(v) / total]
            for d, v in sorted(by_dep.items(), key=lambda kv: -sum(kv[1]))]
    out += ["## Cost by model", "",
            _fmt(rows, ["deployment", "calls", "total INR", "% of spend"]), ""]

    # --- per customer, with margin -----------------------------------------
    by_t: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"cost": 0.0, "tickets": set(), "plan": "", "calls": 0})
    for r in base:
        b = by_t[r["tenant_id"]]
        b["cost"] += r["cost_inr_local"]
        b["tickets"].add(r["ticket_id"])
        b["plan"] = r["plan"]
        b["calls"] += 1

    rows = []
    for tid, b in sorted(by_t.items(), key=lambda kv: -kv[1]["cost"]):
        n = len(b["tickets"])
        price = S.PLANS[b["plan"]].price_inr_per_ticket
        revenue = price * n
        margin = 100 * (revenue - b["cost"]) / revenue if revenue else float("nan")
        rows.append([tid, b["plan"], n, b["calls"], b["cost"], b["cost"] / n,
                     price, revenue - b["cost"], _pct(margin)])
    out += ["## Cost and margin by customer", "",
            _fmt(rows, ["customer", "plan", "tickets", "calls", "cost INR",
                        "cost/ticket", "price/ticket", "gross INR", "margin"]),
            "",
            "Free-tier margin is negative by construction; the real question is whether "
            "the per-ticket loss sits inside the acquisition budget. Note that cost per "
            "ticket is near-identical for Pro and Enterprise while price is 3x -- the "
            "plan tiers are priced on entitlements, not on cost to serve.", ""]

    # --- retry waste --------------------------------------------------------
    retries = [r for r in base if r["attempt"] > 1]
    waste = sum(r["cost_inr_local"] for r in retries)
    out += ["## Retry waste", "",
            f"Retry calls: **{len(retries)}** of {len(base)} "
            f"({100*len(retries)/len(base):.1f}%). Spend on superseded attempts: "
            f"**INR {waste:.4f}** ({100*waste/total:.1f}% of baseline spend).",
            "",
            "Every one of those rupees bought an answer the user never saw. This is the "
            "number an agent cost tool should surface without being asked.", ""]

    # --- model-swap counterfactual -----------------------------------------
    if swap:
        s_total = sum(r["cost_inr_local"] for r in swap)
        s_tickets = {r["ticket_id"] for r in swap}
        saving = 100 * (total - s_total) / total if total else 0.0
        out += ["## Counterfactual: cheap model for drafting", "",
                _fmt([["baseline (smart drafts)", len(tickets), total, total / len(tickets)],
                      ["model-swap (cheap drafts)", len(s_tickets), s_total,
                       s_total / len(s_tickets)]],
                     ["variant", "tickets", "total INR", "INR/ticket"]),
                "",
                f"Dropping the smart model on the draft step cuts spend by **{saving:.1f}%**. "
                "Whether that is a good trade depends on answer quality, which cost data "
                "alone cannot tell you -- see the feedback notes.", ""]

    # --- runaway probe ------------------------------------------------------
    if runaway:
        r_total = sum(r["cost_inr_local"] for r in runaway)
        cap = S.PLANS["free"].budget_cap_inr
        out += ["## Budget cap probe", "",
                f"{len(runaway)} calls landed on a single session against a INR {cap:.2f} cap, "
                f"spending INR {r_total:.4f} before the cap engaged.",
                "",
                f"Overshoot past the cap: **INR {max(0.0, r_total - cap):.4f}**. "
                "If this is above zero against live MetricAI, the cap is being evaluated "
                "after the fact rather than in the request path.", ""]

    # --- per ticket ---------------------------------------------------------
    by_tick: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"cost": 0.0, "calls": 0, "tenant": ""})
    for r in base:
        b = by_tick[r["ticket_id"]]
        b["cost"] += r["cost_inr_local"]
        b["calls"] += 1
        b["tenant"] = r["tenant_id"]
    rows = [[t, b["tenant"], b["calls"], b["cost"]]
            for t, b in sorted(by_tick.items(), key=lambda kv: -kv[1]["cost"])][:10]
    out += ["## Most expensive executions", "",
            _fmt(rows, ["ticket", "customer", "calls", "cost INR"]), ""]

    errs = [r for r in recs if not r["ok"]]
    if errs:
        out += ["## Failed calls", ""]
        out += [f"- `{r['ticket_id']}` / `{r['node_id']}`: {r['error']}" for r in errs[:10]]
        out += [""]

    return "\n".join(out)
