"""Request/response schemas for planner API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BuildRouteRequest(BaseModel):
    """Planner build route request."""

    project_path: str = Field(..., min_length=1, description="Path to project JSON file.")


class BuildRouteResponse(BaseModel):
    """Planner build route response."""

    route: dict[str, Any]
    logs: list[str]


class MissionCreateResponse(BaseModel):
    """Response for mission create endpoint."""

    id: int
    user_id: int | None
    status: str
    input_json: dict[str, Any]
    result_json: dict[str, Any] | None
    created_at: datetime


class MissionListItem(BaseModel):
    """Mission list item."""

    id: int
    user_id: int | None
    status: str
    created_at: datetime


class MissionDetailResponse(BaseModel):
    """Mission detail response."""

    id: int
    user_id: int | None
    status: str
    input_json: dict[str, Any]
    result_json: dict[str, Any] | None
    created_at: datetime
