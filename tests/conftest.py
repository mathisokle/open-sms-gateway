"""Shared fixtures/helpers for API tests: app on tmp SQLite, tokens, webhook config.

Token hashes are computed with hashlib directly (not via gateway.api.auth) so the
tests pin the SPEC contract "stored hash = SHA-256 of the plaintext token".
"""

import hashlib
import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from gateway.api.main import create_app
from gateway.shared import ids
from gateway.shared.clock import utc_now_iso
from gateway.shared.config import Settings
from gateway.shared.configstore import WEBHOOK_SECRET, WEBHOOK_URL, set_config
from gateway.shared.models import ApiToken, Message

ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin-test-pw"
TEST_WEBHOOK_SECRET = "whsec_test"


@pytest.fixture()
def api(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        database_path=str(tmp_path / "api.db"),
        admin_user=ADMIN_USER,
        admin_password=ADMIN_PASSWORD,
        secret_key="test-secret-key-for-sessions",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def admin_login(client: TestClient) -> None:
    response = client.post(
        "/admin/login",
        data={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303, "admin login helper expects a successful login redirect"


def create_token(client: TestClient, *, revoked: bool = False, label: str = "test") -> str:
    """Insert an api_tokens row and return the plaintext token."""
    token = f"sms_{secrets.token_hex(16)}"
    with Session(client.app.state.engine) as session:
        session.add(
            ApiToken(
                id=ids.token_id(),
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                token_prefix=token[:8],
                label=label,
                revoked_at=utc_now_iso() if revoked else None,
                created_at=utc_now_iso(),
            )
        )
        session.commit()
    return token


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def set_webhook(client: TestClient, url: str | None, secret: str | None = TEST_WEBHOOK_SECRET) -> None:
    """Configure (or clear) the webhook target in gateway_config."""
    with Session(client.app.state.engine) as session:
        set_config(session, WEBHOOK_URL, url)
        set_config(session, WEBHOOK_SECRET, secret if url else None)
        session.commit()


def insert_message(
    client: TestClient,
    *,
    direction: str = "outbound",
    msisdn: str = "+41791234567",
    body: str = "hello",
    status: str = "queued",
    created_at: str | None = None,
    sent_at: str | None = None,
    received_at: str | None = None,
) -> str:
    message = Message(
        id=ids.message_id(),
        direction=direction,
        msisdn=msisdn,
        body=body,
        status=status,
        created_at=created_at or utc_now_iso(),
        sent_at=sent_at,
        received_at=received_at,
    )
    with Session(client.app.state.engine) as session:
        session.add(message)
        session.commit()
        return message.id
