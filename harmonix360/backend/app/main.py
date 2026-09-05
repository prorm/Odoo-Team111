from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.exceptions import ConflictError, conflict_error_handler
from app.middleware.idempotency import IdempotencyMiddleware
from app.core.database import engine
from app.core.telemetry import setup_telemetry, FastAPIInstrumentor

# 1. Initialize Sentry & OpenTelemetry tracer BEFORE app initialization
setup_telemetry(engine=engine)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# 2. Instrument FastAPI app instance
if FastAPIInstrumentor:
    FastAPIInstrumentor.instrument_app(app)

from app.api.v1.routers import (
    ai,
    attendance,
    auth,
    contracts,
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
app.include_router(employees.router, prefix=settings.API_V1_STR)
app.include_router(contracts.router, prefix=settings.API_V1_STR)
app.include_router(schedules.router, prefix=settings.API_V1_STR)
app.include_router(attendance.router, prefix=settings.API_V1_STR)
app.include_router(time_off.router, prefix=settings.API_V1_STR)
app.include_router(salary.router, prefix=settings.API_V1_STR)
app.include_router(payroll.router, prefix=settings.API_V1_STR)

# Platform/Intelligence layer — mounted but dormant until Phases 8-10.
# /sync registers zero entity types (app/services/sync_entities.py) and /ai has
# no HR prompt families wired to it yet.
app.include_router(sync.router, prefix=settings.API_V1_STR)
app.include_router(ai.router, prefix=settings.API_V1_STR)

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

