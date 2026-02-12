from __future__ import annotations

from fastapi.testclient import TestClient

from app.deps import get_planner_service
from app.main import app


class _OkPlanner:
    def build_route_from_project(self, project_path: str, log_fn=None):  # noqa: ANN001
        if log_fn:
            log_fn(f"build {project_path}")
        return {"metrics": {"length_total_m": 123.0}}


class _MissingPlanner:
    def build_route_from_project(self, project_path: str, log_fn=None):  # noqa: ANN001
        raise FileNotFoundError(f"Файл не найден: {project_path}")


client = TestClient(app)


def test_build_route_from_project_ok() -> None:
    app.dependency_overrides[get_planner_service] = lambda: _OkPlanner()
    try:
        response = client.post("/planner/build-from-project", json={"project_path": "/tmp/demo.json"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["route"]["metrics"]["length_total_m"] == 123.0
    assert body["logs"] == ["build /tmp/demo.json"]


def test_build_route_from_project_not_found() -> None:
    app.dependency_overrides[get_planner_service] = lambda: _MissingPlanner()
    try:
        response = client.post("/planner/build-from-project", json={"project_path": "/tmp/missing.json"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert "Файл не найден" in response.json()["detail"]
