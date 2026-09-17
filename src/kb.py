"""A deliberately tiny knowledge base.

Retrieval is free (no LLM), but it changes cost: enrichment injects more input
tokens into the draft step. That makes "retrieval is cheap" measurably false,
which is a useful thing to be able to show in MetricAI.
"""
from __future__ import annotations

ARTICLES: list[dict[str, str]] = [
    {"id": "kb-001", "tag": "billing",
     "text": "Refunds are available within 30 days of purchase. Pro-rated refunds "
             "apply to annual plans cancelled after 30 days. Refunds land in 5-7 "
             "business days on the original payment method."},
    {"id": "kb-002", "tag": "billing",
     "text": "Invoices are issued on the 1st of each month. GST is applied for "
             "Indian customers. Invoice history lives under Settings > Billing."},
    {"id": "kb-003", "tag": "auth",
     "text": "Password resets expire after 60 minutes. SSO customers must reset "
             "through their identity provider, not through our reset flow."},
    {"id": "kb-004", "tag": "auth",
     "text": "Accounts lock after 10 failed sign-in attempts and unlock after 30 "
             "minutes, or immediately via an admin in the team console."},
    {"id": "kb-005", "tag": "api",
     "text": "API rate limits: 60 requests/minute on Free, 600 on Pro, negotiated "
             "on Enterprise. A 429 includes a Retry-After header."},
    {"id": "kb-006", "tag": "api",
     "text": "Webhooks retry 5 times with exponential backoff over 24 hours. "
             "Signature verification uses the HMAC secret from the dashboard."},
    {"id": "kb-007", "tag": "outage",
     "text": "Status and incident history are published at status.example.com. "
             "Enterprise customers receive incident RCAs within 5 business days."},
    {"id": "kb-008", "tag": "data",
     "text": "Data exports are generated asynchronously and emailed as a signed "
             "link valid for 24 hours. Exports over 2GB are split into parts."},
]


def retrieve(category: str, query: str, limit: int = 2) -> list[dict[str, str]]:
    """Category filter plus naive keyword overlap. Good enough, and free."""
    pool = [a for a in ARTICLES if a["tag"] == category] or ARTICLES
    terms = {w.lower().strip(".,?!") for w in query.split() if len(w) > 3}
    scored = sorted(
        pool,
        key=lambda a: len(terms & {w.lower().strip(".,?!") for w in a["text"].split()}),
        reverse=True,
    )
    return scored[:limit]
