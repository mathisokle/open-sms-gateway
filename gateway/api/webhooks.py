"""Webhook dispatcher: pushes inbound SMS to the configured webhook URL (SPEC §4.3).

Runs as an asyncio task inside the api container (ARCHITECTURE §4) so the modem
worker is never blocked by a slow receiver. Target URL + secret live in
gateway_config (admin-editable at runtime).
"""

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from gateway.shared.clock import utc_now_iso
from gateway.shared.configstore import WEBHOOK_SECRET, WEBHOOK_URL, get_config
from gateway.shared.events import record_event
from gateway.shared.models import Message, WebhookDelivery

logger = logging.getLogger("gateway.webhooks")

REQUEST_TIMEOUT_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 10.0
# SPEC §4.3 retry chain; a delivery whose attempt count exceeds the chain is failed.
BACKOFF_DELAYS_SECONDS = [60, 300, 1800, 7200, 21600]  # 1m, 5m, 30m, 2h, 6h
MAX_RETRIES = len(BACKOFF_DELAYS_SECONDS)


def sign_payload(secret: str, raw_body: bytes) -> str:
    """X-Gateway-Signature value: sha256=<hex hmac_sha256(secret, raw_body)>."""
    return "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def build_payload(message: Message) -> dict:
    """Webhook JSON payload for an inbound message (SPEC §4.3)."""
    return {
        "id": message.id,
        "type": "sms.received",
        "from": message.msisdn,
        "to": "gateway",
        "body": message.body,
        "received_at": message.received_at,
    }


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


async def process_due_deliveries(
    engine: Engine,
    client: httpx.AsyncClient,
    *,
    now: datetime | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> int:
    """Attempt every due pending delivery once; return how many were attempted.

    2xx -> delivered. Anything else (incl. timeout/connection error) -> attempt++,
    next_retry_at per backoff chain, failed once the chain is exhausted.
    """

    # an injected `now` (tests) stays fixed; in production each read is fresh so a slow
    # round doesn't shrink the per-delivery backoff measured below
    def _now() -> datetime:
        return now or datetime.now(UTC)

    now_dt = _now()
    processed = 0
    with Session(engine) as session:
        due = (
            session.query(WebhookDelivery)
            .filter(WebhookDelivery.status == "pending", WebhookDelivery.next_retry_at <= _iso(now_dt))
            .order_by(WebhookDelivery.next_retry_at, WebhookDelivery.id)
            .all()
        )
        if not due:
            return 0
        webhook_url = get_config(session, WEBHOOK_URL)
        webhook_secret = get_config(session, WEBHOOK_SECRET)
        if not webhook_url or not webhook_secret:
            # config removed (possibly temporarily) — defer instead of failing deliveries
            logger.warning("webhook not configured, deferring %d due deliveries", len(due))
            return 0
        for delivery in due:
            message = session.get(Message, delivery.message_id)
            if message is None:
                delivery.status = "failed"
                session.commit()
                continue
            raw_body = json.dumps(build_payload(message), ensure_ascii=False, separators=(",", ":")).encode()
            response_code: int | None = None
            try:
                response = await client.post(
                    webhook_url,
                    content=raw_body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Gateway-Signature": sign_payload(webhook_secret, raw_body),
                        "X-Gateway-Delivery": delivery.id,
                    },
                    timeout=timeout,
                )
                response_code = response.status_code
                success = 200 <= response.status_code < 300
            except httpx.HTTPError as exc:
                logger.warning("webhook delivery %s failed: %s", delivery.id, exc.__class__.__name__)
                success = False
            delivery.attempt += 1
            delivery.response_code = response_code
            if success:
                delivery.status = "delivered"
                delivery.delivered_at = utc_now_iso()
            elif delivery.attempt > MAX_RETRIES:
                delivery.status = "failed"
                record_event(session, "webhook", "warning", f"delivery {delivery.id} failed permanently")
            else:
                delay = BACKOFF_DELAYS_SECONDS[delivery.attempt - 1]
                # measure the backoff from now, not the round start: a slow round (many
                # deliveries × up to 10s timeout) must not shrink the effective delays
                delivery.next_retry_at = _iso(_now() + timedelta(seconds=delay))
            session.commit()
            processed += 1
    return processed


async def dispatcher_loop(
    engine: Engine,
    client: httpx.AsyncClient,
    *,
    interval: float = POLL_INTERVAL_SECONDS,
) -> None:
    """Poll due deliveries forever; one broken iteration never kills the loop."""
    while True:
        try:
            await process_due_deliveries(engine, client)
        except Exception:
            logger.exception("webhook dispatcher iteration failed")
        await asyncio.sleep(interval)
