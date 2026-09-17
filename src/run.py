"""Scenario runner.

    python -m src.run baseline   --mock     # 15 tickets, 3 tenants, 3 plans
    python -m src.run runaway    --mock     # deliberately breach a budget cap
    python -m src.run model-swap --mock     # same tickets, cheap drafts
    python -m src.run report                # rebuild the report from the ledger
"""
from __future__ import annotations

import argparse
import sys

from . import settings as S
from .ledger import CallRecord, Ledger
from .llm import BudgetExceeded, get_client
from .report import build_report
from .tickets import TICKETS
from .workflow import SupportCopilot


def _print_result(r) -> None:
    flag = " BUDGET-BLOCKED" if r.budget_blocked else ""
    err = f"  !{len(r.errors)} err" if r.errors else ""
    print(f"  {r.ticket_id}  {r.tenant_id:<13} {r.plan:<11} "
          f"calls={r.calls:<2} rev={r.revisions} "
          f"cost=INR {r.cost_inr:7.4f}  waste=INR {r.wasted_inr:6.4f}"
          f"{flag}{err}")


def scenario_baseline(client, ledger, tier_override=None, name="baseline") -> None:
    bot = SupportCopilot(client, ledger, scenario=name, draft_tier_override=tier_override)
    results = [bot.run_ticket(t) for t in TICKETS]
    for r in results:
        _print_result(r)
    total = sum(r.cost_inr for r in results)
    waste = sum(r.wasted_inr for r in results)
    print(f"\n  TOTAL INR {total:.4f} across {len(results)} tickets "
          f"(INR {total/len(results):.4f}/ticket); superseded drafts INR {waste:.4f}")


def scenario_runaway(client, ledger) -> None:
    """Does the budget cap actually stop the call, or just record the overage?

    We take the free tenant (cap INR 2) and hammer one session until MetricAI
    refuses. The interesting output is *where* it stops -- or whether it does.
    """
    tenant = S.TENANTS_BY_ID["acme-retail"]
    cap = tenant.plan_obj.budget_cap_inr
    session_id = "runaway-probe-001"
    print(f"  Probing session {session_id} against a INR {cap} cap "
          f"(user_id={tenant.tenant_id})")

    spent = 0.0
    for i in range(1, 41):
        try:
            res = client.complete(
                tier="smart",
                messages=[{"role": "system", "content": "Write a long, detailed essay."},
                          {"role": "user", "content":
                           "Explain our refund policy in exhaustive detail. " * 12}],
                tenant_id=tenant.tenant_id,
                session_id=session_id,
                node_id="runaway",
                budget_cap_inr=cap,
                attempt=i,
                max_tokens=700,
            )
        except BudgetExceeded as exc:
            print(f"  STOPPED at call #{i} after INR {spent:.4f} of a INR {cap} cap")
            print(f"  -> {exc}")
            print("  Record for the writeup: did MetricAI block this in the request "
                  "path, or did the call still reach Azure and get billed?")
            return
        cost = S.cost_inr("smart", res.input_tokens, res.output_tokens)
        ledger.write(CallRecord(
            run_id=ledger.run_id, scenario="runaway", ticket_id=session_id,
            tenant_id=tenant.tenant_id, plan=tenant.plan_obj.name, session_id=session_id,
            node_id="runaway", attempt=i, tier="smart", deployment=S.DEPLOYMENTS["smart"],
            input_tokens=res.input_tokens, output_tokens=res.output_tokens,
            cost_inr_local=cost, latency_ms=res.latency_ms, ok=res.error is None,
            error=res.error, metricai_request_id=res.request_id))
        spent += cost
        print(f"    call #{i:<2} cumulative INR {spent:.4f}")

    print(f"  NOT STOPPED after 40 calls and INR {spent:.4f} against a INR {cap} cap.")
    print("  That is a finding: the cap did not hold in the request path.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", choices=["baseline", "runaway", "model-swap", "report", "all"])
    ap.add_argument("--mock", action="store_true",
                    help="run without live keys (deterministic stub responses)")
    ap.add_argument("--reset", action="store_true", help="clear the ledger first")
    args = ap.parse_args()

    if args.reset:
        Ledger.reset()

    if args.scenario == "report":
        report = build_report()
        print(report)
        with open("out/report.md", "w", encoding="utf-8") as fh:
            fh.write(report)
        print("\nWritten to out/report.md")
        return 0

    client = get_client(mock=args.mock)
    ledger = Ledger()
    mode = "MOCK" if args.mock else "LIVE via MetricAI -> Azure OpenAI"
    print(f"\n=== {args.scenario} [{mode}] run_id={ledger.run_id} ===\n")

    if args.scenario in ("baseline", "all"):
        scenario_baseline(client, ledger)
    if args.scenario in ("model-swap", "all"):
        print("\n--- model-swap: force cheap drafts for every tier ---")
        scenario_baseline(client, ledger, tier_override="cheap", name="model-swap")
    if args.scenario in ("runaway", "all"):
        print("\n--- runaway: budget cap probe ---")
        scenario_runaway(client, ledger)

    print("\nLedger: out/ledger.jsonl   ->   python -m src.run report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
