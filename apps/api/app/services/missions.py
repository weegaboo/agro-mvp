"""Mission persistence service."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models import Mission


def create_mission(db: Session, *, input_json: dict[str, Any], user_id: int | None = None) -> Mission:
    """Create mission with running status."""
    mission = Mission(
        user_id=user_id,
        status="running",
        input_json=input_json,
        result_json=None,
    )
    db.add(mission)
    db.commit()
    db.refresh(mission)
    return mission


def mark_mission_success(db: Session, mission: Mission, *, route: dict[str, Any], logs: list[str]) -> Mission:
    """Store successful mission result."""
    mission.status = "success"
    mission.result_json = {"route": route, "logs": logs}
    db.add(mission)
    db.commit()
    db.refresh(mission)
    return mission


def mark_mission_failed(db: Session, mission: Mission, *, error: str) -> Mission:
    """Store failed mission result."""
    mission.status = "failed"
    mission.result_json = {"error": error}
    db.add(mission)
    db.commit()
    db.refresh(mission)
    return mission


def list_missions(db: Session, *, limit: int = 50) -> list[Mission]:
    """List latest missions."""
    stmt: Select[tuple[Mission]] = select(Mission).order_by(Mission.created_at.desc()).limit(limit)
    return list(db.scalars(stmt))


def get_mission_by_id(db: Session, mission_id: int) -> Mission | None:
    """Get mission by id."""
    stmt: Select[tuple[Mission]] = select(Mission).where(Mission.id == mission_id)
    return db.scalars(stmt).first()
