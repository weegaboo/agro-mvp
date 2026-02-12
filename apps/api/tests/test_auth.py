from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.deps import get_db
from app.main import app


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


def test_register_and_login() -> None:
    db = _test_db_session()

    def override_get_db():  # noqa: ANN202
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        register = client.post("/auth/register", json={"login": "alice", "password": "secret12"})
        assert register.status_code == 200
        assert register.json()["access_token"]

        duplicate = client.post("/auth/register", json={"login": "alice", "password": "secret12"})
        assert duplicate.status_code == 409

        login = client.post("/auth/login", json={"login": "alice", "password": "secret12"})
        assert login.status_code == 200
        assert login.json()["token_type"] == "bearer"

        bad_login = client.post("/auth/login", json={"login": "alice", "password": "badpass"})
        assert bad_login.status_code == 401
    finally:
        app.dependency_overrides.clear()
        db.close()
