from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.exceptions import ConflictError, conflict_error_handler
from app.middleware.idempotency import IdempotencyMiddleware
from app.core.database import engine
from app.core.telemetry import setup_telemetry

# 1. Initialize Sentry & OpenTelemetry tracer BEFORE app initialization
setup_telemetry(engine=engine)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# 2. NO blanket FastAPI instrumentation. Architecture §8.5 allows exactly three
#    named traces — payroll compute, AI/MCP and offline sync — and explicitly
#    rules out instrumenting every endpoint ("the goal is a demonstrable 'every
#    payroll calculation is traceable' moment, not blanket tracing"). Auto
#    instrumenting the app would bury those three in a span per request and
#    make the payroll trace harder to find, not easier.

from app.api.v1.routers import (
    ai,
    attendance,
    auth,
    contracts,
    dashboard,
    departments,
    employees,
    payroll,
    salary,
    schedules,
    sync,
    time_off,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(IdempotencyMiddleware)

app.add_exception_handler(ConflictError, conflict_error_handler)

app.include_router(auth.router, prefix=settings.API_V1_STR)

# Core HR domain (PS A1-A7 / B1-B9).
app.include_router(departments.router, prefix=settings.API_V1_STR)
app.include_router(employees.router, prefix=settings.API_V1_STR)
app.include_router(contracts.router, prefix=settings.API_V1_STR)
app.include_router(schedules.router, prefix=settings.API_V1_STR)
app.include_router(attendance.router, prefix=settings.API_V1_STR)
app.include_router(time_off.router, prefix=settings.API_V1_STR)
app.include_router(salary.router, prefix=settings.API_V1_STR)
app.include_router(payroll.router, prefix=settings.API_V1_STR)
from app.api.v1.routers import payslip_documents
app.include_router(payslip_documents.router, prefix=settings.API_V1_STR)
app.include_router(dashboard.router, prefix=settings.API_V1_STR)

# Platform/Intelligence layer. /sync registers attendance and time-off creation
# (Phase 8), /ai carries the HR prompt families and the propose-confirm flow
# (Phase 9), and /insights plus the WebSocket channels are Phase 10.
app.include_router(sync.router, prefix=settings.API_V1_STR)
app.include_router(ai.router, prefix=settings.API_V1_STR)

# Phase 10. Every route on `insights` is a GET over already-persisted state
# (Architecture §8.6); `realtime` carries the WebSocket channels, whose frames
# are notifications of committed state and never a source of truth (§8.4).
from app.api.v1.routers import insights, realtime  # noqa: E402
app.include_router(insights.router, prefix=settings.API_V1_STR)
app.include_router(realtime.router, prefix=settings.API_V1_STR)

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "PeoplePay360 Backend", "version": settings.VERSION}

@app.get("/")
async def root():
    return {"message": "Welcome to the PeoplePay360 API", "docs": "/docs"}

@app.get("/sentry-debug")
@app.get("/sentry-debug/")
async def trigger_sentry_debug():
    """Route to verify Sentry installation by triggering a division by zero error."""
    raise ZeroDivisionError("Sentry debug error: division by zero")

