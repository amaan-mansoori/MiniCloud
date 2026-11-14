from __future__ import annotations

import logging

from app.core.db import get_db
from app.core.security import get_current_user
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "api"}


@router.get("/livez")
def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/api/v1/capabilities", dependencies=[Depends(get_current_user)])
def capabilities() -> dict[str, object]:
    return {
        "api_version": "v1",
        "metrics": {
            "available": False,
            "status": "unavailable",
            "reason": "No metrics provider is configured.",
        },
        "alerts": {
            "available": False,
            "status": "unavailable",
            "reason": "Alert delivery is not implemented.",
        },
    }


@router.get("/readyz", response_model=None)
def readiness(db: Session = Depends(get_db)) -> dict[str, str | bool] | JSONResponse:
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready", "database": True}
    except SQLAlchemyError as exc:
        logger.error("Database readiness check failed (%s).", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": False},
        )
