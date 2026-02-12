from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.deps import get_db, get_planner_service
from app.main import app


class _OkPlanner:
    def build_route_from_project(self, project_path: str, log_fn=None):  # noqa: ANN001
        if log_fn:
            log_fn("planner ok")
        return {"metrics": {"length_total_m": 10.0}}


def _test_db_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def test_create_mission_list_and_get() -> None:
    db = _test_db_session()

    def override_get_db():  # noqa: ANN202
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_planner_service] = lambda: _OkPlanner()

    client = TestClient(app)
    try:
        auth = client.post("/auth/register", json={"login": "user1", "password": "secret12"})
        assert auth.status_code == 200
        token = auth.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        create_response = client.post(
            "/missions",
            files={
                "file": (
                    "project.json",
                    b'{"geoms":{"field":{"type":"Polygon","coordinates":[]}}}',
                    "application/json",
                )
            },
            headers=headers,
        )
        assert create_response.status_code == 200
        created = create_response.json()
        assert created["status"] == "success"
        assert created["result_json"]["route"]["metrics"]["length_total_m"] == 10.0
        mission_id = created["id"]

        list_response = client.get("/missions", headers=headers)
        assert list_response.status_code == 200
        items = list_response.json()
        assert len(items) == 1
        assert items[0]["id"] == mission_id

        get_response = client.get(f"/missions/{mission_id}", headers=headers)
        assert get_response.status_code == 200
        detail = get_response.json()
        assert detail["id"] == mission_id
        assert detail["status"] == "success"
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_create_mission_invalid_json() -> None:
    db = _test_db_session()

    def override_get_db():  # noqa: ANN202
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_planner_service] = lambda: _OkPlanner()

    client = TestClient(app)
    try:
        auth = client.post("/auth/register", json={"login": "user2", "password": "secret12"})
        assert auth.status_code == 200
        token = auth.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        response = client.post(
            "/missions",
            files={"file": ("project.json", b"{not-json}", "application/json")},
            headers=headers,
        )
        assert response.status_code == 400
        assert "Invalid JSON payload" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_missions_require_auth() -> None:
    db = _test_db_session()

    def override_get_db():  # noqa: ANN202
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_planner_service] = lambda: _OkPlanner()
    client = TestClient(app)
    try:
        response = client.get("/missions")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_create_mission_from_geo() -> None:
    db = _test_db_session()

    def override_get_db():  # noqa: ANN202
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_planner_service] = lambda: _OkPlanner()
    client = TestClient(app)
    try:
        auth = client.post("/auth/register", json={"login": "user3", "password": "secret12"})
        assert auth.status_code == 200
        token = auth.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        payload = {
            "geoms": {
                "field": {"type": "Polygon", "coordinates": [[[37.6, 55.7], [37.61, 55.7], [37.61, 55.71], [37.6, 55.71], [37.6, 55.7]]]},
                "runway_centerline": {"type": "LineString", "coordinates": [[37.59, 55.7], [37.595, 55.705]]},
                "nfz": [],
            },
            "aircraft": {"spray_width_m": 20},
        }

        response = client.post("/missions/from-geo", json=payload, headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["result_json"]["route"]["metrics"]["length_total_m"] == 10.0
    finally:
        app.dependency_overrides.clear()
        db.close()
