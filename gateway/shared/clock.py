"""UTC timestamp helper — all DB timestamps are UTC ISO 8601 strings (SPEC §6)."""

from datetime import UTC, datetime


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
