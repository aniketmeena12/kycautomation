"""
FastAPI application entrypoint.

Startup only creates database tables (Base.metadata.create_all -- additive,
non-destructive, fast) and does NOT read, validate, or ingest any dataset
file. Running the header/schema validation pass is a deliberate, separate,
on-demand step -- see `python -m app.ingestion.validate_all` -- so startup
time never depends on how many sources are registered or how large they are.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import (
    alerts,
    cases,
    customers,
    datasets,
    entity_resolution,
    evidence,
    health,
    ingestion,
    investigations,
    monitoring,
    providers,
    risk,
    sources,
)
from app.core.config import get_settings
from app.core.database import init_db

settings = get_settings()

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("continuous_kyc")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database initialized (schema only -- no dataset ingestion at startup).")
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Continuous KYC Autonomous Auditor -- ingestion, normalization, and Customer 360 "
            "data layer (Phase 2). AI/agents detect and investigate; deterministic logic scores "
            "and enforces workflow; humans make the final compliance decision."
        ),
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(sources.router, prefix=settings.api_v1_prefix)
    app.include_router(providers.router, prefix=settings.api_v1_prefix)
    app.include_router(ingestion.router, prefix=settings.api_v1_prefix)
    app.include_router(customers.router, prefix=settings.api_v1_prefix)
    app.include_router(datasets.router, prefix=settings.api_v1_prefix)
    app.include_router(entity_resolution.router, prefix=settings.api_v1_prefix)
    app.include_router(evidence.router, prefix=settings.api_v1_prefix)
    app.include_router(monitoring.router, prefix=settings.api_v1_prefix)
    app.include_router(risk.router, prefix=settings.api_v1_prefix)
    app.include_router(alerts.router, prefix=settings.api_v1_prefix)
    app.include_router(investigations.router, prefix=settings.api_v1_prefix)
    app.include_router(cases.router, prefix=settings.api_v1_prefix)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never leak stack traces or internal detail to a client. Full detail
        # goes to the server log only.
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})

    return app


app = create_app()
