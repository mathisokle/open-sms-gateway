"""ULID-based IDs with entity prefixes (SPEC §6: msg_, tok_, whd_)."""

from ulid import ULID


def _new_id(prefix: str) -> str:
    return f"{prefix}_{ULID()}"


def message_id() -> str:
    return _new_id("msg")


def token_id() -> str:
    return _new_id("tok")


def delivery_id() -> str:
    return _new_id("whd")
