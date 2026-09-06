"""
AI Provider Router — single provider (Groq), no fallback.

Architecture ref: §8.1 — provider_router.py
Interface: `async def generate(prompt, context, task_type) -> AIResponse`
1. Check Redis cache first.
2. Try Groq (rate-limited). On failure, raise AIUnavailableError.
# TODO: add Ollama as a second tier once available locally.
   When Ollama is added, append it to the _PROVIDERS list below. The
   ProviderConfig dataclass and the loop already support N providers.
3. On success, cache the response and return.

CEREBRAS WAS REMOVED, NOT DISABLED
-----------------------------------
Cerebras was the original fallback tier. It was cut on 2026-09-06 after
verification showed its account authenticates but every completion returns
`402 Payment Required` — an unfunded account, not a transient outage. It was
never a working fallback in this environment; it was a second failure mode
that made `generate()` look more resilient than it was. Groq alone has an
8000 TPM ceiling and no fallback now — see `app/api/v1/routers/ai.py` for the
single-flight guard and the 429 handling this makes necessary on the caller
side. Re-adding a second provider is fine; re-adding Cerebras specifically
without funding the account is not — it would silently reintroduce exactly
the dead path this removed.

THE PROVIDER IS AN INFERENCE ENGINE, NOT A SOURCE OF TRUTH
----------------------------------------------------------
Nothing this module returns is authoritative. It turns a prompt into prose; the
prompt was built from the deterministic ERP by `app/ai/context_builder.py`, and
every figure in the answer must already appear there. Which provider answered,
whether the answer came from cache, and whether any provider answered at all are
therefore presentation concerns — a payslip is the same payslip either way.

That is what makes `AIUnavailableError` a clean state rather than an outage. It
is raised when every configured provider is missing a key, rate-limited, or
failing, and callers turn it into an explicit "AI unavailable" result that still
carries the ERP facts. Fabricating an answer, or silently degrading to a cached
response for a different question, would be worse than saying nothing.
"""
import json
import logging
import time
from dataclasses import dataclass

from pydantic import BaseModel

from app.core.config import settings
from app.core.redis import redis_client
from app.ai.cache import AIResponseCache

logger = logging.getLogger("harmonix360.ai.provider_router")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class AIResponse(BaseModel):
    """Structured response from any AI provider."""
    text: str
    provider: str
    model: str
    latency_ms: int
    token_count: int
    cached: bool = False


class AIUnavailableError(Exception):
    """Raised when all AI providers fail.

    `reason` is what lets a caller give an honest, specific message instead of
    forwarding a raw provider exception into the UI — "the request volume
    limit was hit, try again shortly" reads very differently from "no
    provider is configured", and a caller that only has `str(exc)` cannot
    tell the two apart without parsing prose.

      not_configured — no provider has an API key set at all.
      rate_limited   — every attempted provider hit our own per-minute cap or
                        the provider's own 429. Transient; retrying later is
                        the right advice.
      provider_error — a provider was called and failed for some other
                        reason (timeout, 5xx, malformed response).
    """

    def __init__(self, message: str, reason: str = "provider_error") -> None:
        super().__init__(message)
        self.reason = reason


# ---------------------------------------------------------------------------
# Per-provider rate limiter (Redis token bucket)
# ---------------------------------------------------------------------------

class ProviderRateLimiter:
    """
    Token-bucket rate limiter parameterized per provider.
    Reuses the Redis pipeline pattern from app/core/rate_limit.py.
    Fail fast to fallback instead of hitting a real 429 from the provider.
    """

    @staticmethod
    async def check_and_increment(provider: str, rpm: int) -> bool:
        """
        Returns True if the request is allowed, False if rate-limited.
        Uses a per-minute sliding window in Redis.
        """
        current_minute = int(time.time() // 60)
        key = f"ai_rate_limit:{provider}:{current_minute}"

        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, 120)  # 2-minute TTL for safety
        results = await pipe.execute()

        request_count = results[0]
        if request_count > rpm:
            logger.warning(
                "Rate limit exceeded for provider %s (%d/%d RPM)",
                provider, request_count, rpm,
            )
            return False
        return True


# ---------------------------------------------------------------------------
# Provider configurations
# ---------------------------------------------------------------------------

@dataclass
class ProviderConfig:
    name: str
    api_key_setting: str  # attribute name on Settings
    model_setting: str
    rpm_setting: str

    @property
    def api_key(self) -> str:
        return getattr(settings, self.api_key_setting)

    @property
    def model(self) -> str:
        return getattr(settings, self.model_setting)

    @property
    def rpm(self) -> int:
        return getattr(settings, self.rpm_setting)


_PROVIDERS = [
    ProviderConfig("groq", "GROQ_API_KEY", "GROQ_MODEL", "GROQ_RPM"),
    # TODO: add Ollama as a second tier once available locally
    # ProviderConfig("ollama", "OLLAMA_API_KEY", "OLLAMA_MODEL", "OLLAMA_RPM"),
]


# ---------------------------------------------------------------------------
# Provider call implementations
# ---------------------------------------------------------------------------

#: The system framing every HR/payroll call carries, separate from the rendered
#: prompt so it survives even a caller that builds its own text. `temperature`
#: is low for the same reason: this layer explains figures it was given, and
#: creative variance in a sentence about someone's pay is not a feature.
_SYSTEM_MESSAGE = (
    "You are the explanation layer of an HR and payroll system. The figures you are "
    "given were calculated by a deterministic engine and are final. Explain them; never "
    "recalculate them, and never state an amount that was not given to you. "
    # Length is a correctness property here, not a style preference: an answer
    # that runs past the token ceiling is cut off mid-sentence, and a payroll
    # explanation whose conclusion is missing is worse than a short one.
    "Answer in under 200 words. Lead with the conclusion — what changed and why — "
    "then the few figures that support it. Do not restate every line of the payslip; "
    "the reader can already see it."
)

#: Prompts contain real payroll data — names, wages, net pay. They are never
#: printed, logged at INFO, or attached to a span. The previous build echoed the
#: full prompt and the full response to stdout, which put every explained
#: payslip into `docker compose logs` and, through the collector, into whatever
#: ingests them. Only sizes and outcomes are recorded here; the content is
#: returned to the caller and goes nowhere else.
#: Headroom over what the system message asks for, so the ceiling is a backstop
#: rather than the thing that ends the answer. At 1024 a verbose model ran out
#: mid-sentence ("...the employee had 3 unpaid leave days in") and the
#: conclusion — the part a payroll officer actually needs — was the part lost.
_MAX_TOKENS = 2048
_TEMPERATURE = 0.3


async def _call_groq(prompt: str, model: str, api_key: str) -> tuple[str, int]:
    """Call Groq API. Returns (response_text, token_count)."""
    from groq import AsyncGroq

    client = AsyncGroq(api_key=api_key, timeout=settings.AI_PROVIDER_TIMEOUT)
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
        ],
        temperature=_TEMPERATURE,
        max_tokens=_MAX_TOKENS,
    )
    text = response.choices[0].message.content or ""
    tokens = response.usage.total_tokens if response.usage else 0
    return text, tokens


_CALL_FUNCTIONS = {
    "groq": _call_groq,
    # TODO: add Ollama call function when available
}


# ---------------------------------------------------------------------------
# Main generate function
# ---------------------------------------------------------------------------

async def generate(prompt: str, context: dict, task_type: str) -> AIResponse:
    """
    Primary AI generation interface per Architecture Section 5.

    1. Check Redis cache.
    2. Try each configured provider in order (Groq only — no fallback).
    3. On success, cache and return.
    4. On all failures, raise AIUnavailableError.
    """
    # Build full prompt with context
    full_prompt = prompt
    if context:
        context_str = json.dumps(context, indent=2, default=str)
        full_prompt = f"{prompt}\n\nContext:\n{context_str}"

    # 1. Check cache
    cached = await AIResponseCache.get(full_prompt, context, task_type)
    if cached:
        return AIResponse(
            text=cached["text"],
            provider=cached["provider"],
            model=cached["model"],
            latency_ms=0,
            token_count=cached.get("token_count", 0),
            cached=True,
        )

    # 2. Try providers in order
    errors: list[str] = []
    # Distinguishes WHY every provider failed, so a caller can give an honest
    # message instead of forwarding a raw exception (see AIUnavailableError).
    any_configured = False
    saw_rate_limit = False

    # Trace 2 of the three Architecture §8.5 allows. Attributes are metadata
    # only — task type, provider, latency, token counts. The prompt and the
    # completion are never attached: they contain payroll.
    from app.core.telemetry import ai_span

    with ai_span("provider_route", task_type=task_type, provider_count=len(_PROVIDERS)):

        for provider_cfg in _PROVIDERS:
            # Skip if no API key configured
            if not provider_cfg.api_key:
                errors.append(f"{provider_cfg.name}: no API key configured")
                logger.info("Skipping %s — no API key", provider_cfg.name)
                continue
            any_configured = True

            # Check rate limit (fail fast to fallback)
            allowed = await ProviderRateLimiter.check_and_increment(
                provider_cfg.name, provider_cfg.rpm
            )
            if not allowed:
                saw_rate_limit = True
                errors.append(f"{provider_cfg.name}: rate limited")
                continue

            call_fn = _CALL_FUNCTIONS.get(provider_cfg.name)
            if not call_fn:
                errors.append(f"{provider_cfg.name}: no call function registered")
                continue

            # Attempt the call
            start = time.time()

            with ai_span(
                "provider_call",
                provider=provider_cfg.name,
                model=provider_cfg.model,
                task_type=task_type,
            ) as span:
                try:
                    text, token_count = await call_fn(
                        full_prompt, provider_cfg.model, provider_cfg.api_key
                    )
                    latency_ms = int((time.time() - start) * 1000)

                    span.set_attribute("latency_ms", latency_ms)
                    span.set_attribute("token_count", token_count)

                    response = AIResponse(
                        text=text,
                        provider=provider_cfg.name,
                        model=provider_cfg.model,
                        latency_ms=latency_ms,
                        token_count=token_count,
                        cached=False,
                    )

                    logger.info(
                        json.dumps({
                            "event": "ai_call_success",
                            "provider": provider_cfg.name,
                            "model": provider_cfg.model,
                            "latency_ms": latency_ms,
                            "token_count": token_count,
                            "cache_hit": False,
                            "task_type": task_type,
                        })
                    )

                    # 3. Cache the response
                    await AIResponseCache.set(
                        full_prompt, context, task_type,
                        {"text": text, "provider": provider_cfg.name, "model": provider_cfg.model, "token_count": token_count},
                    )

                    return response

                except Exception as e:
                    latency_ms = int((time.time() - start) * 1000)
                    error_msg = f"{provider_cfg.name}: {type(e).__name__}: {e}"
                    errors.append(error_msg)

                    # Groq's SDK (and most OpenAI-compatible ones) raise a
                    # `status_code` attribute on API errors — 429 here means
                    # the provider itself rate-limited us, same as our own
                    # ProviderRateLimiter tripping above.
                    if getattr(e, "status_code", None) == 429:
                        saw_rate_limit = True

                    span.set_attribute("latency_ms", latency_ms)
                    span.set_attribute("error.type", type(e).__name__)

                    logger.warning(
                        json.dumps({
                            "event": "ai_call_failed",
                            "provider": provider_cfg.name,
                            "model": provider_cfg.model,
                            "latency_ms": latency_ms,
                            "error": str(e),
                            "task_type": task_type,
                        })
                    )
                    continue
            continue

    # 4. All providers failed
    error_summary = "; ".join(errors)
    logger.error("All AI providers failed: %s", error_summary)
    if not any_configured:
        reason = "not_configured"
    elif saw_rate_limit:
        reason = "rate_limited"
    else:
        reason = "provider_error"
    raise AIUnavailableError(f"All AI providers failed: {error_summary}", reason=reason)
