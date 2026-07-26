"""Admin login and session protection (SPEC §3)."""

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ADMIN_PASSWORD, ADMIN_USER, admin_login

PROTECTED_ROUTES = [
    "/admin",
    "/admin/tokens",
    "/admin/messages",
    "/admin/webhooks",
    "/admin/settings",
    "/admin/backup",
    "/admin/partials/stats",
    "/admin/logs",
    "/admin/users",
    "/admin/chats",
]


@pytest.mark.parametrize("route", PROTECTED_ROUTES)
def test_admin_routes_without_session_redirect_to_login(api: TestClient, route: str) -> None:
    response = api.get(route, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_login_page_is_reachable_without_session(api: TestClient) -> None:
    response = api.get("/admin/login")

    assert response.status_code == 200
    assert "password" in response.text.lower()


def test_login_with_wrong_password_sets_no_cookie(api: TestClient) -> None:
    response = api.post(
        "/admin/login",
        data={"username": ADMIN_USER, "password": "wrong"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()}


def test_login_with_valid_credentials_sets_session_cookie(api: TestClient) -> None:
    response = api.post(
        "/admin/login",
        data={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie or "SameSite=Lax" in set_cookie

    dashboard = api.get("/admin")
    assert dashboard.status_code == 200


def test_tampered_session_cookie_redirects_to_login(api: TestClient) -> None:
    admin_login(api)
    cookie_name = next(iter(api.cookies.keys()))
    api.cookies.set(cookie_name, "tampered-value")

    response = api.get("/admin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_logout_clears_session(api: TestClient) -> None:
    admin_login(api)
    assert api.get("/admin").status_code == 200

    response = api.post("/admin/logout", follow_redirects=False)

    assert response.status_code == 303
    after = api.get("/admin", follow_redirects=False)
    assert after.status_code == 303
    assert after.headers["location"] == "/admin/login"


def test_login_with_non_ascii_credentials_returns_401_not_500(api: TestClient) -> None:
    """secrets.compare_digest raises TypeError on non-ASCII str input — must not 500."""
    response = api.post(
        "/admin/login",
        data={"username": "ädmin", "password": "gehéim🔑"},
        follow_redirects=False,
    )

    assert response.status_code == 401


def test_login_is_rate_limited_after_repeated_attempts(api: TestClient) -> None:
    for _ in range(10):
        response = api.post(
            "/admin/login",
            data={"username": ADMIN_USER, "password": "wrong"},
            follow_redirects=False,
        )
        assert response.status_code == 401

    blocked = api.post(
        "/admin/login",
        data={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )

    assert blocked.status_code == 429


def test_session_cookie_not_secure_by_default(api: TestClient) -> None:
    response = api.post(
        "/admin/login",
        data={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )

    assert "secure" not in response.headers["set-cookie"].lower()


def test_session_cookie_secure_flag_follows_setting(tmp_path) -> None:
    from fastapi.testclient import TestClient as Client

    from gateway.api.main import create_app
    from gateway.shared.config import Settings

    settings = Settings(
        database_path=str(tmp_path / "secure.db"),
        admin_user=ADMIN_USER,
        admin_password=ADMIN_PASSWORD,
        secret_key="test-secret-key-for-sessions",
        session_cookie_secure=True,
    )
    with Client(create_app(settings)) as client:
        response = client.post(
            "/admin/login",
            data={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
            follow_redirects=False,
        )

    assert "secure" in response.headers["set-cookie"].lower()


def test_deleted_db_user_session_is_invalidated(api: TestClient) -> None:
    """Deleting an admin user must immediately kill their session (kill-switch)."""
    from sqlalchemy.orm import Session

    from gateway.api.passwords import hash_password
    from gateway.shared import ids
    from gateway.shared.clock import utc_now_iso
    from gateway.shared.models import AdminUser

    with Session(api.app.state.engine) as session:
        session.add(
            AdminUser(
                id=ids.token_id(),
                username="temp-admin",
                password_hash=hash_password("temp-password-123"),
                created_at=utc_now_iso(),
            )
        )
        session.commit()
    login = api.post(
        "/admin/login",
        data={"username": "temp-admin", "password": "temp-password-123"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert api.get("/admin", follow_redirects=False).status_code == 200

    with Session(api.app.state.engine) as session:
        user = session.query(AdminUser).filter(AdminUser.username == "temp-admin").one()
        session.delete(user)
        session.commit()

    after = api.get("/admin", follow_redirects=False)
    assert after.status_code == 303
    assert after.headers["location"] == "/admin/login"
