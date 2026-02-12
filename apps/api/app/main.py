"""FastAPI entrypoint."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from planner.service import PlannerService

from .deps import get_planner_service
from .schemas import BuildRouteRequest, BuildRouteResponse

app = FastAPI(title="Agro API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness endpoint."""
    return {"status": "ok"}


@app.post("/planner/build-from-project", response_model=BuildRouteResponse)
def build_from_project(
    payload: BuildRouteRequest,
    planner: PlannerService = Depends(get_planner_service),
) -> BuildRouteResponse:
    """Build route from saved project file using planner package."""
    logs: list[str] = []

    try:
        route = planner.build_route_from_project(payload.project_path, log_fn=logs.append)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Planner error: {exc}") from exc

    return BuildRouteResponse(route=route, logs=logs)
