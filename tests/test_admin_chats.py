"""Admin acceptance tests: chat view per number + gateway own-number setting."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from gateway.shared.configstore import OWN_NUMBER, get_config
from gateway.shared.models import Message
from tests.conftest import admin_login, insert_message

NUMBER = "+41791234567"
ENCODED = "%2B41791234567"


def test_chat_list_groups_by_number(api: TestClient) -> None:
    insert_message(api, msisdn=NUMBER, body="hi there", status="sent", created_at="2026-07-25T10:00:00Z")
    insert_message(
        api,
        msisdn=NUMBER,
        body="reply",
        direction="inbound",
        status="received",
        created_at="2026-07-25T10:01:00Z",
    )
    insert_message(api, msisdn="+41790000009", body="other chat", created_at="2026-07-25T09:00:00Z")
    admin_login(api)

    response = api.get("/admin/chats")

    assert response.status_code == 200
    assert NUMBER in response.text
    assert "+41790000009" in response.text
    assert "reply" in response.text  # latest message preview for NUMBER


def test_chat_thread_shows_both_directions(api: TestClient) -> None:
    insert_message(api, msisdn=NUMBER, body="outgoing-bubble", status="delivered")
    insert_message(api, msisdn=NUMBER, body="incoming-bubble", direction="inbound", status="received")
    insert_message(api, msisdn="+41790000009", body="foreign-bubble")
    admin_login(api)

    response = api.get(f"/admin/chats/{ENCODED}")

    assert response.status_code == 200
    assert "outgoing-bubble" in response.text
    assert "incoming-bubble" in response.text
    assert "foreign-bubble" not in response.text
    assert "✓✓" in response.text  # delivered tick

    partial = api.get(f"/admin/partials/chat/{ENCODED}")
    assert partial.status_code == 200
    assert "outgoing-bubble" in partial.text


def test_chat_send_enqueues_outbound(api: TestClient) -> None:
    admin_login(api)

    response = api.post(f"/admin/chats/{ENCODED}/send", data={"body": "from chat"}, follow_redirects=False)

    assert response.status_code == 303
    with Session(api.app.state.engine) as session:
        message = session.query(Message).one()
        assert message.direction == "outbound"
        assert message.msisdn == NUMBER
        assert message.body == "from chat"
        assert message.status == "queued"


def test_chat_send_via_htmx_returns_thread_partial(api: TestClient) -> None:
    admin_login(api)

    response = api.post(
        f"/admin/chats/{ENCODED}/send",
        data={"body": "instant bubble"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "instant bubble" in response.text  # new bubble rendered without a redirect
    assert "bubble-out" in response.text


def test_chat_send_empty_body_is_422(api: TestClient) -> None:
    admin_login(api)

    response = api.post(f"/admin/chats/{ENCODED}/send", data={"body": "   "}, follow_redirects=False)

    assert response.status_code == 422


def test_own_number_setting_roundtrip(api: TestClient) -> None:
    admin_login(api)

    response = api.post(
        "/admin/settings/own-number", data={"own_number": "+41770001122"}, follow_redirects=False
    )

    assert response.status_code == 303
    with Session(api.app.state.engine) as session:
        assert get_config(session, OWN_NUMBER) == "+41770001122"
    assert "+41770001122" in api.get("/admin").text  # modem card shows it

    invalid = api.post("/admin/settings/own-number", data={"own_number": "0777"}, follow_redirects=False)
    assert invalid.status_code == 422

    cleared = api.post("/admin/settings/own-number", data={"own_number": ""}, follow_redirects=False)
    assert cleared.status_code == 303
    with Session(api.app.state.engine) as session:
        assert get_config(session, OWN_NUMBER) is None


def test_chat_shows_newest_messages_when_thread_exceeds_cap(api: TestClient) -> None:
    """The 300-message render cap must keep the newest messages, not the oldest."""
    from gateway.shared import ids

    with Session(api.app.state.engine) as session:
        for i in range(301):
            session.add(
                Message(
                    id=ids.message_id(),
                    direction="outbound",
                    msisdn="+41791234567",
                    body=f"bulk-{i:03d}",
                    status="sent",
                    created_at=f"2026-07-20T10:{i // 60:02d}:{i % 60:02d}Z",
                )
            )
        session.commit()
    admin_login(api)

    response = api.get("/admin/chats/+41791234567")

    assert response.status_code == 200
    assert "bulk-300" in response.text  # newest visible
    assert "bulk-000" not in response.text  # oldest dropped by the cap


def test_test_sms_rejects_trailing_newline_in_number(api: TestClient) -> None:
    """E164 must be a full match: a trailing newline (Python $-quirk) must be rejected."""
    admin_login(api)

    response = api.post(
        "/admin/test-sms", data={"to": "+41791234567\n", "body": "hi"}, follow_redirects=False
    )

    assert response.status_code == 422


def test_test_sms_rejects_oversized_body(api: TestClient) -> None:
    admin_login(api)

    response = api.post(
        "/admin/test-sms", data={"to": "+41791234567", "body": "a" * 1601}, follow_redirects=False
    )

    assert response.status_code == 422


def test_chat_send_rejects_oversized_body(api: TestClient) -> None:
    admin_login(api)

    response = api.post("/admin/chats/+41791234567/send", data={"body": "a" * 1601}, follow_redirects=False)

    assert response.status_code == 422
