"""Admin acceptance tests: webhook log + manual retry (SPEC §4.3/§4.5)."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from gateway.shared import ids
from gateway.shared.clock import utc_now_iso
from gateway.shared.models import WebhookDelivery
from tests.conftest import admin_login, insert_message


def make_delivery(api: TestClient, *, status: str, attempt: int, response_code: int | None) -> str:
    message_id = insert_message(api, direction="inbound", status="received")
    delivery = WebhookDelivery(
        id=ids.delivery_id(),
        message_id=message_id,
        attempt=attempt,
        status=status,
        response_code=response_code,
        created_at=utc_now_iso(),
    )
    with Session(api.app.state.engine) as session:
        session.add(delivery)
        session.commit()
        return delivery.id


def test_webhook_log_lists_deliveries(api: TestClient) -> None:
    delivery_id = make_delivery(api, status="failed", attempt=6, response_code=500)
    admin_login(api)

    response = api.get("/admin/webhooks")

    assert response.status_code == 200
    assert "failed" in response.text
    assert "500" in response.text
    assert f"/admin/webhooks/{delivery_id}/retry" in response.text


def test_manual_retry_resets_failed_delivery_to_pending_and_due(api: TestClient) -> None:
    delivery_id = make_delivery(api, status="failed", attempt=6, response_code=500)
    admin_login(api)

    response = api.post(f"/admin/webhooks/{delivery_id}/retry", follow_redirects=False)

    assert response.status_code == 303
    with Session(api.app.state.engine) as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        assert delivery is not None
        assert delivery.status == "pending"
        assert delivery.next_retry_at is not None
        assert delivery.next_retry_at <= utc_now_iso()


def test_manual_retry_unknown_delivery_is_404(api: TestClient) -> None:
    admin_login(api)

    response = api.post("/admin/webhooks/whd_missing/retry", follow_redirects=False)

    assert response.status_code == 404


def test_retry_of_delivered_delivery_is_rejected(api: TestClient) -> None:
    delivery_id = make_delivery(api, status="delivered", attempt=1, response_code=200)
    admin_login(api)

    response = api.post(f"/admin/webhooks/{delivery_id}/retry", follow_redirects=False)

    assert response.status_code == 409
    with Session(api.app.state.engine) as session:
        assert session.get(WebhookDelivery, delivery_id).status == "delivered"
