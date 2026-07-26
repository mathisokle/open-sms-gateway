"""Bearer-token authentication via SHA-256 hash lookup (SPEC §3).

Tokens are stored hashed only; auth rejects unknown and revoked tokens (401)
and stamps last_used_at on success.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from gateway.shared.clock import utc_now_iso
from gateway.shared.models import ApiToken

_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}  # RFC 6750 §3
# only re-stamp last_used_at when it is this stale — one write per token per minute at most,
# not one per request (SQLite has a single writer; the worker competes for it)
LAST_USED_REFRESH_SECONDS = 60


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class CurrentToken:
    id: str
    label: str | None


def _needs_refresh(last_used_at: str | None) -> bool:
    if not last_used_at:
        return True
    age = datetime.now(UTC) - datetime.fromisoformat(last_used_at)
    return age >= timedelta(seconds=LAST_USED_REFRESH_SECONDS)


def authenticate(request: Request) -> CurrentToken:
    header = request.headers.get("Authorization", "")
    scheme, _, rest = header.partition(" ")
    token = rest.strip()  # tolerate RFC 7235 multiple SP between scheme and credentials
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="missing or malformed Authorization header",
            headers=_UNAUTHORIZED_HEADERS,
        )
    with Session(request.app.state.engine) as session:
        row = (
            session.query(ApiToken)
            .filter(ApiToken.token_hash == hash_token(token), ApiToken.revoked_at.is_(None))
            .one_or_none()
        )
        if row is None:
            raise HTTPException(status_code=401, detail="invalid token", headers=_UNAUTHORIZED_HEADERS)
        current = CurrentToken(id=row.id, label=row.label)
        if _needs_refresh(row.last_used_at):
            row.last_used_at = utc_now_iso()
            session.commit()
    return current
