"""Admin acceptance tests: dashboard content + UI smoke for all pages."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from gateway.shared.clock import utc_now_iso
from gateway.shared.models import GatewayStatus
from tests.conftest import admin_login, insert_message


def seed_status(api: TestClient) -> None:
    now = utc_now_iso()
    values = {
        "worker_heartbeat": now,
        "modem_connected": "1",
        "signal_percent": "61",
        "operator": "Sunrise",
        "registration": "home",
    }
    with Session(api.app.state.engine) as session:
        for key, value in values.items():
            session.merge(GatewayStatus(key=key, value=value, updated_at=now))
        session.commit()


@pytest.mark.parametrize(
    "route",
    [
        "/admin",
        "/admin/tokens",
        "/admin/messages",
        "/admin/webhooks",
        "/admin/settings",
        "/admin/docs",
    ],
)
def test_admin_pages_render_with_session(api: TestClient, route: str) -> None:
    admin_login(api)

    response = api.get(route)

    assert response.status_code == 200
    assert "SMS Gateway" in response.text


def test_dashboard_shows_modem_status_queue_and_today_counters(api: TestClient) -> None:
    seed_status(api)
    now = utc_now_iso()
    insert_message(api, status="queued")
    insert_message(api, status="sent", msisdn="+41790000001", sent_at=now, created_at=now)
    insert_message(api, direction="inbound", status="received", msisdn="+41790000002", received_at=now)
    insert_message(api, status="failed", msisdn="+41790000003", created_at=now)
    admin_login(api)

    response = api.get("/admin")

    assert response.status_code == 200
    assert "Sunrise" in response.text
    assert 'data-stat="queue_depth">1<' in response.text
    assert 'data-stat="sent_today">1<' in response.text
    assert 'data-stat="received_today">1<' in response.text
    assert 'data-stat="failed_today">1<' in response.text
    assert 'data-stat="total_messages">4<' in response.text
    # success rate = non-failed share of concluded outbound: 1 sent / (1 sent + 1 failed)
    assert 'data-stat="success_rate">50%<' in response.text
    assert "Success rate" in response.text
    assert "Sent today" in response.text  # the UI is English-only
    assert "<svg" in response.text  # 24h activity chart
    assert 'class="donut"' in response.text  # status donut
    assert "trend-line" in response.text  # 7-day trend


def test_settings_page_contains_test_sms_info_and_danger_zone(api: TestClient) -> None:
    admin_login(api)

    response = api.get("/admin/settings")

    assert 'action="/admin/test-sms"' in response.text
    assert 'action="/admin/purge-messages"' in response.text
    assert 'action="/admin/restart-worker"' in response.text
    from gateway import __version__

    assert f"v{__version__}" in response.text  # gateway info card


def test_stats_partial_renders_for_htmx_polling(api: TestClient) -> None:
    seed_status(api)
    admin_login(api)

    response = api.get("/admin/partials/stats")

    assert response.status_code == 200
    assert 'data-stat="queue_depth"' in response.text
    assert "<svg" in response.text


def test_footer_shows_version_and_github_link(api: TestClient) -> None:
    admin_login(api)

    response = api.get("/admin")

    assert "github.com/mathisokle" in response.text
    from gateway import __version__

    assert f"v{__version__}" in response.text
