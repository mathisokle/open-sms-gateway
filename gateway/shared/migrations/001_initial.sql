-- 001: initial schema (single-tenant SMS relay) per docs/SPEC.md §6
CREATE TABLE api_tokens (
    id TEXT PRIMARY KEY,
    token_hash TEXT UNIQUE NOT NULL,
    token_prefix TEXT NOT NULL,
    label TEXT,
    last_used_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    direction TEXT NOT NULL CHECK (direction IN ('outbound','inbound')),
    msisdn TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    segments INTEGER DEFAULT 1,
    error TEXT,
    modem_ref INTEGER,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    delivered_at TEXT,
    received_at TEXT
);

CREATE INDEX idx_messages_created ON messages(created_at);
CREATE INDEX idx_messages_status ON messages(status);
CREATE INDEX idx_messages_msisdn ON messages(msisdn);

CREATE TABLE webhook_deliveries (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id),
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    response_code INTEGER,
    next_retry_at TEXT,
    delivered_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE gateway_status (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE gateway_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE admin_users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX idx_events_ts ON events(ts);
