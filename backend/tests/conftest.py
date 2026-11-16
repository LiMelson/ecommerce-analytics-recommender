import pytest
from fastapi.testclient import TestClient

from app.auth import bootstrap
from app.config import Settings
from app.db import Base
from app.main import create_app
from app.models import AppSetting

TEST_PASSWORD = "unit-test-password-2026"


class FakeAnalytics:
    def ping(self):
        return True


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}", instance_dir=tmp_path, allowed_origins=["http://testserver"]
    )
    app = create_app(settings, FakeAnalytics())
    Base.metadata.create_all(app.state.db.engine)
    with app.state.db.session() as db:
        bootstrap(db, TEST_PASSWORD)
        db.add_all([AppSetting(key="active_dataset", value=""), AppSetting(key="active_model", value="")])
        db.commit()
    yield app
    app.state.db.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def login(client, username="admin", password=TEST_PASSWORD):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()


@pytest.fixture
def admin(client):
    login(client)
    return client
