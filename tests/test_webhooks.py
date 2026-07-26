"""Webhook dispatcher acceptance tests (SPEC §4.3, ARCHITECTURE §4).

Delivery behavior is tested against a real local HTTP server (threaded http.server)
that records raw bodies and headers and serves scripted status codes.
"""

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from gateway.api.webhooks import (
    build_payload,
    dispatcher_loop,
    process_due_deliveries,
    sign_payload,
)
from gateway.shared import ids
from gateway.shared.models import Message, WebhookDelivery
from tests.conftest import TEST_WEBHOOK_SECRET, insert_message, set_webhook

NOW = datetime(2026, 7, 24, 12, 0, 0, tzinfo=UTC)
NOW_ISO = "2026-07-24T12:00:00Z"


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.requests.append(  # type: ignore[attr-defined]
            {"path": self.path, "body": body, "headers": dict(self.headers)}
        )
        behaviors = self.server.behaviors  # type: ignore[attr-defined]
        behavior = behaviors.pop(0) if behaviors else 200
        if behavior == "slow":
            time.sleep(0.5)
            behavior = 200
        self.send_response(behavior)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        pass


class HookServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.requests: list[dict] = []
        self.behaviors: list[object] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/hook"


@pytest.fixture()
def hook_server() -> Iterator[HookServer]:
    server = HookServer()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def insert_delivery(
    client: TestClient,
    message_id: str,
    *,
    attempt: int = 0,
    status: str = "pending",
    next_retry_at: str | None = NOW_ISO,
) -> str:
    delivery = WebhookDelivery(
        id=ids.delivery_id(),
        message_id=message_id,
        attempt=attempt,
        status=status,
        next_retry_at=next_retry_at,
        created_at=NOW_ISO,
    )
    with Session(client.app.state.engine) as session:
        session.add(delivery)
        session.commit()
        return delivery.id


def make_inbound_with_delivery(
    api: TestClient, webhook_url: str | None, *, attempt: int = 0, next_retry_at: str = NOW_ISO
) -> tuple[str, str]:
    """Configures the webhook target and returns (message_id, delivery_id)."""
    set_webhook(api, webhook_url)
    message_id = insert_message(
        api,
        direction="inbound",
        status="received",
        msisdn="+41791112233",
        body="Antworttext",
        received_at="2026-07-24T11:59:00Z",
    )
    delivery_id = insert_delivery(api, message_id, attempt=attempt, next_retry_at=next_retry_at)
    return message_id, delivery_id


def run_dispatch(api: TestClient, *, now: datetime = NOW, timeout: float = 2.0) -> int:
    async def _run() -> int:
        async with httpx.AsyncClient() as client:
            return await process_due_deliveries(api.app.state.engine, client, now=now, timeout=timeout)

    return asyncio.run(_run())


def get_delivery(api: TestClient, delivery_id: str) -> WebhookDelivery:
    with Session(api.app.state.engine) as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        assert delivery is not None
        session.expunge(delivery)
        return delivery


# --- signature (SPEC §4.3: X-Gateway-Signature: sha256=<hex hmac_sha256(secret, raw_body)>) ---


def test_sign_payload_is_hmac_sha256_hex_with_prefix() -> None:
    secret = "whsec_test"
    raw = b'{"id":"msg_1"}'

    signature = sign_payload(secret, raw)

    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    assert signature == f"sha256={expected}"


def test_sign_payload_differs_for_different_secrets() -> None:
    raw = b"payload"

    assert sign_payload("secret-a", raw) != sign_payload("secret-b", raw)


# --- payload (SPEC §4.3 example) ---


def test_build_payload_matches_spec_shape() -> None:
    message = Message(
        id="msg_01J8Z",
        direction="inbound",
        msisdn="+41791234567",
        body="Antworttext",
        status="received",
        segments=1,
        created_at="2026-07-24T18:00:01Z",
        received_at="2026-07-24T18:00:00Z",
    )

    payload = build_payload(message)

    assert payload == {
        "id": "msg_01J8Z",
        "type": "sms.received",
        "from": "+41791234567",
        "to": "gateway",
        "body": "Antworttext",
        "received_at": "2026-07-24T18:00:00Z",
    }
    assert json.loads(json.dumps(payload)) == payload


# --- delivery behavior against a local HTTP server ---


def test_2xx_marks_delivered_and_signature_is_verifiable(api: TestClient, hook_server: HookServer) -> None:
    message_id, delivery_id = make_inbound_with_delivery(api, hook_server.url)

    processed = run_dispatch(api)

    assert processed == 1
    assert len(hook_server.requests) == 1
    request = hook_server.requests[0]
    expected_sig = (
        "sha256=" + hmac.new(TEST_WEBHOOK_SECRET.encode(), request["body"], hashlib.sha256).hexdigest()
    )
    assert request["headers"]["X-Gateway-Signature"] == expected_sig
    assert request["headers"]["X-Gateway-Delivery"] == delivery_id
    assert request["headers"]["Content-Type"] == "application/json"
    payload = json.loads(request["body"])
    assert payload["id"] == message_id
    assert payload["type"] == "sms.received"
    assert payload["from"] == "+41791112233"
    assert payload["to"] == "gateway"
    assert payload["body"] == "Antworttext"

    delivery = get_delivery(api, delivery_id)
    assert delivery.status == "delivered"
    assert delivery.attempt == 1
    assert delivery.response_code == 200
    assert delivery.delivered_at is not None


def test_500_schedules_retry_with_one_minute_backoff(api: TestClient, hook_server: HookServer) -> None:
    hook_server.behaviors = [500]
    _, delivery_id = make_inbound_with_delivery(api, hook_server.url)

    run_dispatch(api, now=NOW)

    delivery = get_delivery(api, delivery_id)
    assert delivery.status == "pending"
    assert delivery.attempt == 1
    assert delivery.response_code == 500
    assert delivery.next_retry_at == "2026-07-24T12:01:00Z"
    assert delivery.delivered_at is None


@pytest.mark.parametrize(
    ("attempt_before", "expected_next"),
    [
        (0, "2026-07-24T12:01:00Z"),  # +1 min
        (1, "2026-07-24T12:05:00Z"),  # +5 min
        (2, "2026-07-24T12:30:00Z"),  # +30 min
        (3, "2026-07-24T14:00:00Z"),  # +2 h
        (4, "2026-07-24T18:00:00Z"),  # +6 h
    ],
)
def test_backoff_chain_follows_spec(
    api: TestClient, hook_server: HookServer, attempt_before: int, expected_next: str
) -> None:
    hook_server.behaviors = [500]
    _, delivery_id = make_inbound_with_delivery(api, hook_server.url, attempt=attempt_before)

    run_dispatch(api, now=NOW)

    delivery = get_delivery(api, delivery_id)
    assert delivery.status == "pending"
    assert delivery.attempt == attempt_before + 1
    assert delivery.next_retry_at == expected_next


def test_failed_after_five_failed_retries(api: TestClient, hook_server: HookServer) -> None:
    hook_server.behaviors = [500]
    _, delivery_id = make_inbound_with_delivery(api, hook_server.url, attempt=5)

    run_dispatch(api, now=NOW)

    delivery = get_delivery(api, delivery_id)
    assert delivery.status == "failed"
    assert delivery.attempt == 6
    assert delivery.delivered_at is None


def test_slow_endpoint_times_out_and_schedules_retry(api: TestClient, hook_server: HookServer) -> None:
    hook_server.behaviors = ["slow"]  # handler sleeps 0.5s; dispatcher timeout below is 0.1s
    _, delivery_id = make_inbound_with_delivery(api, hook_server.url)

    run_dispatch(api, now=NOW, timeout=0.1)

    delivery = get_delivery(api, delivery_id)
    assert delivery.status == "pending"
    assert delivery.attempt == 1
    assert delivery.response_code is None
    assert delivery.next_retry_at == "2026-07-24T12:01:00Z"


def test_future_deliveries_are_not_touched(api: TestClient, hook_server: HookServer) -> None:
    _, delivery_id = make_inbound_with_delivery(api, hook_server.url, next_retry_at="2026-07-24T12:30:00Z")

    processed = run_dispatch(api, now=NOW)

    assert processed == 0
    assert hook_server.requests == []
    delivery = get_delivery(api, delivery_id)
    assert delivery.status == "pending"
    assert delivery.attempt == 0


def test_delivered_rows_are_ignored(api: TestClient, hook_server: HookServer) -> None:
    make_inbound_with_delivery(api, hook_server.url)
    with Session(api.app.state.engine) as session:
        row = session.query(WebhookDelivery).one()
        row.status = "delivered"
        session.commit()

    processed = run_dispatch(api)

    assert processed == 0
    assert hook_server.requests == []


def test_missing_webhook_config_defers_deliveries(api: TestClient, hook_server: HookServer) -> None:
    """A (temporarily) removed webhook config must not destroy pending deliveries."""
    message_id = insert_message(api, direction="inbound", status="received")
    delivery_id = insert_delivery(api, message_id)  # no webhook configured

    run_dispatch(api)

    assert hook_server.requests == []
    delivery = get_delivery(api, delivery_id)
    assert delivery.status == "pending"  # deferred — delivered once the config is back
    assert delivery.attempt == 0


# --- dispatcher loop + lifespan wiring (ARCHITECTURE §4: task in the api container) ---


def test_dispatcher_loop_delivers_due_delivery(api: TestClient, hook_server: HookServer) -> None:
    _, delivery_id = make_inbound_with_delivery(api, hook_server.url)

    async def _run() -> None:
        async with httpx.AsyncClient() as http_client:
            task = asyncio.create_task(dispatcher_loop(api.app.state.engine, http_client, interval=0.05))
            try:
                for _ in range(100):
                    await asyncio.sleep(0.05)
                    if get_delivery(api, delivery_id).status == "delivered":
                        return
                raise AssertionError("dispatcher loop never delivered the due webhook")
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    asyncio.run(_run())


def test_dispatcher_loop_survives_iteration_errors(tmp_path) -> None:
    from gateway.shared.db import create_db_engine

    broken_engine = create_db_engine(tmp_path / "no-schema.db")  # tables missing on purpose

    async def _run() -> None:
        async with httpx.AsyncClient() as http_client:
            task = asyncio.create_task(dispatcher_loop(broken_engine, http_client, interval=0.02))
            await asyncio.sleep(0.2)  # several failing iterations
            assert not task.done(), "loop must survive iteration errors"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(_run())
    broken_engine.dispose()


def test_lifespan_starts_and_stops_dispatcher_task(api: TestClient) -> None:
    task = api.app.state.webhook_task

    assert isinstance(task, asyncio.Task)
    assert not task.done()


def test_lifespan_cancels_task_on_shutdown(tmp_path) -> None:
    from gateway.api.main import create_app
    from gateway.shared.config import Settings

    settings = Settings(database_path=str(tmp_path / "shutdown.db"))
    with TestClient(create_app(settings)) as client:
        task = client.app.state.webhook_task
        assert not task.done()

    assert task.done(), "webhook task must be stopped after lifespan shutdown"
