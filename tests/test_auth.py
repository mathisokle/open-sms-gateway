"""Bearer-auth acceptance tests (SPEC §3), exercised via GET /api/v1/messages."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from gateway.shared.models import ApiToken
from tests.conftest import auth_header, create_token


def test_valid_token_authenticates(api: TestClient) -> None:
    token = create_token(api)

    response = api.get("/api/v1/messages", headers=auth_header(token))

    assert response.status_code == 200
    assert response.json() == {"data": [], "next_cursor": None}


def test_missing_authorization_header_is_401(api: TestClient) -> None:
    response = api.get("/api/v1/messages")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_non_bearer_scheme_is_401(api: TestClient) -> None:
    response = api.get("/api/v1/messages", headers={"Authorization": "Basic dXNlcjpwdw=="})

    assert response.status_code == 401


def test_unknown_token_is_401(api: TestClient) -> None:
    response = api.get("/api/v1/messages", headers=auth_header("sms_" + "0" * 32))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_revoked_token_is_401(api: TestClient) -> None:
    token = create_token(api, revoked=True)

    response = api.get("/api/v1/messages", headers=auth_header(token))

    assert response.status_code == 401


def test_token_use_updates_last_used_at(api: TestClient) -> None:
    token = create_token(api)

    api.get("/api/v1/messages", headers=auth_header(token))

    with Session(api.app.state.engine) as session:
        row = session.query(ApiToken).one()
        assert row.last_used_at is not None
        assert row.last_used_at.endswith("Z")


def test_401_carries_www_authenticate_header(api: TestClient) -> None:
    response = api.get("/api/v1/messages", headers={"Authorization": "Bearer nope"})

    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_bearer_with_extra_whitespace_still_authenticates(api: TestClient) -> None:
    token = create_token(api)

    response = api.get("/api/v1/messages", headers={"Authorization": f"Bearer  {token}"})

    assert response.status_code == 200
