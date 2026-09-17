"""Seeded support tickets, spread across tenants so per-customer cost differs."""
from __future__ import annotations

TICKETS: list[dict[str, str]] = [
    {"id": "T-1001", "tenant_id": "acme-retail",
     "text": "I cancelled my annual plan last week and still have not seen a refund. When does it arrive?"},
    {"id": "T-1002", "tenant_id": "acme-retail",
     "text": "My team keeps getting locked out after a few wrong passwords. How do we unlock faster?"},
    {"id": "T-1003", "tenant_id": "acme-retail",
     "text": "Where do I download last month's invoice? I cannot find it in the dashboard."},
    {"id": "T-1004", "tenant_id": "globex-saas",
     "text": "We are hitting 429s on the API around 9am every day. What is our actual limit and how do we raise it?"},
    {"id": "T-1005", "tenant_id": "globex-saas",
     "text": "Our webhook endpoint was down for two hours. Will the missed events be redelivered?"},
    {"id": "T-1006", "tenant_id": "globex-saas",
     "text": "SSO users cannot reset their password through your reset link. Is that expected?"},
    {"id": "T-1007", "tenant_id": "globex-saas",
     "text": "Requesting a full data export for compliance review. How long does it take and what format?"},
    {"id": "T-1008", "tenant_id": "initech-bank",
     "text": "We need the RCA for yesterday's incident for our regulator. What is the SLA on that document?"},
    {"id": "T-1009", "tenant_id": "initech-bank",
     "text": "Explain exactly how webhook signature verification works; our security team needs specifics."},
    {"id": "T-1010", "tenant_id": "initech-bank",
     "text": "We were charged GST on an invoice but our entity is registered overseas. Can this be corrected?"},
    {"id": "T-1011", "tenant_id": "initech-bank",
     "text": "Our export is 4GB and arrived in pieces. Confirm the parts are complete and the link validity window."},
    {"id": "T-1012", "tenant_id": "initech-bank",
     "text": "Rate limits were negotiated at contract signing but we are seeing throttling. Please verify our tier."},
]
