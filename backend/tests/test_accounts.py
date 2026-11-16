from conftest import TEST_PASSWORD, login
from fastapi.testclient import TestClient

from app.models import Account


def create(admin, username="analyst01", role="analyst"):
    return admin.post(
        "/api/v1/accounts", json={"username": username, "name": "组员", "role_id": role, "password": TEST_PASSWORD}
    )


def test_auth_required_and_password_never_returned(client, admin, app):
    with TestClient(app) as guest:
        assert guest.get("/api/v1/accounts").status_code == 401
    result = create(admin)
    assert result.status_code == 201
    assert "password" not in str(result.json())
    assert create(admin).status_code == 409
    with app.state.db.session() as db:
        account = db.get(Account, "analyst01")
        assert account.password_hash.startswith("$argon2")
        assert TEST_PASSWORD not in account.password_hash


def test_csrf_and_origin_are_enforced(admin):
    body = {"type": "问题反馈", "content": "check"}
    csrf = admin.headers.pop("X-CSRF-Token")
    assert admin.post("/api/v1/messages", json=body).status_code == 403
    admin.headers["X-CSRF-Token"] = csrf
    assert admin.post("/api/v1/messages", json=body, headers={"Origin": "https://untrusted.invalid"}).status_code == 403
    assert admin.post("/api/v1/messages", json=body).status_code == 201


def test_disabled_and_password_reset_revoke_existing_sessions(admin, app):
    create(admin)
    with TestClient(app) as analyst:
        login(analyst, "analyst01")
        assert analyst.get("/api/v1/accounts").status_code == 403
        changed = admin.patch(
            "/api/v1/accounts/analyst01", json={"name": "组员", "role_id": "analyst", "enabled": False}
        )
        assert changed.status_code == 200
        assert analyst.get("/api/v1/auth/me").status_code == 401
        admin.patch("/api/v1/accounts/analyst01", json={"name": "组员", "role_id": "analyst", "enabled": True})
        login(analyst, "analyst01")
        assert (
            admin.post("/api/v1/accounts/analyst01/password", json={"password": "new-password-1234"}).status_code == 200
        )
        assert analyst.get("/api/v1/auth/me").status_code == 401
        login(analyst, "analyst01", "new-password-1234")


def test_no_self_lockout_and_live_role_permissions(admin, app):
    assert (
        admin.patch("/api/v1/accounts/admin", json={"name": "管理员", "role_id": "viewer", "enabled": True}).status_code
        == 409
    )
    assert admin.patch("/api/v1/roles/viewer", json={"permissions": ["数据概览", "账号管理"]}).status_code == 409
    assert admin.patch("/api/v1/roles/viewer", json={"permissions": []}).status_code == 409
    create(admin, "viewer01", "viewer")
    with TestClient(app) as viewer:
        login(viewer, "viewer01")
        assert viewer.post("/api/v1/datasets/import", files={"file": ("a.csv", b"x")}).status_code == 403
        assert admin.patch("/api/v1/roles/viewer", json={"permissions": ["数据概览"]}).status_code == 200
        assert viewer.get("/api/v1/models/active").status_code == 403


def test_profile_and_messages_survive_new_connection(admin, app):
    assert (
        admin.patch(
            "/api/v1/auth/me",
            json={"name": "组长", "email": "lead@example.com", "team": "分析组", "note": "integration"},
        ).status_code
        == 200
    )
    message = admin.post("/api/v1/messages", json={"type": "项目待办", "content": "接入真实数据"}).json()
    with TestClient(app) as second:
        login(second)
        assert second.get("/api/v1/auth/me").json()["account"]["name"] == "组长"
        assert second.get("/api/v1/messages").json()["items"][0]["id"] == message["id"]
    assert admin.patch(f"/api/v1/messages/{message['id']}", json={"done": True}).json()["done"] is True
    assert admin.get("/api/v1/logs?page_size=1").json()["total"] >= 3


def test_login_limit_errors_and_pagination(admin, client):
    assert admin.get("/api/v1/accounts?page_size=101").status_code == 422
    assert admin.get("/api/v1/logs?page=0").status_code == 422
    response = admin.post("/api/v1/accounts", json={"username": "bad", "name": " ", "password": "x"})
    assert response.status_code == 422
    assert TEST_PASSWORD not in response.text
    for _ in range(9):
        response = client.post("/api/v1/auth/login", json={"username": "missing", "password": "wrong"})
    assert response.status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "missing", "password": "wrong"}).status_code == 429


def test_messages_cannot_be_modified_by_other_ordinary_accounts(admin, app):
    create(admin, "member01")
    message = admin.post("/api/v1/messages", json={"type": "数据说明", "content": "保留真实事件"}).json()
    with TestClient(app) as member:
        login(member, "member01")
        assert member.patch(f"/api/v1/messages/{message['id']}", json={"done": True}).status_code == 403


def test_password_space_preserved_and_change_logs_out(admin, app):
    create(admin)
    assert (
        admin.post("/api/v1/accounts/analyst01/password", json={"password": " password-with-spaces "}).status_code
        == 200
    )
    with TestClient(app) as member:
        login(member, "analyst01", " password-with-spaces ")
        response = member.post(
            "/api/v1/auth/password",
            json={"current_password": " password-with-spaces ", "password": "another-valid-password"},
        )
        assert response.status_code == 200
        assert member.get("/api/v1/auth/me").status_code == 401
