"""FastAPI dependencies."""

from __future__ import annotations

from planner.service import AgroPlannerService, PlannerService

_planner_service: PlannerService = AgroPlannerService()


def get_planner_service() -> PlannerService:
    """Return planner service singleton."""
    return _planner_service
