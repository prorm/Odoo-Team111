"""OpenTelemetry and Sentry — exactly three business traces (Architecture §8.5).

    1. PAYROLL COMPUTE   POST /payruns/{id}/compute -> resolve contract ->
                         schedule -> attendance -> leave -> rules -> payslip ->
                         validate -> queue jobs
    2. AI / MCP          request -> tool/service -> DB -> provider -> response
    3. OFFLINE SYNC      client mutation -> validation -> transaction -> audit

AND NOTHING ELSE. The previous build auto-instrumented FastAPI, SQLAlchemy,
Redis and HTTPX, which produces a span for every request, every statement and
every cache read. That is not more observability, it is less: PRD §5.5 asks for
"three specific business traces (not a generic showcase)", and the deliverable
is a payroll manager pointing at one trace and following a single payslip from
contract resolution to the queued PDF job. A span per SELECT buries exactly that.

Concretely, blanket tracing also carries a cost this domain cannot ignore: a
SQLAlchemy span carries the statement, and statements in this application
contain wages. Three hand-placed traces with named, reviewed attributes keep
that surface small enough to reason about.

WHAT NEVER GOES INTO A SPAN
---------------------------
No prompt, no model response, no salary figure, no bank account, no employee
name. `ai_trace` records the task type, the provider, latency, token counts and
an outcome — enough to answer "what did the AI layer do and how long did it
take", and nothing that would put someone's pay into a telemetry backend. The
payroll trace records counts and public ids, never amounts.

TRACING IS OFF BY DEFAULT AND MUST STAY OPTIONAL
------------------------------------------------
`OTEL_ENABLED` gates the exporter. With it off, `get_tracer` returns the API's
no-op tracer, every helper below is a cheap context manager, and nothing tries
to reach a collector — which is what keeps the test suite fast and keeps a core
`docker compose up` from needing the advanced profile.
"""
import logging
from contextlib import contextmanager
from typing import Any

import sentry_sdk
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from sentry_sdk.integrations.fastapi import FastApiIntegration

from app.core.config import settings

logger = logging.getLogger("harmonix360.telemetry")

_tracer_provider: TracerProvider | None = None

#: The only three trace names this application emits. Anything else is a bug in
#: a caller, and `_span` refuses it rather than quietly widening the surface.
TRACE_PAYROLL_COMPUTE = "payroll.compute"
TRACE_AI_MCP = "ai.mcp"
TRACE_OFFLINE_SYNC = "offline.sync"

ALLOWED_TRACES = frozenset({TRACE_PAYROLL_COMPUTE, TRACE_AI_MCP, TRACE_OFFLINE_SYNC})


def setup_telemetry(app=None, engine=None):
    """Initialise Sentry, and the tracer provider when OTEL is enabled.

    `app` and `engine` are accepted and deliberately unused: they were the
    handles for the FastAPI and SQLAlchemy auto-instrumentation this function
    used to install. The parameters stay so the call sites do not have to
    change, and so this comment sits where someone would otherwise re-add it.
    """
    global _tracer_provider

    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            integrations=[FastApiIntegration()],
            traces_sample_rate=1.0,
            profiles_sample_rate=1.0,
            # send_default_pii stays False: this is an HR and payroll system,
            # so "default PII" here means employee names, work emails and
            # request bodies containing salaries, shipped to a third-party
            # error tracker. Sentry needs the stack trace, not the payroll.
            send_default_pii=False,
            debug=settings.SENTRY_DEBUG,
            environment=settings.ENVIRONMENT,
        )
        logger.info("Sentry SDK initialized with DSN.")

    if not settings.OTEL_ENABLED:
        logger.debug("OpenTelemetry disabled (OTEL_ENABLED=false); skipping tracer setup.")
        return

    resource = Resource.create(attributes={SERVICE_NAME: "peoplepay360-backend"})
    _tracer_provider = TracerProvider(resource=resource)
    endpoint = f"{settings.OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces"
    _tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    if settings.OTEL_CONSOLE_EXPORT:
        _tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(_tracer_provider)
    logger.info(
        "OpenTelemetry initialized targeting %s. Three business traces only: %s",
        endpoint,
        ", ".join(sorted(ALLOWED_TRACES)),
    )


def get_tracer(name: str = "harmonix360.tracer"):
    """An OpenTelemetry tracer. A no-op tracer when tracing is not configured."""
    return trace.get_tracer(name)


def _set(span, attributes: dict[str, Any]) -> None:
    for key, value in attributes.items():
        if value is None:
            continue
        span.set_attribute(key, value if isinstance(value, (str, int, float, bool)) else str(value))


@contextmanager
def _span(trace_name: str, step: str, **attributes: Any):
    if trace_name not in ALLOWED_TRACES:  # pragma: no cover - guards a typo
        raise ValueError(
            f"{trace_name!r} is not one of the three traces Architecture §8.5 allows: "
            f"{sorted(ALLOWED_TRACES)}"
        )
    tracer = get_tracer(trace_name)
    with tracer.start_as_current_span(f"{trace_name}.{step}") as span:
        _set(span, attributes)
        try:
            yield span
        except Exception as exc:
            # The exception TYPE and message, not the payload that caused it.
            span.set_attribute("outcome", "error")
            span.set_attribute("error.type", type(exc).__name__)
            span.record_exception(exc)
            raise
        else:
            span.set_attribute("outcome", "ok")


def payroll_span(step: str, **attributes: Any):
    """Trace 1 — a step of a payroll computation.

    Attributes carry counts and public ids. Never an amount: a span attribute
    is exported to a telemetry backend, and Architecture §10's rule about money
    crossing boundaries applies to that boundary too.
    """
    return _span(TRACE_PAYROLL_COMPUTE, step, **attributes)


def ai_span(step: str, **attributes: Any):
    """Trace 2 — a step of an AI or MCP request.

    Enough to understand the lifecycle: which task type, which tool, which
    provider, how long, cached or not, and how it ended. Deliberately no
    prompt, no completion, no API key and no employee data.
    """
    return _span(TRACE_AI_MCP, step, **attributes)


def sync_span(step: str, **attributes: Any):
    """Trace 3 — a step of an offline-sync push or pull."""
    return _span(TRACE_OFFLINE_SYNC, step, **attributes)


def describe_traces() -> dict:
    """What this process would emit, for the observability screen and for tests.

    Exposed so "exactly three traces" is assertable rather than a claim in a
    docstring — a fourth trace name added carelessly would fail the test that
    reads this.
    """
    return {
        "enabled": bool(settings.OTEL_ENABLED),
        "endpoint": settings.OTEL_EXPORTER_OTLP_ENDPOINT if settings.OTEL_ENABLED else None,
        "traces": [
            {
                "name": TRACE_PAYROLL_COMPUTE,
                "description": (
                    "Payrun compute: contract resolution, schedule, attendance, leave, rule "
                    "execution, payslip write, validation, queued jobs."
                ),
            },
            {
                "name": TRACE_AI_MCP,
                "description": (
                    "AI and MCP lifecycle: context assembly, tool invocation, provider call, "
                    "response. No prompts or payroll figures are recorded."
                ),
            },
            {
                "name": TRACE_OFFLINE_SYNC,
                "description": (
                    "Offline sync: client mutation, validation, transaction, audit."
                ),
            },
        ],
        "blanket_instrumentation": False,
        "note": (
            "Architecture §8.5 permits exactly these three. FastAPI, SQLAlchemy, Redis and "
            "HTTPX auto-instrumentation is deliberately NOT installed."
        ),
    }


__all__ = [
    "ALLOWED_TRACES",
    "TRACE_AI_MCP",
    "TRACE_OFFLINE_SYNC",
    "TRACE_PAYROLL_COMPUTE",
    "ai_span",
    "describe_traces",
    "get_tracer",
    "payroll_span",
    "setup_telemetry",
    "sync_span",
]
