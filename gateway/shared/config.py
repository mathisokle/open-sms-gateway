"""Application settings loaded from environment variables (SPEC §7).

ADMIN_PASSWORD and SECRET_KEY are mandatory unless running under pytest, so tests
and local tooling can construct Settings without a fully configured environment.
"""

import os
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_KEY_LEN = 32  # below this the itsdangerous session cookie is brute-forceable
MIN_ADMIN_PASSWORD_LEN = 12


class ConfigError(Exception):
    """Startup configuration is unusable. Carries only the reason, never the values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_port: int = 8080
    admin_user: str = "admin"
    admin_password: str = ""
    secret_key: str = ""
    session_cookie_secure: bool = False  # set to 1 when serving the admin over TLS
    host_modem_device: str = ""
    modem_fake: bool = False
    messages_per_minute: int = Field(default=6, gt=0)  # 0 would silently stall the outbox
    rate_limit_per_minute: int = 0
    database_path: str = "/data/gateway.db"
    tz: str = "Europe/Zurich"

    @model_validator(mode="after")
    def _require_secrets(self) -> "Settings":
        if "PYTEST_CURRENT_TEST" in os.environ:
            return self
        # Collect every problem before raising: this runs at container start, so reporting
        # one issue at a time would send the operator through a restart per mistake.
        # Never include the values themselves (CONTRIBUTING.md: no secrets in logs).
        problems: list[str] = []
        for name in ("admin_password", "secret_key"):
            if not getattr(self, name):
                problems.append(f"{name.upper()} is not set")
        if self.secret_key and len(self.secret_key) < MIN_SECRET_KEY_LEN:
            problems.append(f"SECRET_KEY must be at least {MIN_SECRET_KEY_LEN} characters")
        if self.admin_password and len(self.admin_password) < MIN_ADMIN_PASSWORD_LEN:
            problems.append(f"ADMIN_PASSWORD must be at least {MIN_ADMIN_PASSWORD_LEN} characters")
        if problems:
            raise ValueError(
                "invalid configuration: "
                + "; ".join(problems)
                + ". Fix these in your .env (see .env.example), then restart: docker compose up -d"
            )
        return self

    @model_validator(mode="after")
    def _validate_tz(self) -> "Settings":
        # always on (even under pytest): a broken TZ must fail at startup, not later
        # on every admin page render (ZoneInfo is resolved lazily in the templates).
        try:
            ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"invalid TZ '{self.tz}': {exc}") from exc
        return self


def safe_reasons(exc: ValidationError) -> str:
    """The 'msg' parts of a ValidationError — without pydantic's `input_value=...` dump.

    That dump repeats the whole settings input, so ADMIN_PASSWORD and SECRET_KEY appear
    in plaintext in `str(exc)`. Only the messages we wrote ourselves are safe to log.
    """
    reasons = "; ".join(str(error.get("msg", "")).removeprefix("Value error, ") for error in exc.errors())
    return reasons or "settings could not be loaded"


@lru_cache
def get_settings() -> Settings:
    """Load settings, or fail with a message that is safe to paste into a bug report.

    `from None` drops the original exception too: a chained traceback would print the
    secret-bearing ValidationError right underneath ours.
    """
    try:
        return Settings()
    except ValidationError as exc:
        raise ConfigError(safe_reasons(exc)) from None
