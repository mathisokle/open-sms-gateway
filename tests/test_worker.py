"""Worker-loop acceptance tests: outbound state machine incl. retries, throttle,
inbound-to-delivery, heartbeat and reconnect backoff (ARCHITECTURE §2)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from gateway.shared import ids
from gateway.shared.configstore import WEBHOOK_SECRET, WEBHOOK_URL, set_config
from gateway.shared.db import create_db_engine, run_migrations
from gateway.shared.models import GatewayStatus, Message, WebhookDelivery
from gateway.worker.main import (
    TokenBucket,
    next_backoff,
    process_inbound,
    process_outbox,
    recover_interrupted_sends,
    write_status,
)
from gateway.worker.modem import (
    DeliveryReport,
    InboundSMS,
    ModemConnectionError,
    ModemError,
    ModemStatus,
    SendResult,
)

NOW_ISO = "2026-07-24T12:00:00Z"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class StubDriver:
    """Minimal ModemDriver implementation for exercising the worker."""

    def __init__(self, fail_sends: int = 0, inbound: list | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail_sends = fail_sends
        self._inbound = list(inbound or [])
        self._reference = 0

    def connect(self) -> None:
        pass

    def send_sms(self, to: str, body: str) -> SendResult:
        if self.fail_sends > 0:
            self.fail_sends -= 1
            raise ModemError("simulated modem failure")
        self.sent.append((to, body))
        self._reference += 1
        return SendResult(segments=1, reference=self._reference)

    def fetch_inbound(self) -> list:
        inbound, self._inbound = self._inbound, []
        return inbound

    def status(self) -> ModemStatus:
        return ModemStatus(connected=True, signal_percent=61, operator="Test", registration="home")


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_db_engine(tmp_path / "test.db")
    run_migrations(eng)
    yield eng
    eng.dispose()


def full_bucket() -> TokenBucket:
    return TokenBucket(per_minute=6, clock=FakeClock())


def make_queued_message(
    engine: Engine,
    to: str = "+41791234567",
    body: str = "hello",
    created_at: str = NOW_ISO,
) -> str:
    message = Message(
        id=ids.message_id(),
        direction="outbound",
        msisdn=to,
        body=body,
        status="queued",
        created_at=created_at,
    )
    with Session(engine) as session:
        session.add(message)
        session.commit()
        return message.id


def configure_webhook(engine: Engine) -> None:
    with Session(engine) as session:
        set_config(session, WEBHOOK_URL, "https://receiver.example/hook")
        set_config(session, WEBHOOK_SECRET, "whsec_test")
        session.commit()


# --- TokenBucket ---


def test_token_bucket_allows_burst_up_to_capacity() -> None:
    bucket = TokenBucket(per_minute=6, clock=FakeClock())

    assert [bucket.try_take() for _ in range(6)] == [True] * 6
    assert bucket.try_take() is False


def test_token_bucket_refills_over_time() -> None:
    clock = FakeClock()
    bucket = TokenBucket(per_minute=6, clock=clock)
    for _ in range(6):
        bucket.try_take()

    clock.now = 9.0  # 6/min = 1 token per 10s: not yet
    assert bucket.try_take() is False
    clock.now = 10.0
    assert bucket.try_take() is True
    assert bucket.try_take() is False


# --- Reconnect backoff ---


def test_reconnect_backoff_doubles_from_5s_and_caps_at_60s() -> None:
    delays = []
    current: float | None = None
    for _ in range(6):
        current = next_backoff(current)
        delays.append(current)

    assert delays == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0]


# --- Outbound state machine ---


def test_queued_message_is_sent(engine: Engine) -> None:
    msg_id = make_queued_message(engine, to="+41791234567")
    driver = StubDriver()

    process_outbox(engine, driver, full_bucket())

    assert driver.sent == [("+41791234567", "hello")]
    with Session(engine) as session:
        message = session.get(Message, msg_id)
        assert message is not None
        assert message.status == "sent"
        assert message.sent_at is not None
        assert message.error is None


def test_outbox_sends_fifo_by_created_at(engine: Engine) -> None:
    make_queued_message(engine, to="+41791111111", created_at="2026-07-24T00:00:02Z")
    make_queued_message(engine, to="+41792222222", created_at="2026-07-24T00:00:01Z")
    driver = StubDriver()

    process_outbox(engine, driver, full_bucket())

    # driver receives them oldest-first despite insertion order
    assert [to for to, _ in driver.sent] == ["+41792222222", "+41791111111"]


def test_modem_error_is_retried_then_sent(engine: Engine) -> None:
    msg_id = make_queued_message(engine)
    driver = StubDriver(fail_sends=2)  # 2 retries allowed -> 3rd attempt succeeds

    process_outbox(engine, driver, full_bucket())

    with Session(engine) as session:
        message = session.get(Message, msg_id)
        assert message is not None
        assert message.status == "sent"
        assert len(driver.sent) == 1


def test_message_fails_after_two_retries(engine: Engine) -> None:
    msg_id = make_queued_message(engine, to="+41799999999")
    driver = StubDriver(fail_sends=3)  # initial attempt + 2 retries all fail

    process_outbox(engine, driver, full_bucket())

    assert driver.sent == []
    with Session(engine) as session:
        message = session.get(Message, msg_id)
        assert message is not None
        assert message.status == "failed"
        assert message.error is not None and "simulated modem failure" in message.error
        assert message.sent_at is None


def test_throttle_leaves_excess_messages_queued(engine: Engine) -> None:
    make_queued_message(engine, to="+41791111111", created_at="2026-07-24T00:00:01Z")
    make_queued_message(engine, to="+41792222222", created_at="2026-07-24T00:00:02Z")
    driver = StubDriver()

    process_outbox(engine, driver, TokenBucket(per_minute=1, clock=FakeClock()))

    assert [to for to, _ in driver.sent] == ["+41791111111"]
    with Session(engine) as session:
        statuses = {m.msisdn: m.status for m in session.query(Message).all()}
    assert statuses == {"+41791111111": "sent", "+41792222222": "queued"}


def test_connection_loss_keeps_message_queued_and_propagates(engine: Engine) -> None:
    msg_id = make_queued_message(engine)

    class DeadDriver(StubDriver):
        def send_sms(self, to: str, body: str) -> SendResult:
            raise ModemConnectionError("serial port gone")

    with pytest.raises(ModemConnectionError):
        process_outbox(engine, DeadDriver(), full_bucket())

    with Session(engine) as session:
        message = session.get(Message, msg_id)
        assert message is not None
        assert message.status == "queued", "connection loss must not burn the message"


def test_send_stores_modem_reference(engine: Engine) -> None:
    msg_id = make_queued_message(engine)

    process_outbox(engine, StubDriver(), full_bucket())

    with Session(engine) as session:
        message = session.get(Message, msg_id)
        assert message is not None
        assert message.modem_ref == 1


# --- Delivery reports (DLR) ---


def send_one(engine: Engine, to: str = "+41791234567") -> str:
    msg_id = make_queued_message(engine, to=to)
    process_outbox(engine, StubDriver(), full_bucket())
    return msg_id


def test_delivery_report_marks_message_delivered(engine: Engine) -> None:
    msg_id = send_one(engine)
    report = DeliveryReport(
        msisdn="+41791234567", reference=1, delivered=True, timestamp="2026-07-24T12:05:00Z"
    )

    process_inbound(engine, StubDriver(inbound=[report]))

    with Session(engine) as session:
        message = session.get(Message, msg_id)
        assert message is not None
        assert message.status == "delivered"
        assert message.delivered_at == "2026-07-24T12:05:00Z"


def test_delivery_report_without_reference_matches_latest_sent(engine: Engine) -> None:
    msg_id = send_one(engine)
    report = DeliveryReport(msisdn="+41791234567", reference=None, delivered=True, timestamp=NOW_ISO)

    process_inbound(engine, StubDriver(inbound=[report]))

    with Session(engine) as session:
        message = session.get(Message, msg_id)
        assert message is not None and message.status == "delivered"


def test_negative_delivery_report_keeps_message_sent(engine: Engine) -> None:
    msg_id = send_one(engine)
    report = DeliveryReport(msisdn="+41791234567", reference=1, delivered=False, timestamp=NOW_ISO)

    process_inbound(engine, StubDriver(inbound=[report]))

    with Session(engine) as session:
        message = session.get(Message, msg_id)
        assert message is not None
        assert message.status == "sent"
        assert message.delivered_at is None


def test_referenced_report_without_match_does_not_hit_fallback(engine: Engine) -> None:
    """A DLR carrying a TP-MR that matches nothing must not mark some other message."""
    msg_id = send_one(engine)  # stored with modem_ref=1
    report = DeliveryReport(msisdn="+41791234567", reference=99, delivered=True, timestamp=NOW_ISO)

    process_inbound(engine, StubDriver(inbound=[report]))

    with Session(engine) as session:
        message = session.get(Message, msg_id)
        assert message is not None
        assert message.status == "sent"
        assert message.delivered_at is None


def test_delivery_report_without_match_is_ignored(engine: Engine) -> None:
    report = DeliveryReport(msisdn="+41790000000", reference=9, delivered=True, timestamp=NOW_ISO)

    process_inbound(engine, StubDriver(inbound=[report]))  # must not raise

    with Session(engine) as session:
        assert session.query(Message).count() == 0


# --- Inbound processing ---


def test_inbound_is_stored_and_delivery_enqueued_when_webhook_configured(engine: Engine) -> None:
    configure_webhook(engine)
    sms = InboundSMS(msisdn="+41791112233", body="reply", received_at="2026-07-24T13:00:00Z")

    process_inbound(engine, StubDriver(inbound=[sms]))

    with Session(engine) as session:
        message = session.query(Message).one()
        assert message.direction == "inbound"
        assert message.status == "received"
        assert message.msisdn == "+41791112233"
        assert message.body == "reply"
        assert message.received_at == "2026-07-24T13:00:00Z"

        delivery = session.query(WebhookDelivery).one()
        assert delivery.message_id == message.id
        assert delivery.status == "pending"
        assert delivery.attempt == 0
        assert delivery.next_retry_at is not None


def test_inbound_without_webhook_config_gets_no_delivery(engine: Engine) -> None:
    sms = InboundSMS(msisdn="+41790000000", body="hi", received_at=NOW_ISO)

    process_inbound(engine, StubDriver(inbound=[sms]))

    with Session(engine) as session:
        message = session.query(Message).one()
        assert message.status == "received"
        assert session.query(WebhookDelivery).count() == 0


# --- Startup recovery ---


def test_interrupted_sending_messages_are_requeued_on_start(engine: Engine) -> None:
    """A crash mid-send leaves status='sending'; startup must return it to the queue."""
    msg_id = make_queued_message(engine)
    with Session(engine) as session:
        message = session.get(Message, msg_id)
        assert message is not None
        message.status = "sending"
        session.commit()

    requeued = recover_interrupted_sends(engine)

    assert requeued == 1
    with Session(engine) as session:
        message = session.get(Message, msg_id)
        assert message is not None
        assert message.status == "queued"


def test_recovery_is_a_noop_without_stuck_messages(engine: Engine) -> None:
    make_queued_message(engine)

    assert recover_interrupted_sends(engine) == 0


# --- Heartbeat / gateway_status ---


def test_write_status_records_heartbeat_and_modem_state(engine: Engine) -> None:
    status = ModemStatus(connected=True, signal_percent=61, operator="Sunrise", registration="home")

    write_status(engine, status)

    with Session(engine) as session:
        rows = {row.key: row.value for row in session.query(GatewayStatus).all()}
    assert rows["modem_connected"] == "1"
    assert rows["signal_percent"] == "61"
    assert rows["operator"] == "Sunrise"
    assert rows["registration"] == "home"
    assert rows["worker_heartbeat"].endswith("Z")


def test_write_status_upserts_on_subsequent_calls(engine: Engine) -> None:
    online = ModemStatus(connected=True, signal_percent=61, operator="Sunrise", registration="home")
    offline = ModemStatus(connected=False, signal_percent=None, operator=None, registration=None)
    write_status(engine, online)
    write_status(engine, offline)

    with Session(engine) as session:
        rows = {row.key: row.value for row in session.query(GatewayStatus).all()}
    assert rows["modem_connected"] == "0"
    assert rows["signal_percent"] == ""
    assert rows["operator"] == ""
    assert rows["registration"] == ""


def test_token_bucket_charges_by_segment_count() -> None:
    clock = FakeClock()
    bucket = TokenBucket(per_minute=6, clock=clock)

    assert bucket.try_take(cost=4) is True  # 6 -> 2 left
    assert bucket.try_take(cost=4) is True  # 2 -> -2 (debt allowed once a token is available)
    assert bucket.try_take(cost=1) is False  # empty/negative -> denied


def test_token_bucket_oversized_message_never_deadlocks() -> None:
    clock = FakeClock()
    bucket = TokenBucket(per_minute=6, clock=clock)

    # a message costing more than capacity still goes through when at least 1 token is present
    assert bucket.try_take(cost=20) is True


class PartialThenFailDriver(StubDriver):
    """Raises a non-retryable PartialSendError to model a multipart send that failed mid-way."""

    def send_sms(self, to: str, body: str):
        from gateway.worker.modem import PartialSendError

        raise PartialSendError("part 2 of 3 failed")


def test_partial_multipart_failure_is_not_retried(engine: Engine) -> None:
    make_queued_message(engine)

    process_outbox(engine, PartialThenFailDriver(), full_bucket())

    with Session(engine) as session:
        message = session.query(Message).one()
        assert message.status == "failed"  # no message-level retry after a partial send
        assert message.error
