"""Admin acceptance tests: users, event log, test SMS, restarts."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import gateway.api.routes_admin as routes_admin
from gateway.shared.configstore import RESTART_WORKER, get_config
from gateway.shared.db import create_db_engine, run_migrations
from gateway.shared.events import record_event
from gateway.shared.models import AdminUser, Event, Message
from gateway.worker.main import restart_requested
from tests.conftest import admin_login

# --- admin users ---


def test_create_user_and_login_with_it(api: TestClient) -> None:
    admin_login(api)

    response = api.post(
        "/admin/users", data={"username": "mathis", "password": "super-secret-pw"}, follow_redirects=False
    )

    assert response.status_code == 303
    with Session(api.app.state.engine) as session:
        user = session.query(AdminUser).one()
        assert user.username == "mathis"
        assert user.password_hash.startswith("pbkdf2$")
        assert "super-secret-pw" not in user.password_hash

    api.cookies.clear()
    login = api.post(
        "/admin/login", data={"username": "mathis", "password": "super-secret-pw"}, follow_redirects=False
    )
    assert login.status_code == 303


def test_env_admin_still_works_with_db_users(api: TestClient) -> None:
    admin_login(api)
    api.post("/admin/users", data={"username": "other", "password": "super-secret-pw"})
    api.cookies.clear()

    admin_login(api)  # env credentials

    assert api.get("/admin").status_code == 200


def test_short_password_and_duplicate_username_rejected(api: TestClient) -> None:
    admin_login(api)

    short = api.post("/admin/users", data={"username": "x", "password": "short"}, follow_redirects=False)
    api.post("/admin/users", data={"username": "dup", "password": "super-secret-pw"})
    duplicate = api.post(
        "/admin/users", data={"username": "dup", "password": "super-secret-pw"}, follow_redirects=False
    )

    assert short.status_code == 422
    assert duplicate.status_code == 422


def test_delete_user_blocks_their_login(api: TestClient) -> None:
    admin_login(api)
    api.post("/admin/users", data={"username": "temp", "password": "super-secret-pw"})
    with Session(api.app.state.engine) as session:
        user_id = session.query(AdminUser).one().id

    api.post(f"/admin/users/{user_id}/delete", follow_redirects=False)

    api.cookies.clear()
    login = api.post(
        "/admin/login", data={"username": "temp", "password": "super-secret-pw"}, follow_redirects=False
    )
    assert login.status_code == 401


# --- event log ---


def test_logs_page_shows_recorded_events(api: TestClient) -> None:
    with Session(api.app.state.engine) as session:
        record_event(session, "worker", "error", "modem loop error (TestError), reconnecting")
        session.commit()
    admin_login(api)

    page = api.get("/admin/logs")
    partial = api.get("/admin/partials/logs")

    assert page.status_code == 200
    assert "modem loop error" in page.text
    assert "modem loop error" in partial.text


def test_event_retention_prunes_old_entries(api: TestClient) -> None:
    with Session(api.app.state.engine) as session:
        session.add(Event(ts="2020-01-01T00:00:00Z", source="worker", level="info", message="ancient"))
        session.commit()
        record_event(session, "admin", "info", "fresh")
        session.commit()
        messages = [event.message for event in session.query(Event).all()]
    assert "fresh" in messages
    assert "ancient" not in messages


def test_logs_can_be_filtered_by_level_and_source(api: TestClient) -> None:
    with Session(api.app.state.engine) as session:
        record_event(session, "worker", "error", "boom-error")
        record_event(session, "admin", "info", "calm-info")
        session.commit()
    admin_login(api)

    only_errors = api.get("/admin/logs?level=error").text
    only_admin = api.get("/admin/partials/logs?source=admin").text

    assert "boom-error" in only_errors
    assert "calm-info" not in only_errors
    assert "calm-info" in only_admin
    assert "boom-error" not in only_admin


def test_change_password_swaps_credentials(api: TestClient) -> None:
    admin_login(api)
    api.post("/admin/users", data={"username": "rotate", "password": "old-password-1"})
    with Session(api.app.state.engine) as session:
        user_id = session.query(AdminUser).one().id

    response = api.post(
        f"/admin/users/{user_id}/password", data={"password": "new-password-2"}, follow_redirects=False
    )

    assert response.status_code == 303
    api.cookies.clear()
    old = api.post(
        "/admin/login", data={"username": "rotate", "password": "old-password-1"}, follow_redirects=False
    )
    new = api.post(
        "/admin/login", data={"username": "rotate", "password": "new-password-2"}, follow_redirects=False
    )
    assert old.status_code == 401
    assert new.status_code == 303


def test_purge_deletes_old_messages_and_their_deliveries(api: TestClient) -> None:
    from gateway.shared import ids as ids_mod
    from gateway.shared.models import WebhookDelivery

    admin_login(api)
    with Session(api.app.state.engine) as session:
        old = Message(
            id=ids_mod.message_id(),
            direction="inbound",
            msisdn="+41790000001",
            body="ancient",
            status="received",
            created_at="2020-01-01T00:00:00Z",
        )
        session.add(old)
        session.add(
            WebhookDelivery(
                id=ids_mod.delivery_id(),
                message_id=old.id,
                attempt=1,
                status="failed",
                created_at="2020-01-01T00:00:00Z",
            )
        )
        fresh = Message(
            id=ids_mod.message_id(),
            direction="inbound",
            msisdn="+41790000002",
            body="fresh",
            status="received",
            created_at="2026-07-25T00:00:00Z",
        )
        session.add(fresh)
        session.commit()

    response = api.post("/admin/purge-messages", data={"days": "90"}, follow_redirects=False)

    assert response.status_code == 303
    with Session(api.app.state.engine) as session:
        bodies = [m.body for m in session.query(Message).all()]
        assert bodies == ["fresh"]
        assert session.query(WebhookDelivery).count() == 0


def test_purge_rejects_arbitrary_day_values(api: TestClient) -> None:
    admin_login(api)

    response = api.post("/admin/purge-messages", data={"days": "1"}, follow_redirects=False)

    assert response.status_code == 422


# --- test SMS ---


def test_test_sms_enqueues_message(api: TestClient) -> None:
    admin_login(api)

    response = api.post(
        "/admin/test-sms", data={"to": "+41791234567", "body": "ping"}, follow_redirects=False
    )

    assert response.status_code == 303
    with Session(api.app.state.engine) as session:
        message = session.query(Message).one()
        assert message.status == "queued"
        assert message.msisdn == "+41791234567"
        assert message.body == "ping"


def test_test_sms_invalid_number_is_422(api: TestClient) -> None:
    admin_login(api)

    response = api.post("/admin/test-sms", data={"to": "0791234567"}, follow_redirects=False)

    assert response.status_code == 422


# --- restarts ---


def test_restart_worker_sets_flag_and_worker_consumes_it(api: TestClient, tmp_path) -> None:
    admin_login(api)

    response = api.post("/admin/restart-worker", follow_redirects=False)

    assert response.status_code == 303
    engine = api.app.state.engine
    with Session(engine) as session:
        assert get_config(session, RESTART_WORKER) is not None

    assert restart_requested(engine) is True  # worker consumes and clears the flag
    with Session(engine) as session:
        assert get_config(session, RESTART_WORKER) is None
    assert restart_requested(engine) is False


def test_restart_api_triggers_process_termination(api: TestClient, monkeypatch) -> None:
    admin_login(api)
    calls: list[bool] = []
    monkeypatch.setattr(routes_admin, "_terminate_process", lambda: calls.append(True))

    response = api.post("/admin/restart-api", follow_redirects=False)

    assert response.status_code == 303
    assert calls == [True]


def test_worker_restart_check_without_flag_is_noop(tmp_path) -> None:
    engine = create_db_engine(tmp_path / "restart.db")
    run_migrations(engine)

    assert restart_requested(engine) is False
    engine.dispose()
