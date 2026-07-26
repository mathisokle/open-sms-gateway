"""Admin acceptance tests: message browser with filters + detail view (SPEC §4.5)."""

from fastapi.testclient import TestClient

from tests.conftest import admin_login, insert_message


def test_message_browser_filters(api: TestClient) -> None:
    insert_message(api, body="out-sent", status="sent", msisdn="+41791111111")
    insert_message(api, body="out-queued", status="queued", msisdn="+41792222222")
    insert_message(api, body="in-received", direction="inbound", status="received", msisdn="+41793333333")
    admin_login(api)

    inbound = api.get("/admin/messages?direction=inbound").text
    assert "in-received" in inbound
    assert "out-sent" not in inbound

    sent = api.get("/admin/messages?status=sent").text
    assert "out-sent" in sent
    assert "out-queued" not in sent

    by_msisdn = api.get("/admin/messages?msisdn=%2B41792222222").text
    assert "out-queued" in by_msisdn
    assert "out-sent" not in by_msisdn


def test_message_detail_shows_full_message(api: TestClient) -> None:
    long_body = "Detailansicht " + "x" * 100
    msg_id = insert_message(api, body=long_body, status="sent")
    admin_login(api)

    response = api.get(f"/admin/messages/{msg_id}")

    assert response.status_code == 200
    assert long_body in response.text
    assert msg_id in response.text


def test_message_detail_unknown_id_is_404(api: TestClient) -> None:
    admin_login(api)

    response = api.get("/admin/messages/msg_does_not_exist")

    assert response.status_code == 404
