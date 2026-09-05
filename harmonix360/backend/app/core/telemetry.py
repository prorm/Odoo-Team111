"""
OpenTelemetry & Sentry Observability Configuration for Harmonix360.

Architecture ref: Section 7 — Observability
1. OpenTelemetry auto-instrumentation for FastAPI, SQLAlchemy, Redis, and HTTPX.
2. OTLP Exporter targeting SigNoz.
3. Sentry SDK integration for exception tracking with breadcrumbs.
4. Custom spans for AI provider router calls & resilience failures.
"""
import logging
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
except ImportError:
    FastAPIInstrumentor = None

try:
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
except ImportError:
    SQLAlchemyInstrumentor = None

try:
    from opentelemetry.instrumentation.redis import RedisInstrumentor
except ImportError:
    RedisInstrumentor = None

try:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
except ImportError:
    HTTPXClientInstrumentor = None

from app.core.config import settings

logger = logging.getLogger("harmonix360.telemetry")

_tracer_provider: TracerProvider | None = None

def setup_telemetry(app=None, engine=None):
    """
    Initialize OpenTelemetry Tracing + Sentry SDK.
    Instrument FastAPI, SQLAlchemy, Redis, and HTTPX.
    """
    global _tracer_provider

    # 1. Initialize Sentry if DSN configured
    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            integrations=[FastApiIntegration()],
            traces_sample_rate=1.0,
            profiles_sample_rate=1.0,
            send_default_pii=True,
            debug=True,
            environment=settings.ENVIRONMENT,
        )
        logger.info("Sentry SDK initialized with DSN.")

    # 2. Setup OpenTelemetry Resource & TracerProvider
    resource = Resource.create(attributes={SERVICE_NAME: "harmonix360-backend"})
    _tracer_provider = TracerProvider(resource=resource)

    # OTLP Exporter (HTTP endpoint for SigNoz collector)
    endpoint = f"{settings.OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces"
    otlp_exporter = OTLPSpanExporter(endpoint=endpoint)
    _tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    # Also log spans to console in dev mode
    _tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(_tracer_provider)
    logger.info("OpenTelemetry TracerProvider initialized targeting %s", endpoint)

    # 3. Auto-instrumentation
    if app and FastAPIInstrumentor:
        FastAPIInstrumentor.instrument_app(app, tracer_provider=_tracer_provider)
        logger.info("OpenTelemetry FastAPI instrumentation active.")

    if engine and SQLAlchemyInstrumentor:
        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=_tracer_provider)
        logger.info("OpenTelemetry SQLAlchemy instrumentation active.")

    if RedisInstrumentor:
        RedisInstrumentor().instrument(tracer_provider=_tracer_provider)
        logger.info("OpenTelemetry Redis instrumentation active.")

    if HTTPXClientInstrumentor:
        HTTPXClientInstrumentor().instrument(tracer_provider=_tracer_provider)
        logger.info("OpenTelemetry HTTPX instrumentation active.")

def get_tracer(name: str = "harmonix360.tracer"):
    """Get an OpenTelemetry tracer for custom span instrumentation."""
    return trace.get_tracer(name)
