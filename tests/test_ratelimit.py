"""Rate-limit acceptance tests (SPEC §5: 429, default off; single shared window)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway.api.main import create_app
from gateway.api.ratelimit import RateLimiter
from gateway.shared.config import Settings
from tests.conftest import ADMIN_PASSWORD, ADMIN_USER, auth_header, create_token


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# --- unit: sliding window ---


def test_limiter_allows_up_to_per_minute_then_blocks() -> None:
    limiter = RateLimiter(per_minute=2, clock=FakeClock())

    assert limiter.allow("api") is True
    assert limiter.allow("api") is True
    assert limiter.allow("api") is False


def test_limiter_window_slides_after_sixty_seconds() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=1, clock=clock)
    assert limiter.allow("api") is True
    assert limiter.allow("api") is False

    clock.now = 61.0

    assert limiter.allow("api") is True


def test_limiter_zero_means_disabled() -> None:
    limiter = RateLimiter(per_minute=0, clock=FakeClock())

    assert all(limiter.allow("api") for _ in range(100))


# --- API integration ---


@pytest.fixture()
def api_limited(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        database_path=str(tmp_path / "limited.db"),
        admin_user=ADMIN_USER,
        admin_password=ADMIN_PASSWORD,
        secret_key="test-secret-key-for-sessions",
        rate_limit_per_minute=2,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_third_request_within_window_is_429(api_limited: TestClient) -> None:
    token = create_token(api_limited)

    first = api_limited.get("/api/v1/messages", headers=auth_header(token))
    second = api_limited.get("/api/v1/messages", headers=auth_header(token))
    third = api_limited.get("/api/v1/messages", headers=auth_header(token))

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limited"


def test_window_is_shared_across_tokens(api_limited: TestClient) -> None:
    token_a = create_token(api_limited, label="a")
    token_b = create_token(api_limited, label="b")

    api_limited.get("/api/v1/messages", headers=auth_header(token_a))
    api_limited.get("/api/v1/messages", headers=auth_header(token_a))

    # single-tenant gateway: one shared window protects the modem, not per-token quotas
    assert api_limited.get("/api/v1/messages", headers=auth_header(token_b)).status_code == 429


def test_default_configuration_has_no_limit(api: TestClient) -> None:
    token = create_token(api)

    responses = [api.get("/api/v1/messages", headers=auth_header(token)) for _ in range(10)]

    assert all(response.status_code == 200 for response in responses)
