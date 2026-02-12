"""FastAPI entrypoint."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
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
    temp_path: str | None = None

    try:
        content = await file.read()
        with NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            temp_path = tmp.name
        route = planner.build_route_from_project(temp_path, log_fn=logs.append)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Planner error: {exc}") from exc
    finally:
        await file.close()
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)

    return BuildRouteResponse(route=route, logs=logs)
