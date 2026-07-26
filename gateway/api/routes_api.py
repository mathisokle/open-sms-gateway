"""REST API v1 endpoints (SPEC §5), mounted under /api/v1."""

import base64
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from gateway.api.auth import CurrentToken, authenticate
from gateway.shared import ids
from gateway.shared.clock import utc_now_iso
from gateway.shared.models import Message
from gateway.shared.sms import MAX_BODY_CHARS, MAX_SEGMENTS, body_too_long, count_segments

E164_PATTERN = r"^\+[1-9][0-9]{6,14}$"
STATUS_PATTERN = "^(queued|sending|sent|delivered|failed|received)$"
# timestamps compare lexicographically only in the canonical Z-format
TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
RATE_LIMIT_KEY = "api"  # single-tenant: one shared window

router = APIRouter()


def authorize(request: Request, token: Annotated[CurrentToken, Depends(authenticate)]) -> CurrentToken:
    """Auth plus optional rate limit (SPEC §5: 429)."""
    if not request.app.state.rate_limiter.allow(RATE_LIMIT_KEY):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    return token


AuthToken = Annotated[CurrentToken, Depends(authorize)]


class SendMessageRequest(BaseModel):
    to: str = Field(pattern=E164_PATTERN)
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)


@router.post("/messages", status_code=201)
def post_message(request: Request, _token: AuthToken, payload: SendMessageRequest) -> dict:
    # char cap is enforced by the field; also cap segments (UCS-2 bodies stay under the
    # char limit but can still explode into many segments)
    if body_too_long(payload.body):
        raise HTTPException(status_code=422, detail=f"body exceeds {MAX_SEGMENTS} SMS segments")
    message = Message(
        id=ids.message_id(),
        direction="outbound",
        msisdn=payload.to,
        body=payload.body,
        status="queued",
        segments=count_segments(payload.body),
        created_at=utc_now_iso(),
    )
    with Session(request.app.state.engine) as session:
        session.add(message)
        session.commit()
        return {
            "id": message.id,
            "status": message.status,
            "segments": message.segments,
            "created_at": message.created_at,
        }


def _serialize(message: Message) -> dict:
    outbound = message.direction == "outbound"
    return {
        "id": message.id,
        "direction": message.direction,
        "to": message.msisdn if outbound else None,
        "from": message.msisdn if not outbound else None,
        "body": message.body,
        "status": message.status,
        "segments": message.segments,
        "error": message.error,
        "created_at": message.created_at,
        "sent_at": message.sent_at,
        "delivered_at": message.delivered_at,
        "received_at": message.received_at,
    }


def _encode_cursor(created_at: str, message_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at}|{message_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        created_at, sep, message_id = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
    except Exception as exc:
        raise HTTPException(status_code=422, detail="invalid cursor") from exc
    if not sep or not created_at or not message_id:
        raise HTTPException(status_code=422, detail="invalid cursor")
    return created_at, message_id


@router.get("/messages")
def list_messages(
    request: Request,
    _token: AuthToken,
    direction: Annotated[str | None, Query(pattern="^(outbound|inbound)$")] = None,
    status: Annotated[str | None, Query(pattern=STATUS_PATTERN)] = None,
    since: Annotated[str | None, Query(pattern=TIMESTAMP_PATTERN)] = None,
    until: Annotated[str | None, Query(pattern=TIMESTAMP_PATTERN)] = None,
    to: Annotated[str | None, Query(pattern=E164_PATTERN)] = None,
    from_: Annotated[str | None, Query(alias="from", pattern=E164_PATTERN)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> dict:
    with Session(request.app.state.engine) as session:
        query = session.query(Message)
        if direction:
            query = query.filter(Message.direction == direction)
        if status:
            query = query.filter(Message.status == status)
        if since:
            query = query.filter(Message.created_at >= since)
        if until:
            query = query.filter(Message.created_at <= until)
        if to:
            query = query.filter(Message.direction == "outbound", Message.msisdn == to)
        if from_:
            query = query.filter(Message.direction == "inbound", Message.msisdn == from_)
        if cursor:
            after_created, after_id = _decode_cursor(cursor)
            query = query.filter(
                or_(
                    Message.created_at > after_created,
                    and_(Message.created_at == after_created, Message.id > after_id),
                )
            )
        rows = query.order_by(Message.created_at, Message.id).limit(limit).all()
        next_cursor = _encode_cursor(rows[-1].created_at, rows[-1].id) if len(rows) == limit else None
        return {"data": [_serialize(row) for row in rows], "next_cursor": next_cursor}


@router.get("/messages/{message_id}")
def get_message(request: Request, _token: AuthToken, message_id: str) -> dict:
    with Session(request.app.state.engine) as session:
        message = session.get(Message, message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        return _serialize(message)
