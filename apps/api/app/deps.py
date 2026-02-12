"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator

from planner.service import AgroPlannerService, PlannerService
from sqlalchemy.orm import Session

from .db import SessionLocal

_planner_service: PlannerService = AgroPlannerService()


def get_planner_service() -> PlannerService:
    """Return planner service singleton."""
    return _planner_service


def get_db() -> Generator[Session, None, None]:
    """Provide SQLAlchemy session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
