"""API acceptance tests: /api/v1/messages (SPEC §5) — enqueue, filters, pagination."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from gateway.shared.models import Message
from tests.conftest import auth_header, create_token, insert_message


@pytest.fixture()
def token(api: TestClient) -> str:
    return create_token(api)


# --- POST /messages ---


def test_post_message_enqueues_and_returns_201(api: TestClient, token: str) -> None:
    response = api.post(
        "/api/v1/messages",
        json={"to": "+41791234567", "body": "Hallo"},
        headers=auth_header(token),
    )

    assert response.status_code == 201
    data = response.json()
    assert data["id"].startswith("msg_")
    assert data["status"] == "queued"
    assert data["segments"] == 1
    assert data["created_at"].endswith("Z")
    with Session(api.app.state.engine) as session:
        message = session.get(Message, data["id"])
        assert message is not None
        assert message.direction == "outbound"
        assert message.msisdn == "+41791234567"
        assert message.body == "Hallo"
        assert message.status == "queued"


def test_post_long_body_reports_segment_count(api: TestClient, token: str) -> None:
    response = api.post(
        "/api/v1/messages",
        json={"to": "+41791234567", "body": "a" * 161},
        headers=auth_header(token),
    )

    assert response.status_code == 201
    assert response.json()["segments"] == 2


@pytest.mark.parametrize(
    "to",
    ["0791234567", "41791234567", "+041791234567", "+4179", "+41 79 123 45 67", ""],
)
def test_post_invalid_e164_recipient_is_422(api: TestClient, token: str, to: str) -> None:
    response = api.post("/api/v1/messages", json={"to": to, "body": "hi"}, headers=auth_header(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_post_missing_or_empty_body_is_422(api: TestClient, token: str) -> None:
    missing = api.post("/api/v1/messages", json={"to": "+41791234567"}, headers=auth_header(token))
    empty = api.post("/api/v1/messages", json={"to": "+41791234567", "body": ""}, headers=auth_header(token))

    assert missing.status_code == 422
    assert empty.status_code == 422


def test_post_without_token_is_401(api: TestClient) -> None:
    response = api.post("/api/v1/messages", json={"to": "+41791234567", "body": "hi"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


# --- GET /messages (list) ---


def test_get_messages_filters_direction_and_status(api: TestClient, token: str) -> None:
    insert_message(api, body="out-sent", status="sent", created_at="2026-07-24T10:00:00Z")
    insert_message(api, body="out-queued", status="queued", created_at="2026-07-24T10:01:00Z")
    insert_message(
        api,
        body="in-received",
        direction="inbound",
        status="received",
        created_at="2026-07-24T10:02:00Z",
    )

    inbound = api.get("/api/v1/messages?direction=inbound", headers=auth_header(token)).json()["data"]
    sent = api.get("/api/v1/messages?status=sent", headers=auth_header(token)).json()["data"]

    assert [m["body"] for m in inbound] == ["in-received"]
    assert [m["body"] for m in sent] == ["out-sent"]


def test_get_messages_filters_since_and_until(api: TestClient, token: str) -> None:
    insert_message(api, body="early", created_at="2026-07-24T10:00:00Z")
    insert_message(api, body="middle", created_at="2026-07-24T11:00:00Z")
    insert_message(api, body="late", created_at="2026-07-24T12:00:00Z")

    response = api.get(
        "/api/v1/messages?since=2026-07-24T10:30:00Z&until=2026-07-24T11:30:00Z",
        headers=auth_header(token),
    )

    assert [m["body"] for m in response.json()["data"]] == ["middle"]


def test_get_messages_filters_to_and_from(api: TestClient, token: str) -> None:
    insert_message(api, body="out-1", msisdn="+41791111111", created_at="2026-07-24T10:00:00Z")
    insert_message(api, body="out-2", msisdn="+41792222222", created_at="2026-07-24T10:01:00Z")
    insert_message(
        api,
        body="in-1",
        direction="inbound",
        status="received",
        msisdn="+41791111111",
        created_at="2026-07-24T10:02:00Z",
    )

    to_filtered = api.get("/api/v1/messages?to=%2B41791111111", headers=auth_header(token)).json()["data"]
    from_filtered = api.get("/api/v1/messages?from=%2B41791111111", headers=auth_header(token)).json()["data"]

    assert [m["body"] for m in to_filtered] == ["out-1"]
    assert [m["body"] for m in from_filtered] == ["in-1"]


def test_message_representation_maps_to_and_from_by_direction(api: TestClient, token: str) -> None:
    insert_message(
        api,
        body="out",
        status="sent",
        created_at="2026-07-24T10:00:00Z",
        sent_at="2026-07-24T10:00:05Z",
    )
    insert_message(
        api,
        body="in",
        direction="inbound",
        status="received",
        msisdn="+41798888888",
        created_at="2026-07-24T10:01:00Z",
        received_at="2026-07-24T10:01:00Z",
    )

    data = api.get("/api/v1/messages", headers=auth_header(token)).json()["data"]

    outbound, inbound = data[0], data[1]
    assert outbound["to"] == "+41791234567"
    assert outbound["from"] is None
    assert outbound["sent_at"] == "2026-07-24T10:00:05Z"
    assert inbound["from"] == "+41798888888"
    assert inbound["to"] is None
    assert inbound["received_at"] == "2026-07-24T10:01:00Z"
    assert set(outbound) == {
        "id",
        "direction",
        "to",
        "from",
        "body",
        "status",
        "segments",
        "error",
        "created_at",
        "sent_at",
        "delivered_at",
        "received_at",
    }


def test_pagination_walks_all_messages_without_duplicates_or_gaps(api: TestClient, token: str) -> None:
    expected_ids = [insert_message(api, body=f"m{i}", created_at=f"2026-07-24T10:00:0{i}Z") for i in range(5)]

    collected: list[str] = []
    cursor = ""
    pages = 0
    while True:
        url = f"/api/v1/messages?limit=2{cursor}"
        page = api.get(url, headers=auth_header(token)).json()
        collected.extend(m["id"] for m in page["data"])
        pages += 1
        if page["next_cursor"] is None:
            break
        cursor = f"&cursor={page['next_cursor']}"

    assert collected == expected_ids
    assert pages == 3


def test_limit_out_of_range_is_422(api: TestClient, token: str) -> None:
    too_big = api.get("/api/v1/messages?limit=201", headers=auth_header(token))
    zero = api.get("/api/v1/messages?limit=0", headers=auth_header(token))

    assert too_big.status_code == 422
    assert zero.status_code == 422


def test_garbage_cursor_is_422(api: TestClient, token: str) -> None:
    response = api.get("/api/v1/messages?cursor=not-a-cursor", headers=auth_header(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --- GET /messages/{id} ---


def test_get_message_by_id(api: TestClient, token: str) -> None:
    msg_id = insert_message(api, body="mine")

    response = api.get(f"/api/v1/messages/{msg_id}", headers=auth_header(token))

    assert response.status_code == 200
    assert response.json()["id"] == msg_id
    assert response.json()["body"] == "mine"


def test_get_unknown_message_is_404(api: TestClient, token: str) -> None:
    response = api.get("/api/v1/messages/msg_does_not_exist", headers=auth_header(token))

    assert response.status_code == 404


def test_invalid_status_filter_is_rejected(api: TestClient, token: str) -> None:
    response = api.get("/api/v1/messages?status=bogus", headers=auth_header(token))

    assert response.status_code == 422


def test_since_with_offset_format_is_rejected(api: TestClient, token: str) -> None:
    response = api.get(
        "/api/v1/messages",
        params={"since": "2026-07-24T10:30:00+00:00"},
        headers=auth_header(token),
    )

    assert response.status_code == 422


def test_body_over_segment_cap_is_rejected(api, token):
    from tests.conftest import auth_header

    huge = "a" * 1601  # exceeds MAX_BODY_CHARS
    r = api.post("/api/v1/messages", json={"to": "+41791234567", "body": huge}, headers=auth_header(token))
    assert r.status_code == 422


def test_body_at_cap_is_accepted(api, token):
    from tests.conftest import auth_header

    ok = "a" * 1530  # 10 GSM-7 segments exactly
    r = api.post("/api/v1/messages", json={"to": "+41791234567", "body": ok}, headers=auth_header(token))
    assert r.status_code == 201


def test_openapi_and_docs_are_disabled(api):
    assert api.get("/api/openapi.json").status_code == 404
    assert api.get("/api/docs").status_code == 404
