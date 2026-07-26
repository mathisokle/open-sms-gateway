"""Operational event log (events table) — surfaced live in the admin panel.

Complements (does not replace) the structured stdout logs; only significant,
low-volume events belong here. Never log secrets, tokens or SMS contents.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from gateway.shared.clock import utc_now_iso
from gateway.shared.models import Event

RETENTION_DAYS = 7


def record_event(session: Session, source: str, level: str, message: str) -> None:
    """Insert an event and prune entries older than the retention window."""
    session.add(Event(ts=utc_now_iso(), source=source, level=level, message=message))
    cutoff = (datetime.now(UTC) - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    session.query(Event).filter(Event.ts < cutoff).delete()
