"""FastAPI entrypoint."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
import json

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from planner.service import PlannerService

from .deps import get_db, get_planner_service
from .schemas import (
    BuildRouteRequest,
    BuildRouteResponse,
    MissionCreateResponse,
    MissionDetailResponse,
    MissionListItem,
)
from .services.missions import create_mission, get_mission_by_id, list_missions, mark_mission_failed, mark_mission_success

app = FastAPI(title="Agro API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _build_with_temp_file(
    *,
    planner: PlannerService,
    raw_bytes: bytes,
    suffix: str,
    logs: list[str],
) -> dict:
    """Write bytes to temp file and run planner."""
    temp_path: str | None = None
    try:
        with NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as tmp:
            tmp.write(raw_bytes)
            temp_path = tmp.name
        return planner.build_route_from_project(temp_path, log_fn=logs.append)
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)


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


@app.post("/planner/build-from-upload", response_model=BuildRouteResponse)
async def build_from_upload(
    file: UploadFile = File(...),
    planner: PlannerService = Depends(get_planner_service),
) -> BuildRouteResponse:
    """Build route from uploaded project JSON file."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="File name is required")

    suffix = Path(file.filename).suffix or ".json"
    logs: list[str] = []
    try:
        route = _build_with_temp_file(planner=planner, raw_bytes=await file.read(), suffix=suffix, logs=logs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Planner error: {exc}") from exc
    finally:
        await file.close()

    return BuildRouteResponse(route=route, logs=logs)


@app.post("/missions", response_model=MissionCreateResponse)
async def create_mission_from_upload(
    file: UploadFile = File(...),
    user_id: int | None = None,
    planner: PlannerService = Depends(get_planner_service),
    db: Session = Depends(get_db),
) -> MissionCreateResponse:
    """Create mission from uploaded project and store result."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="File name is required")

    try:
        input_payload = json.loads((await file.read()).decode("utf-8"))
    except Exception as exc:
        await file.close()
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

    mission = create_mission(db, input_json=input_payload, user_id=user_id)
    logs: list[str] = []

    try:
        route = _build_with_temp_file(
            planner=planner,
            raw_bytes=json.dumps(input_payload, ensure_ascii=False).encode("utf-8"),
            suffix=Path(file.filename).suffix or ".json",
            logs=logs,
        )
        mission = mark_mission_success(db, mission, route=route, logs=logs)
    except ValueError as exc:
        mission = mark_mission_failed(db, mission, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        mission = mark_mission_failed(db, mission, error=f"Planner error: {exc}")
        raise HTTPException(status_code=500, detail=f"Planner error: {exc}") from exc
    finally:
        await file.close()

    return MissionCreateResponse(
        id=mission.id,
        user_id=mission.user_id,
        status=mission.status,
        input_json=mission.input_json,
        result_json=mission.result_json,
        created_at=mission.created_at,
    )


@app.get("/missions", response_model=list[MissionListItem])
def get_missions(
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[MissionListItem]:
    """List latest missions."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    missions = list_missions(db, limit=limit)
    return [
        MissionListItem(
            id=mission.id,
            user_id=mission.user_id,
            status=mission.status,
            created_at=mission.created_at,
        )
        for mission in missions
    ]


@app.get("/missions/{mission_id}", response_model=MissionDetailResponse)
def get_mission(
    mission_id: int,
    db: Session = Depends(get_db),
) -> MissionDetailResponse:
    """Return mission by id."""
    mission = get_mission_by_id(db, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return MissionDetailResponse(
        id=mission.id,
        user_id=mission.user_id,
        status=mission.status,
        input_json=mission.input_json,
        result_json=mission.result_json,
        created_at=mission.created_at,
    )
