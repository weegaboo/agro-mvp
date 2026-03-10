"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator

from planner.service import AgroPlannerService, PlannerService
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import User
from .services.auth import decode_access_token

_planner_service: PlannerService = AgroPlannerService()
_auth_scheme = HTTPBearer(auto_error=True)


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


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_auth_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Return current authenticated user from bearer JWT."""
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user
