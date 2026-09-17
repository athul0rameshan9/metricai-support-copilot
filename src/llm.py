"""MetricAI-instrumented LLM access (Gemini for the cheap tier, Azure for smart).

BYOK mode: our provider keys travel as headers to the MetricAI proxy, which
forwards the call, meters it, and attributes the spend. We never change how we
call the vendor SDKs -- only where they point:

    cheap  -> Gemini        via mc.gemini_sdk()        (google-genai Client)
    smart  -> Azure OpenAI  via mc.azure_openai_sdk()  (OpenAI Client)

Every call carries four attribution dimensions:
    agent_id   -> which agent            (support-copilot)
    user_id    -> which paying customer  (tenant_id)
    session_id -> which ticket           (one session per ticket)
    node_id    -> which workflow step    (triage / draft / critique / summarise)
plus graph_id for the workflow version, and a per-session budget cap.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from . import settings as S


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    request_id: Optional[str] = None
    blocked_by_budget: bool = False
    error: Optional[str] = None


class BudgetExceeded(RuntimeError):
    """Raised when MetricAI refuses a call because the session cap is spent."""


# --------------------------------------------------------------------------
# Real client
# --------------------------------------------------------------------------
class MetricAIClient:
    def __init__(self) -> None:
        from metricai import MetricAI

        missing = [k for k, v in {
            "METRICAI_API_KEY": S.METRICAI_API_KEY,
            "GEMINI_API_KEY": S.GEMINI_API_KEY,
            "AZURE_OPENAI_API_KEY": S.AZURE_API_KEY,
            "AZURE_OPENAI_ENDPOINT": S.AZURE_ENDPOINT,
        }.items() if not v]
        if missing:
            raise RuntimeError(
                f"Missing env vars: {', '.join(missing)}. "
                "Copy .env.example to .env, or run with --mock."
            )

        kwargs: dict[str, Any] = dict(
            api_key=S.METRICAI_API_KEY,
            mode="byok",
            llm_keys={
                "gemini": S.GEMINI_API_KEY,
                "azure_openai": S.AZURE_API_KEY,
                "azure_openai_endpoint": S.AZURE_ENDPOINT,
                "azure_openai_api_version": S.AZURE_API_VERSION,
            },
            active_providers=("gemini", "azure_openai"),
            default_agent_id=S.AGENT_ID,
            # Metering must never take the product down. Fail open, and let the
            # shadow ledger catch anything MetricAI misses.
            fail_open=True,
            raise_on_error=False,
        )
        if S.METRICAI_BASE_URL:
            kwargs["base_url"] = S.METRICAI_BASE_URL

        self.mc = MetricAI(**kwargs)

    def complete(
        self,
        *,
        tier: str,
        messages: list[dict[str, str]],
        tenant_id: str,
        session_id: str,
        node_id: str,
        budget_cap_inr: float,
        attempt: int,
        max_tokens: int = 400,
        temperature: float = 0.2,
    ) -> LLMResult:
        deployment = S.DEPLOYMENTS[tier]
        provider = S.PROVIDERS[tier]

        # Idempotency key: a retried *network* call must not double-bill, but a
        # genuine second revision attempt must, so `attempt` is part of the key.
        idem = hashlib.sha256(
            f"{session_id}:{node_id}:{attempt}:{deployment}".encode()
        ).hexdigest()[:32]

        attribution = dict(
            agent_id=S.AGENT_ID,
            user_id=tenant_id,
            session_id=session_id,
            graph_id=S.GRAPH_ID,
            node_id=node_id,
            budget_cap_inr=budget_cap_inr,
            idempotency_key=idem,
        )

        t0 = time.perf_counter()
        try:
            if provider == "gemini":
                result = self._complete_gemini(
                    self.mc.gemini_sdk(**attribution), deployment, messages,
                    max_tokens, temperature,
                )
            else:
                result = self._complete_openai(
                    self.mc.azure_openai_sdk(**attribution), deployment, messages,
                    max_tokens, temperature,
                )
        except Exception as exc:  # noqa: BLE001
            latency = int((time.perf_counter() - t0) * 1000)
            if _looks_like_budget_block(exc):
                raise BudgetExceeded(str(exc)) from exc
            return LLMResult("", 0, 0, latency, error=f"{type(exc).__name__}: {exc}")

        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        return result

    def track_tool(self, *, name: str, tenant_id: str, session_id: str, node_id: str,
                   latency_ms: int, success: bool, result: dict) -> None:
        """Report a non-LLM tool call so it shows up in MetricAI beside the LLM steps."""
        self.mc.track(
            # `type` must be one of the SDK's recognised tool types -- an
            # unrecognised string leaves `execution_type` unset and the event
            # never reaches Tool Intelligence, so the Tools tab stays empty.
            tools=[{"name": name, "type": "function_call", "invoked": True,
                    "provider": S.AGENT_ID}],
            agent_id=S.AGENT_ID,
            user_id=tenant_id,
            session_id=session_id,
            latency_ms=latency_ms,
            success=success,
            input_tokens=0,
            output_tokens=0,
            cost_inr=0.0,
            extra={"node_id": node_id, "graph_id": S.GRAPH_ID, "result": result},
        )

    @staticmethod
    def _complete_openai(client, deployment, messages, max_tokens, temperature) -> LLMResult:
        resp = client.chat.completions.create(
            model=deployment,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        usage = getattr(resp, "usage", None)
        return LLMResult(
            text=(resp.choices[0].message.content or "").strip(),
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=0,
            request_id=getattr(resp, "id", None),
        )

    @staticmethod
    def _complete_gemini(client, model, messages, max_tokens, temperature) -> LLMResult:
        """Same chat-style messages, translated to the google-genai shape.

        OpenAI `system` -> Gemini `system_instruction`; `assistant` -> `model`.
        """
        from google.genai import types

        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part.from_text(text=m["content"])],
            )
            for m in messages if m["role"] != "system"
        ]
        resp = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction="\n\n".join(system_parts) or None,
                max_output_tokens=max_tokens,
                temperature=temperature,
                # Gemini 3.x "thinks" by default and those hidden tokens count
                # against max_output_tokens. Cheap-tier nodes want a short,
                # structured answer, not deliberation -- keep thinking minimal
                # or a 60-token triage call returns nothing but MAX_TOKENS.
                thinking_config=types.ThinkingConfig(thinking_level="MINIMAL"),
            ),
        )
        usage = getattr(resp, "usage_metadata", None)
        return LLMResult(
            text=(resp.text or "").strip(),
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            # Gemini reports "thinking" tokens separately; they are billed as
            # output, so fold them in to keep the shadow ledger honest.
            output_tokens=(getattr(usage, "candidates_token_count", 0) or 0)
                          + (getattr(usage, "thoughts_token_count", 0) or 0),
            latency_ms=0,
            request_id=getattr(resp, "response_id", None),
        )


def _looks_like_budget_block(exc: Exception) -> bool:
    """MetricAI signals a spent cap with 402/429 or a quota exception."""
    name = type(exc).__name__
    if "Quota" in name or "Budget" in name:
        return True
    status = getattr(exc, "status_code", None)
    if status in (402, 429):
        return True
    return "budget" in str(exc).lower() or "quota" in str(exc).lower()


# --------------------------------------------------------------------------
# Mock client -- lets the whole suite run (and be tested) without live keys
# --------------------------------------------------------------------------
class MockClient:
    """Deterministic stand-in. Same interface, plausible token counts.

    Also simulates a budget cap locally so the runaway scenario is meaningful
    even before you point it at real MetricAI.
    """

    def __init__(self) -> None:
        self._spent: dict[str, float] = {}

    def complete(self, *, tier, messages, tenant_id, session_id, node_id,
                 budget_cap_inr, attempt, max_tokens=400, temperature=0.2) -> LLMResult:
        prompt_chars = sum(len(m["content"]) for m in messages)
        in_tok = max(40, prompt_chars // 4)
        out_tok = {"triage": 35, "draft": 260, "critique": 60, "summarise": 45}.get(node_id, 80)
        if tier == "smart":
            out_tok = int(out_tok * 1.25)

        spent = self._spent.get(session_id, 0.0)
        this = S.cost_inr(tier, in_tok, out_tok)
        if spent + this > budget_cap_inr:
            raise BudgetExceeded(
                f"[mock] session {session_id} cap INR {budget_cap_inr:.2f} would be "
                f"exceeded (spent {spent:.4f} + {this:.4f})"
            )
        self._spent[session_id] = spent + this

        text = _mock_text(node_id, attempt, messages)
        return LLMResult(text, in_tok, out_tok, latency_ms=120 + 40 * (tier == "smart"),
                         request_id=f"mock-{session_id}-{node_id}-{attempt}")

    def track_tool(self, **kwargs) -> None:
        """No-op: nothing to meter in mock mode."""


def _mock_text(node_id: str, attempt: int, messages) -> str:
    if node_id == "triage":
        text = " ".join(m["content"] for m in messages if m["role"] == "user").lower()
        if re.search(r"\b(outage|down|incident|unreachable)\b", text):
            return '{"category": "outage", "urgency": "high"}'
        return '{"category": "billing", "urgency": "high"}'
    if node_id == "critique":
        # First drafts often miss the bar -> produces real, observable retries.
        return '{"score": 3, "reason": "Does not cite the refund window."}' if attempt == 1 \
            else '{"score": 5, "reason": "Accurate and complete."}'
    if node_id == "summarise":
        return "Customer billing query resolved; refund policy cited."
    return f"[draft attempt {attempt}] Thanks for reaching out -- here is what I found..."


def get_client(mock: bool = False):
    return MockClient() if mock else MetricAIClient()
