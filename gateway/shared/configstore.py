"""Runtime key-value config (gateway_config table): webhook target, editable via admin.

Unlike env Settings, these values change at runtime without a restart; api and
worker both read them fresh from the shared DB.
"""

from sqlalchemy.orm import Session

from gateway.shared.clock import utc_now_iso
from gateway.shared.models import GatewayConfig

WEBHOOK_URL = "webhook_url"
WEBHOOK_SECRET = "webhook_secret"
RESTART_WORKER = "restart_worker"  # set by admin; worker sees it, clears it and exits
OWN_NUMBER = "own_number"  # the SIM's MSISDN, display-only (modem cannot read it reliably)


def get_config(session: Session, key: str) -> str | None:
    row = session.get(GatewayConfig, key)
    return row.value if row is not None else None


def set_config(session: Session, key: str, value: str | None) -> None:
    if value is None:
        row = session.get(GatewayConfig, key)
        if row is not None:
            session.delete(row)
        return
    session.merge(GatewayConfig(key=key, value=value, updated_at=utc_now_iso()))
