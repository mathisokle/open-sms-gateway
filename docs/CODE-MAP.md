# Module map

A map for finding your way into the code: what each module is responsible for, and which tests
cover it. For *what* the gateway does, `docs/SPEC.md` is authoritative; for *how* the pieces fit
together, see `docs/ARCHITECTURE.md`.

| Area | Modules | Tests |
|---|---|---|
| [Foundation](#foundation-configuration-database-models) | `shared/config.py`, `db.py`, `models.py`, `ids.py`, `migrations/` | `test_config.py`, `test_db.py`, `test_ids.py` |
| [Modem worker](#modem-worker) | `worker/main.py`, `worker/modem.py` | `test_worker.py`, `test_modem_fake.py` |
| [REST API v1](#rest-api-v1) | `api/main.py`, `auth.py`, `routes_api.py`, `ratelimit.py` | `test_auth.py`, `test_api_messages.py`, `test_healthz.py`, `test_ratelimit.py` |
| [Webhook dispatcher](#webhook-dispatcher) | `api/webhooks.py` | `test_webhooks.py` |
| [Admin panel](#admin-panel) | `api/routes_admin.py`, `passwords.py`, `templates/`, `static/` | `test_admin_*.py`, `test_composer_js.py` |
| [Operator manual](#operator-manual) | `shared/markdown.py`, `api/docs_pages.py` | `test_markdown.py`, `test_admin_docs.py` |
| [Cross-cutting](#cross-cutting) | `shared/logs.py`, `events.py`, `configstore.py`, `clock.py`, `sms.py` | `test_logs.py` |

## Foundation: configuration, database, models

- `gateway/shared/config.py` — pydantic-settings; every environment variable from SPEC §7, with
  required fields, minimum lengths for `SECRET_KEY` and `ADMIN_PASSWORD`, and timezone validation
  at startup. A bad configuration stops the container rather than running weakly secured.
- `gateway/shared/db.py` — the SQLAlchemy engine (synchronous), connection pragmas (`WAL`,
  `busy_timeout`, `foreign_keys`), and the migration runner. Migrations are numbered SQL scripts in
  `gateway/shared/migrations/`, applied at startup and recorded in `schema_version`.
  **Additive only** — a released migration is never edited; changes go into a new file.
- `gateway/shared/models.py` — all tables from SPEC §6, mapped 1:1 with the DDL.
- `gateway/shared/ids.py` — ULID-based identifiers with a type prefix (`msg_`, `tok_`, `whd_`),
  sortable by creation time, which is what the keyset pagination relies on.

Covered by: schema creation on an empty database, an idempotent second start, active pragmas, and
the models matching the migrated schema.

## Modem worker

- `gateway/worker/modem.py` — the `ModemDriver` protocol and its two implementations:
  `GammuDriver` (real hardware, `python-gammu` on `/dev/modem`) and `FakeDriver`
  (`MODEM_FAKE=1`; inbound injected via `/data/fake_inbound.jsonl`, sent messages logged to
  `/data/fake_sent.jsonl`). Error classes distinguish a retryable `ModemError` from a
  `ModemConnectionError` (reconnect and requeue) and a `PartialSendError` (never resend — that
  would duplicate).
- `gateway/worker/main.py` — the loop described in ARCHITECTURE §2: sending against a segment token
  bucket, receiving, delivery reports, heartbeat, reconnect backoff, and requeueing sends that a
  crash interrupted.

Covered by: the `queued → sending → sent | failed` state machine including retries, the throttle,
a partial multipart failure that produces no duplicates, inbound messages creating
`webhook_deliveries`, and a delivery report setting `delivered`.

## REST API v1

- `gateway/api/main.py` — app factory, lifespan, uniform error format, and `/healthz` per SPEC §4.6.
- `gateway/api/auth.py` — bearer token to token record via SHA-256 hash, with a throttled
  `last_used_at` update.
- `gateway/api/routes_api.py` — every endpoint from SPEC §5, including keyset cursor pagination and
  filter validation.
- `gateway/api/ratelimit.py` — the optional shared rate limit (`RATE_LIMIT_PER_MINUTE`, off by default).

Covered by: auth (valid, invalid, revoked, missing header), `POST` validating E.164 and the segment
limit before enqueueing, and pagination staying stable across identical timestamps.

## Webhook dispatcher

- `gateway/api/webhooks.py` — an asyncio task started by the FastAPI lifespan; httpx with a 10 s
  timeout, the HMAC signature, the backoff chain from SPEC §4.3, and one row per attempt in
  `webhook_deliveries`.

Covered by: 2xx marking `delivered`; 500 retrying with the correct `next_retry_at`; `failed` once
the chain is exhausted; the signature verifying; a timeout counting as a failed attempt; and a
missing webhook configuration deferring instead of failing.

## Admin panel

- `gateway/api/routes_admin.py` with `templates/` — session login, dashboard with live tiles and
  inline SVG charts, message browser, chat view, token management (one-time plaintext display),
  webhook log with manual retry, event log, admin users, and settings.
- `gateway/api/passwords.py` — PBKDF2-HMAC-SHA256 hashing for panel-managed admin users.
- Jinja2 and htmx, no build step, one `static/admin.css`, and `static/composer.js` for the live
  SMS length counter (which mirrors `gateway/shared/sms.py`).

Covered by: every `/admin` route redirecting without a session, the login throttle, token plaintext
appearing exactly once in a response, the CRUD paths, and UI smoke tests through `TestClient`.

## Operator manual

- `gateway/shared/markdown.py` — a deliberately small, purpose-built renderer (see ARCHITECTURE §5).
- `gateway/api/docs_pages.py` — the page registry, title and summary extraction, link rewriting and
  an mtime cache; serves `/admin/docs`.

Covered by: the renderer subset, escaping and link safety; every registered page rendering; no dead
links or anchors anywhere in the manual; and slugs being unable to escape the docs directory.

## Cross-cutting

- `gateway/shared/logs.py` — one structured JSON logger setup. Never secrets, tokens or SMS contents.
- `gateway/shared/events.py` — the operational event log shown under Logs (7-day retention).
- `gateway/shared/configstore.py` — runtime settings in `gateway_config` (webhook URL and secret,
  gateway number, worker restart flag).
- `gateway/shared/clock.py` — UTC timestamps in ISO 8601, injectable so tests stay deterministic.
- `gateway/shared/sms.py` — alphabet detection (GSM-7 vs UCS-2), segment counting and limits.
