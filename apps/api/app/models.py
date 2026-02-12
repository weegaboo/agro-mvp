"""ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class User(Base):
    """Basic user model placeholder for future auth flows."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    login: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)


class Mission(Base):
    """Stored route build mission."""

    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    input_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
