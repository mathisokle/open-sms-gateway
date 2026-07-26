"""Settings loaded from env vars per SPEC §7, mandatory fields validated."""

import pytest
from pydantic import ValidationError

from gateway.shared.config import Settings, safe_reasons

SPEC_ENV_VARS = [
    "API_PORT",
    "ADMIN_USER",
    "ADMIN_PASSWORD",
    "SECRET_KEY",
    "HOST_MODEM_DEVICE",
    "MODEM_FAKE",
    "MESSAGES_PER_MINUTE",
    "RATE_LIMIT_PER_MINUTE",
    "DATABASE_PATH",
    "TZ",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in SPEC_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_defaults_match_spec() -> None:
    settings = Settings(_env_file=None)

    assert settings.api_port == 8080
    assert settings.modem_fake is False
    assert settings.messages_per_minute == 6
    assert settings.rate_limit_per_minute == 0
    assert settings.database_path == "/data/gateway.db"
    assert settings.tz == "Europe/Zurich"


def test_env_vars_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESSAGES_PER_MINUTE", "10")
    monkeypatch.setenv("MODEM_FAKE", "1")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "30")

    settings = Settings(_env_file=None)

    assert settings.messages_per_minute == 10
    assert settings.modem_fake is True
    assert settings.rate_limit_per_minute == 30


def test_admin_password_and_secret_key_required_outside_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    with pytest.raises(ValidationError, match="ADMIN_PASSWORD"):
        Settings(_env_file=None)


def test_required_fields_accepted_when_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    settings = Settings(_env_file=None, admin_password="a-good-password", secret_key="s" * 64)

    assert settings.admin_password == "a-good-password"
    assert settings.secret_key == "s" * 64


def test_zero_messages_per_minute_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, messages_per_minute=0)


def test_short_secret_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(_env_file=None, admin_password="a-good-password", secret_key="short")


def test_short_admin_password_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(ValidationError, match="ADMIN_PASSWORD"):
        Settings(_env_file=None, admin_password="short", secret_key="s" * 40)


def test_every_config_problem_is_reported_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # this runs at container start: one problem per restart would be a miserable upgrade
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, admin_password="short", secret_key="also-short")

    message = safe_reasons(caught.value)
    assert "ADMIN_PASSWORD" in message
    assert "SECRET_KEY" in message
    assert ".env" in message  # tells the operator which file to fix


def test_config_failure_never_exposes_the_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """pydantic renders the whole input dict in `input_value=...` — that leaks the
    plaintext secrets into stdout, i.e. into `docker compose logs`."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    password, key = "pw-in-the-clear", "key-in-the-clear"
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, admin_password=password, secret_key=key)

    assert key in str(caught.value), "guard assumes pydantic still dumps the input values"
    message = safe_reasons(caught.value)
    assert password not in message
    assert key not in message


def test_invalid_timezone_is_rejected() -> None:
    # TZ is validated always (not skipped under pytest) so a broken zone fails fast at start.
    with pytest.raises(ValidationError, match="[Tt][Zz]"):
        Settings(_env_file=None, admin_password="a" * 12, secret_key="s" * 40, tz="Not/AZone")
