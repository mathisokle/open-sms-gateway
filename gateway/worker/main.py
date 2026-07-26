"""Worker entrypoint: synchronous send/receive loop (docs/ARCHITECTURE.md §2).

Every ~5s: (1) drain the outbound queue FIFO with a token-bucket throttle
(MESSAGES_PER_MINUTE), (2) fetch + store inbound and enqueue webhook deliveries,
(3) write modem status + heartbeat to gateway_status. Reconnects with backoff
(5..60s) forever if the modem disappears; queued messages survive in the DB.
Deliberately synchronous — python-gammu blocks.
"""

import logging
import time
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from gateway.shared import ids
from gateway.shared.clock import utc_now_iso
from gateway.shared.config import ConfigError, get_settings
from gateway.shared.configstore import RESTART_WORKER, WEBHOOK_SECRET, WEBHOOK_URL, get_config, set_config
from gateway.shared.db import create_db_engine, run_migrations
from gateway.shared.events import record_event
from gateway.shared.logs import setup_logging
from gateway.shared.models import GatewayStatus, Message, WebhookDelivery
from gateway.worker.modem import (
    DeliveryReport,
    FakeDriver,
    GammuDriver,
    InboundSMS,
    ModemConnectionError,
    ModemDriver,
    ModemStatus,
    PartialSendError,
    SendResult,
)

logger = logging.getLogger("gateway.worker")

POLL_INTERVAL_SECONDS = 5.0
MAX_SEND_ATTEMPTS = 3  # initial attempt + 2 automatic retries (SPEC §4.1.3)
BACKOFF_INITIAL_SECONDS = 5.0
BACKOFF_CAP_SECONDS = 60.0

DISCONNECTED_STATUS = ModemStatus(connected=False, signal_percent=None, operator=None, registration=None)


class TokenBucket:
    """Send throttle: starts full, refills at per_minute/60 tokens per second."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._rate = per_minute / 60.0
        self._capacity = float(per_minute)
        self._tokens = float(per_minute)
        self._clock = clock
        self._last_refill = clock()

    def try_take(self, cost: float = 1.0) -> bool:
        """Take `cost` tokens (one per SMS segment). Allowed whenever at least one token
        is present; the balance may go negative so an oversized message never deadlocks
        the queue, it just pauses sending until the bucket refills."""
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._last_refill) * self._rate)
        self._last_refill = now
        if self._tokens >= 1.0:
            self._tokens -= cost
            return True
        return False


def next_backoff(current: float | None) -> float:
    """Reconnect delay sequence: 5, 10, 20, 40, 60, 60, ... seconds."""
    if current is None:
        return BACKOFF_INITIAL_SECONDS
    return min(current * 2.0, BACKOFF_CAP_SECONDS)


def _send_with_retries(driver: ModemDriver, to: str, body: str, max_attempts: int) -> SendResult:
    last_error: Exception | None = None
    for _ in range(max_attempts):
        try:
            return driver.send_sms(to, body)
        except (ModemConnectionError, PartialSendError):
            # connection loss: reconnect+requeue the whole message.
            # partial multipart send: retrying would re-send the parts that already went
            # out — fail the message instead of duplicating segments at the recipient.
            raise
        except Exception as exc:  # message-level modem failure -> retry
            last_error = exc
    assert last_error is not None
    raise last_error


def process_outbox(
    engine: Engine,
    driver: ModemDriver,
    bucket: TokenBucket,
    *,
    max_attempts: int = MAX_SEND_ATTEMPTS,
) -> None:
    """queued -> sending -> sent/failed (FIFO, throttled)."""
    with Session(engine) as session:
        queued = (
            session.query(Message)
            .filter(Message.direction == "outbound", Message.status == "queued")
            .order_by(Message.created_at, Message.id)
            .all()
        )
        for message in queued:
            # charge one token per segment: a multipart SMS is several provider submits,
            # so the throttle stays honest as a spam-filter / cost guard
            if not bucket.try_take(cost=message.segments or 1):
                break
            message.status = "sending"
            session.commit()
            try:
                result = _send_with_retries(driver, message.msisdn, message.body, max_attempts)
            except ModemConnectionError:
                message.status = "queued"  # keep it for after the reconnect
                session.commit()
                raise
            except Exception as exc:
                message.status = "failed"
                message.error = str(exc) or exc.__class__.__name__
                session.commit()
                continue
            message.status = "sent"
            message.sent_at = utc_now_iso()
            message.segments = result.segments
            message.modem_ref = result.reference
            session.commit()
            logger.info("message %s sent (modem_ref=%s)", message.id, result.reference)


def _apply_delivery_report(session: Session, report: DeliveryReport) -> None:
    """Mark the matching sent message as delivered.

    Match strictly by TP-MR when the report carries one — a referenced report that
    matches nothing must never fall through to "latest sent", or it would mark a
    different message (e.g. multipart sends store only the last part's reference).
    """
    if not report.delivered:
        return  # negative/interim reports don't change state
    query = session.query(Message).filter(
        Message.direction == "outbound",
        Message.msisdn == report.msisdn,
        Message.status == "sent",
        Message.delivered_at.is_(None),
    )
    if report.reference is not None:
        message = query.filter(Message.modem_ref == report.reference).order_by(Message.sent_at.desc()).first()
    else:
        message = query.order_by(Message.sent_at.desc()).first()  # no TP-MR: best effort
    if message is None:
        logger.info(
            "delivery report without matching message (ref=%s, msisdn suffix …%s)",
            report.reference,
            report.msisdn[-4:],
        )
        return
    message.status = "delivered"
    message.delivered_at = report.timestamp or utc_now_iso()


def process_inbound(engine: Engine, driver: ModemDriver) -> None:
    """Store inbound SMS (+ webhook delivery if configured); apply delivery reports.

    Inbound texts are committed first, in their own transaction, before delivery reports
    are applied. A DLR whose target row was purged concurrently raises StaleDataError on
    commit — isolating it keeps that from rolling back freshly received (already-deleted-
    from-the-modem) inbound SMS.
    """
    events = driver.fetch_inbound()
    if not events:
        return
    texts = [event for event in events if isinstance(event, InboundSMS)]
    reports = [event for event in events if isinstance(event, DeliveryReport)]
    logger.info("inbound events: %d total, %d delivery report(s)", len(events), len(reports))

    with Session(engine) as session:
        webhook_configured = bool(get_config(session, WEBHOOK_URL)) and bool(
            get_config(session, WEBHOOK_SECRET)
        )
        for sms in texts:
            message = Message(
                id=ids.message_id(),
                direction="inbound",
                msisdn=sms.msisdn,
                body=sms.body,
                status="received",
                segments=sms.segments,
                created_at=utc_now_iso(),
                received_at=sms.received_at,
            )
            session.add(message)
            if webhook_configured:
                now = utc_now_iso()
                session.add(
                    WebhookDelivery(
                        id=ids.delivery_id(),
                        message_id=message.id,
                        attempt=0,
                        status="pending",
                        next_retry_at=now,
                        created_at=now,
                    )
                )
        session.commit()

    if not reports:
        return
    with Session(engine) as session:
        try:
            for report in reports:
                logger.info("delivery report: ref=%s delivered=%s", report.reference, report.delivered)
                _apply_delivery_report(session, report)
            session.commit()
        except StaleDataError:
            logger.warning("delivery report target changed mid-commit; skipping reports this cycle")


def write_status(engine: Engine, status: ModemStatus) -> None:
    """Upsert modem status + worker heartbeat into gateway_status (SPEC §6)."""
    now = utc_now_iso()
    values = {
        "worker_heartbeat": now,
        "modem_connected": "1" if status.connected else "0",
        "signal_percent": "" if status.signal_percent is None else str(status.signal_percent),
        "operator": status.operator or "",
        "registration": status.registration or "",
        "modem_own_number": status.own_number or "",
    }
    with Session(engine) as session:
        for key, value in values.items():
            session.merge(GatewayStatus(key=key, value=value, updated_at=now))
        session.commit()


def recover_interrupted_sends(engine: Engine) -> int:
    """Requeue messages stuck in 'sending' after a crash mid-send (at-least-once).

    The SMS may or may not have left the modem before the crash — requeueing can
    produce a duplicate at the recipient, but silently losing the message is worse.
    """
    with Session(engine) as session:
        stuck = (
            session.query(Message).filter(Message.direction == "outbound", Message.status == "sending").all()
        )
        for message in stuck:
            message.status = "queued"
        if stuck:
            record_event(
                session, "worker", "warning", f"requeued {len(stuck)} message(s) interrupted mid-send"
            )
        session.commit()
    return len(stuck)


def restart_requested(engine: Engine) -> bool:
    """True once when the admin requested a worker restart; clears the flag."""
    with Session(engine) as session:
        if not get_config(session, RESTART_WORKER):
            return False
        set_config(session, RESTART_WORKER, None)
        record_event(session, "worker", "info", "worker restarting (admin request)")
        session.commit()
    return True


def _record_event_safe(engine: Engine, source: str, level: str, message: str) -> None:
    try:
        with Session(engine) as session:
            record_event(session, source, level, message)
            session.commit()
    except Exception:  # the DB itself may be the failing part — never crash on logging
        logger.warning("could not record event")


def run() -> None:
    setup_logging()
    settings = get_settings()
    engine = create_db_engine(settings.database_path)
    run_migrations(engine)
    requeued = recover_interrupted_sends(engine)
    if requeued:
        logger.warning("requeued %d message(s) stuck in 'sending' from a previous run", requeued)
    driver: ModemDriver
    if settings.modem_fake:
        driver = FakeDriver(Path(settings.database_path).parent)
    else:
        driver = GammuDriver()
    bucket = TokenBucket(settings.messages_per_minute)
    backoff: float | None = None
    connected = False
    while True:
        try:
            if restart_requested(engine):
                logger.info("restart requested via admin — exiting (docker restarts us)")
                raise SystemExit(0)
            if not connected:
                driver.connect()
                connected = True
                backoff = None
                logger.info("modem connected (fake=%s)", settings.modem_fake)
                _record_event_safe(engine, "worker", "info", "modem connected")
                # loop head is a safe point (no send in flight): reclaim any message left
                # in 'sending' by a prior iteration that died after send but before commit
                recover_interrupted_sends(engine)
            process_outbox(engine, driver, bucket)
            process_inbound(engine, driver)
            write_status(engine, driver.status())
            time.sleep(POLL_INTERVAL_SECONDS)
        except Exception as exc:
            connected = False
            backoff = next_backoff(backoff)
            logger.exception("modem loop error, reconnecting in %.0fs", backoff)
            _record_event_safe(
                engine, "worker", "error", f"modem loop error ({exc.__class__.__name__}), reconnecting"
            )
            try:
                write_status(engine, DISCONNECTED_STATUS)
            except Exception:  # the DB may be the failing part — keep the backoff loop alive
                logger.warning("could not write disconnected status")
            time.sleep(backoff)


if __name__ == "__main__":
    try:
        run()
    except ConfigError as exc:
        # one readable log line instead of a pydantic traceback; restarting cannot fix
        # a bad .env, so exit non-zero and let the operator read the reason
        logger.error("configuration error: %s", exc)
        raise SystemExit(2) from None
