"""Planner service interface and adapter implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, Optional


class PlannerService(ABC):
    """Abstract planner interface for route generation workflows."""

    @abstractmethod
    def build_route_from_project(
        self,
        project_path: str | Path,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Build route payload from a saved project file.

        Args:
            project_path: Path to JSON project payload.
            log_fn: Optional callback used for progress logging.

        Returns:
            Route response payload.
        """


class AgroPlannerService(PlannerService):
    """Adapter over current agro route building logic."""

    def build_route_from_project(
        self,
        project_path: str | Path,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        # Lazy import avoids loading heavy native deps (OMPL/F2C) during API startup/tests.
        from agro.services.mission_builder import build_route_from_file

        return build_route_from_file(str(project_path), log_fn=log_fn)
