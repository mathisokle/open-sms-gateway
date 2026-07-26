"""Admin acceptance tests: API token management (one-time plaintext display!)."""

import hashlib
import re

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from gateway.shared.models import ApiToken
from tests.conftest import admin_login, auth_header, create_token

TOKEN_RE = re.compile(r"sms_[0-9a-f]{32}")


def test_token_creation_shows_plaintext_exactly_once(api: TestClient) -> None:
    admin_login(api)

    response = api.post("/admin/tokens", data={"label": "prod"})

    assert response.status_code == 200
    match = TOKEN_RE.search(response.text)
    assert match, "plaintext token must appear in the creation response"
    plaintext = match.group(0)

    with Session(api.app.state.engine) as session:
        row = session.query(ApiToken).one()
        assert row.token_hash == hashlib.sha256(plaintext.encode()).hexdigest()
        assert row.token_prefix == plaintext[:8]
        assert row.label == "prod"

    # plaintext must never appear again — the token list shows only the prefix
    overview = api.get("/admin/tokens")
    assert plaintext not in overview.text
    assert plaintext[:8] in overview.text

    # the created token authenticates against the API
    assert api.get("/api/v1/messages", headers=auth_header(plaintext)).status_code == 200


def test_revoke_token_blocks_api_access(api: TestClient) -> None:
    token = create_token(api)
    admin_login(api)
    with Session(api.app.state.engine) as session:
        token_id = session.query(ApiToken).one().id

    response = api.post(f"/admin/tokens/{token_id}/revoke", follow_redirects=False)

    assert response.status_code == 303
    with Session(api.app.state.engine) as session:
        assert session.get(ApiToken, token_id).revoked_at is not None
    assert api.get("/api/v1/messages", headers=auth_header(token)).status_code == 401


def test_revoke_unknown_token_is_404(api: TestClient) -> None:
    admin_login(api)

    response = api.post("/admin/tokens/tok_missing/revoke", follow_redirects=False)

    assert response.status_code == 404


def test_revoked_token_can_be_deleted(api: TestClient) -> None:
    create_token(api, revoked=True)
    admin_login(api)
    with Session(api.app.state.engine) as session:
        token_id = session.query(ApiToken).one().id

    response = api.post(f"/admin/tokens/{token_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    with Session(api.app.state.engine) as session:
        assert session.get(ApiToken, token_id) is None


def test_active_token_cannot_be_deleted(api: TestClient) -> None:
    create_token(api)
    admin_login(api)
    with Session(api.app.state.engine) as session:
        token_id = session.query(ApiToken).one().id

    response = api.post(f"/admin/tokens/{token_id}/delete", follow_redirects=False)

    assert response.status_code == 422
    with Session(api.app.state.engine) as session:
        assert session.get(ApiToken, token_id) is not None


def test_double_revoke_preserves_original_timestamp(api: TestClient) -> None:
    admin_login(api)
    with Session(api.app.state.engine) as session:
        row = session.query(ApiToken).first()
        if row is None:
            create_token(api)
    token_row_id = None
    with Session(api.app.state.engine) as session:
        create_token(api)
        row = session.query(ApiToken).first()
        token_row_id = row.id

    api.post(f"/admin/tokens/{token_row_id}/revoke", follow_redirects=False)
    with Session(api.app.state.engine) as session:
        first = session.get(ApiToken, token_row_id).revoked_at
    api.post(f"/admin/tokens/{token_row_id}/revoke", follow_redirects=False)
    with Session(api.app.state.engine) as session:
        second = session.get(ApiToken, token_row_id).revoked_at

    assert first is not None
    assert second == first  # second revoke must not overwrite the audit timestamp
