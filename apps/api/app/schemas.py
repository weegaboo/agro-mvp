"""Request/response schemas for planner API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BuildRouteRequest(BaseModel):
    """Planner build route request."""

    project_path: str = Field(..., min_length=1, description="Path to project JSON file.")


class BuildRouteResponse(BaseModel):
    """Planner build route response."""

    route: dict[str, Any]
    logs: list[str]
