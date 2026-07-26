"""SQLAlchemy models for all tables defined in docs/SPEC.md §6.

The DDL source of truth is gateway/shared/migrations/; these models mirror it 1:1
(verified by tests/test_db.py::test_models_match_migrated_schema). All timestamps
are TEXT columns holding UTC ISO 8601 strings.
"""

from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(unique=True)
    token_prefix: Mapped[str]
    label: Mapped[str | None]
    last_used_at: Mapped[str | None]
    revoked_at: Mapped[str | None]
    created_at: Mapped[str]


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("direction IN ('outbound','inbound')", name="ck_messages_direction"),
        Index("idx_messages_created", "created_at"),
        Index("idx_messages_status", "status"),
        Index("idx_messages_msisdn", "msisdn"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    direction: Mapped[str]
    msisdn: Mapped[str]
    body: Mapped[str]
    status: Mapped[str]
    segments: Mapped[int] = mapped_column(server_default=text("1"))
    error: Mapped[str | None]
    modem_ref: Mapped[int | None]
    created_at: Mapped[str]
    sent_at: Mapped[str | None]
    delivered_at: Mapped[str | None]
    received_at: Mapped[str | None]


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"))
    attempt: Mapped[int]
    status: Mapped[str]
    response_code: Mapped[int | None]
    next_retry_at: Mapped[str | None]
    delivered_at: Mapped[str | None]
    created_at: Mapped[str]


class GatewayStatus(Base):
    __tablename__ = "gateway_status"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
    updated_at: Mapped[str]


class GatewayConfig(Base):
    __tablename__ = "gateway_config"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
    updated_at: Mapped[str]


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    created_at: Mapped[str]


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("idx_events_ts", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[str]
    source: Mapped[str]
    level: Mapped[str]
    message: Mapped[str]
