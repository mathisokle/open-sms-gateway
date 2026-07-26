"""/healthz per SPEC §4.6 (no auth, 503 on a stale heartbeat)."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from gateway.shared.models import GatewayStatus
from tests.conftest import insert_message


def seed_status(
    client: TestClient,
    *,
    heartbeat: str,
    connected: str = "1",
    signal: str = "61",
    operator: str = "Sunrise",
) -> None:
    values = {
        "worker_heartbeat": heartbeat,
        "modem_connected": connected,
        "signal_percent": signal,
        "operator": operator,
        "registration": "home",
    }
    with Session(client.app.state.engine) as session:
        for key, value in values.items():
            session.merge(GatewayStatus(key=key, value=value, updated_at=heartbeat))
        session.commit()


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_healthz_ok_with_fresh_heartbeat(api: TestClient) -> None:
    heartbeat = iso(datetime.now(UTC))
    seed_status(api, heartbeat=heartbeat)

    response = api.get("/healthz")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["modem"] == {"connected": True, "signal_percent": 61, "operator": "Sunrise"}
    assert data["queue_depth"] == 0
    assert data["worker_seen_at"] == heartbeat


def test_healthz_reports_queue_depth(api: TestClient) -> None:
    seed_status(api, heartbeat=iso(datetime.now(UTC)))
    insert_message(api, status="queued")
    insert_message(api, status="queued", msisdn="+41790000001")
    insert_message(api, status="sent", msisdn="+41790000002")

    response = api.get("/healthz")

    assert response.status_code == 200
    assert response.json()["queue_depth"] == 2


def test_healthz_503_when_heartbeat_stale(api: TestClient) -> None:
    seed_status(api, heartbeat=iso(datetime.now(UTC) - timedelta(seconds=121)))

    response = api.get("/healthz")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_healthz_503_without_any_heartbeat(api: TestClient) -> None:
    response = api.get("/healthz")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "degraded"
    assert data["worker_seen_at"] is None
