from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import RequestResponseEndpoint

from app.core.config import settings
from app.core.rate_limit import FixedWindowRateLimiter
from app.routers.auth import router as auth_router
from app.routers.deployments import router as deployment_router
from app.routers.health import router as health_router
from app.routers.projects import router as project_router

app = FastAPI(title="MiniCloud API", version="0.1.0")
logger = logging.getLogger(__name__)
auth_rate_limiter = FixedWindowRateLimiter(
    settings.auth_rate_limit_max_requests,
    settings.auth_rate_limit_window_seconds,
)


def run_database_migrations() -> None:
    if settings.service_role != "migrate":
        raise RuntimeError("Database migrations require SERVICE_ROLE=migrate")
    url = settings.migration_database_url
    if not url:
        raise RuntimeError("MIGRATION_DATABASE_URL is required to run migrations")
    alembic_path = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(alembic_path / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(alembic_path / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(alembic_cfg, "head")


origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.exception_handler(RequestValidationError)
async def sanitized_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    errors = [
        {
            "loc": error.get("loc", []),
            "msg": "Invalid request.",
            "type": error.get("type", "value_error"),
        }
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(SQLAlchemyError)
async def sanitized_database_error(
    request: Request,
    exc: SQLAlchemyError,
) -> JSONResponse:
    logger.error(
        "database_request_failed request_id=%s error_type=%s",
        request.state.request_id,
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=503,
        content={"detail": "Database operation unavailable."},
    )


@app.exception_handler(Exception)
async def sanitized_unexpected_error(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    request_id = request.state.request_id
    logger.error(
        "http_request_failed request_id=%s error_type=%s",
        request_id,
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
        headers={"X-Request-ID": request_id},
    )


@app.middleware("http")
async def rate_limit_authentication(
    request: Request,
    call_next: RequestResponseEndpoint,
) -> Response:
    path = request.url.path.rstrip("/")
    if request.method == "POST" and path in {"/api/v1/auth/login", "/api/v1/auth/register"}:
        client_host = request.client.host if request.client else "unknown"
        retry_after = auth_rate_limiter.consume(f"{client_host}:{path}")
        if retry_after is not None:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many authentication attempts; retry later."},
                headers={"Retry-After": str(retry_after)},
            )
    return await call_next(request)


@app.middleware("http")
async def correlate_request(
    request: Request,
    call_next: RequestResponseEndpoint,
) -> Response:
    request_id = str(uuid4())
    request.state.request_id = request_id
    started_at = perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "http_request_completed request_id=%s method=%s path=%s "
        "status_code=%d duration_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        (perf_counter() - started_at) * 1000,
    )
    return response


app.include_router(health_router)
app.include_router(auth_router)
app.include_router(project_router)
app.include_router(deployment_router)


@app.get("/")
def root() -> dict[str, Any]:
    return {"name": settings.app_name, "status": "running", "api": "/api/v1"}
