# Specification

The behavioural contract of the gateway: what it does, what it stores, and what it exposes. When
this document and any other disagree, **this one wins** — including against the code, which is then
the thing to fix.

Runs on any Linux host with a free USB port (arm64 or x86-64) · reference target: Raspberry Pi 3
(1 GB RAM) · Language: Python 3.11+

Section numbers here are referenced from code comments and tests (`SPEC §6`, `SPEC §4.6`, …), so
they are stable. Adding a subsection is fine; renumbering an existing one is not.

## 1. Purpose and scope

A self-hosted SMS relay: one Linux host, one SIM7600E-H USB dongle, one SIM card, one phone
number. It offers

- a **REST API** for sending SMS and for reading message history, authenticated with bearer tokens,
- **inbound delivery** either by polling that API or by a signed **webhook** to a URL of the
  operator's choosing,
- an **admin panel** for the operator, including a built-in copy of the operator manual.

It is **single-tenant** by design. There is no tenant model, no per-client isolation and no routing
between numbers, because a single SIM cannot serve more than one identity anyway. That assumption
removes an entire dimension from the schema, the API and the UI.

Explicitly out of scope: bulk messaging, MMS, voice, USSD, data connectivity, and multiple modems.

The hardware environment the gateway assumes is described in [hardware.md](hardware.md): the
dongle's AT port is USB interface 02, addressed through a stable `/dev/serial/by-id/...-if02-port0`
path; the SIM has no PIN; and ModemManager is disabled so nothing else holds the port.

## 2. Terms

| Term | Meaning |
|---|---|
| Message | One SMS. `direction` is `outbound` or `inbound`, and it carries a status through its lifetime. |
| Segment | One physical SMS transmission. A long message is split into several; operators count these, so the gateway does too. |
| API token | Bearer credential, `sms_` plus 32 hex characters, stored server-side only as a SHA-256 hash. Several may exist in parallel; each is revocable individually. |
| Webhook | Optional push target for inbound SMS: a URL plus a signing secret, both configurable at runtime. |
| Delivery | One inbound message's push to the webhook, covering every retry attempt. It carries an attempt counter, the response code of the last attempt, and an id that stays stable across retries. |
| Operator | The human running the gateway — the only administrator. Also, in modem context, the mobile network. |

## 3. Roles and authentication

**API client.** Sends `Authorization: Bearer sms_...` on every `/api/v1/*` request. The token is
looked up by the SHA-256 hash of the presented value. Missing, unknown, malformed and revoked tokens
are all `401`, and are indistinguishable from one another to the caller. `last_used_at` is updated on
use, throttled so that a polling client does not cause a write per request.

**Administrator (web).** Session login at `/admin/login`, establishing a cookie signed with
`itsdangerous`: `HttpOnly`, `SameSite=Lax`, valid 12 hours, and marked `Secure` when
`SESSION_COOKIE_SECURE=1`. There are two sources of admin identity:

1. The `ADMIN_USER` / `ADMIN_PASSWORD` pair from the environment. This account is a **permanent
   fallback**: it cannot be deleted in the panel, so no user-management action can lock the operator
   out.
2. Optional additional accounts in `admin_users`, managed in the panel, with PBKDF2-HMAC-SHA256
   password hashes. Deleting such an account ends its session immediately.

Login accepts at most **10 attempts per minute**, answering `429` beyond that. Every `/admin` route
except the login page requires a valid session and redirects to `/admin/login` without one.

**Token visibility.** A token's plaintext is shown **exactly once**, at creation. Afterwards only its
label and prefix (`sms_ab12…`) remain, because only a hash was stored. There is no recovery path;
the remedy for a lost token is to revoke it and create another.

## 4. Functional requirements

### 4.1 Sending

1. `POST /api/v1/messages` validates the request and creates a message with `status=queued`. The
   queue is the `messages` table — there is no broker.
2. The worker drains the queue **FIFO**, subject to a throttle of `MESSAGES_PER_MINUTE`
   (default 6). The throttle is a token bucket that charges **segments, not messages**, matching how
   operators count submissions: a three-part message consumes three tokens.
3. Status progresses `queued → sending → sent`, or `→ failed` with a human-readable reason in
   `error`. Modem errors are retried at most twice before failing.
4. Long bodies are sent as concatenated SMS. The alphabet is detected automatically — GSM-7 where
   possible, UCS-2 otherwise — and the resulting `segments` count is stored. A body exceeding
   **10 segments** is rejected with `422`.
5. Recipients are E.164, validated as `^\+[1-9][0-9]{6,14}$`. Anything else is `422`.
6. A send interrupted by a crash is recovered: messages left in `sending` return to `queued` at the
   next startup or reconnect. This is deliberately at-least-once — a duplicate is better than a
   silently lost message — **except** for a multipart send that partly succeeded, which is never
   retried because that would duplicate the delivered parts.

The message conventions the operator is encouraged to follow are documented in
[manual/sms-format.md](manual/sms-format.md). They are a writing convention, not a rule the gateway
enforces beyond the limits above.

### 4.2 Receiving

1. The worker polls the modem roughly every 5 seconds, reads new messages, and stores them with
   `status=received`.
2. Multipart messages are reassembled by their UDH before being stored, so one arriving text becomes
   one row regardless of how many segments carried it.
3. Messages are deleted from the modem once stored, so the SIM's message store cannot fill up.
4. If a webhook is configured, exactly one delivery job is created per inbound message (§4.3).
   Independently of that, every message is retrievable through the API (§4.4).

### 4.3 Webhook

Configured at runtime in the panel: a `webhook_url` plus an automatically generated, rotatable
`webhook_secret`. An empty URL means polling-only, and no delivery rows are created.

The payload is a JSON POST:

```json
{
  "id": "msg_01J8ZQ...",
  "type": "sms.received",
  "from": "+41791234567",
  "to": "gateway",
  "body": "Reply text",
  "received_at": "2026-01-15T18:00:00Z"
}
```

Headers:

| Header | Meaning |
|---|---|
| `X-Gateway-Signature` | `sha256=<hex>` — HMAC-SHA256 over the **raw** request body, keyed with `webhook_secret`. |
| `X-Gateway-Delivery` | The delivery id, stable across all retries of the same message. Use it as an idempotency key. |

Success is any HTTP 2xx within 10 seconds. Otherwise the delivery is retried after **1 min, 5 min,
30 min, 2 h, 6 h**, and then marked `failed`. Failed deliveries remain visible in the panel and can
be retried by hand.

One `webhook_deliveries` row tracks the whole chain: it carries an attempt counter and the response
code of the **most recent** attempt. Individual attempts are not kept as separate rows, so there is
no per-attempt history on the gateway side — use `X-Gateway-Delivery` together with the attempt
counter if you need one on yours.

The dispatcher runs inside the api container, not the worker, so a slow or hanging receiver can
never block sending or receiving.

### 4.4 Polling

`GET /api/v1/messages` covers both directions and serves as the polling interface:

```
GET /api/v1/messages?direction=inbound&since=2026-01-15T18:00:00Z&limit=100
```

Pagination is a keyset cursor over `created_at` plus `id`, which keeps results stable even when many
messages share a timestamp — an offset would skip or repeat rows there.

### 4.5 Admin panel

Served at `/admin`, server-rendered with Jinja2 and htmx. No SPA framework, no build step, no
external CDN: it must work on a network with no internet access.

| Area | Contents |
|---|---|
| Dashboard | Modem status (connection, signal, operator, registration, last worker activity), queue depth, today's and lifetime counters, outbound status breakdown, 24-hour and 7-day activity charts. |
| Chats | One thread per counterparty number, with a reply box and a live segment counter. |
| Messages | Filterable list (direction, status, number) and a detail view with the full status history. |
| API Tokens | Create — with the one-time plaintext display — and revoke. |
| Webhook Log | Recent delivery attempts with response codes, and manual retry. |
| Logs | The operational event log from worker, webhook dispatcher and admin actions, retained 7 days. |
| Users | Additional admin accounts. The environment admin is the permanent fallback and is not listed. |
| Settings | Webhook URL and secret (view, set, clear, rotate), gateway number, test SMS, data cleanup, database backup, container restarts, and the effective configuration. |
| Docs | The operator manual from `docs/manual/*.md`, rendered offline. |

The panel is English-only and has no i18n layer: one operator means one language, one set of strings,
and no drift between translations.

### 4.6 System behaviour

`GET /healthz` requires no authentication:

```json
{
  "status": "ok",
  "modem": {"connected": true, "signal_percent": 68, "operator": "Example Mobile"},
  "queue_depth": 0,
  "worker_seen_at": "2026-01-15T18:00:00Z"
}
```

It answers **503** with `"status": "degraded"` when the worker heartbeat is older than **120
seconds**. This is the health signal: the api container can be perfectly healthy while the worker —
the part that actually moves messages — is gone.

Worker resilience: if the serial port disappears, the worker reconnects forever with a 5→60 s
backoff, and queued messages simply wait in the database. Both containers run with
`restart: unless-stopped`.

All timestamps are stored in UTC as ISO 8601 strings. Only the admin panel localises them, using
`TZ`.

## 5. REST API v1

Base `http://<host>:8080/api/v1` · bearer auth on every endpoint · errors as
`{"error": {"code": "...", "message": "..."}}`.

| Method and path | Purpose | Request | Response |
|---|---|---|---|
| `POST /messages` | Enqueue an SMS | `{"to": "+41791234567", "body": "Text"}` | `201 {"id": "msg_...", "status": "queued", "segments": 1, "created_at": "..."}` |
| `GET /messages` | List, both directions | Query: `direction`, `since`, `until`, `to`, `from`, `status`, `limit` (≤200, default 50), `cursor` | `200 {"data": [...], "next_cursor": "..."}` |
| `GET /messages/{id}` | One message | — | `200 {message}` or `404` |

The message representation:

```json
{
  "id": "msg_01J8ZQ...",
  "direction": "outbound",
  "to": "+41791234567",
  "from": null,
  "body": "Text",
  "status": "sent",
  "segments": 1,
  "error": null,
  "created_at": "2026-01-15T18:00:00Z",
  "sent_at": "2026-01-15T18:00:05Z",
  "delivered_at": null,
  "received_at": null
}
```

`to` and `from` are projections of the stored `msisdn`: for an outbound message the counterparty is
the recipient, for an inbound one the sender.

Status codes: `401` (authentication), `404` (unknown id), `422` (validation), `429` (rate limit, only
when `RATE_LIMIT_PER_MINUTE` is non-zero), `503` (`/healthz` when degraded).

`delivered` and `delivered_at` require the modem *and* the network to produce SMS status reports.
Many do not, so this is **best effort**: without reports a successfully submitted message stays at
`sent` forever, and that is not a fault.

**No OpenAPI document is served.** The default docs UI fetches JS and CSS from a CDN, which breaks
the offline requirement, and `openapi.json` would publish the entire admin surface to
unauthenticated callers. The reference is [manual/rest-api.md](manual/rest-api.md).

## 6. Database

SQLite in WAL mode, one file at `/data/gateway.db`.

```sql
api_tokens(id TEXT PK,                    -- "tok_" + ULID
           token_hash TEXT UNIQUE NOT NULL,   -- SHA-256 of the plaintext; the plaintext is never stored
           token_prefix TEXT NOT NULL,        -- for identification in the UI
           label TEXT, last_used_at TEXT, revoked_at TEXT,
           created_at TEXT NOT NULL)

messages(id TEXT PK,                      -- "msg_" + ULID
         direction TEXT NOT NULL CHECK (direction IN ('outbound','inbound')),
         msisdn TEXT NOT NULL,             -- the counterparty: recipient if outbound, sender if inbound
         body TEXT NOT NULL,
         status TEXT NOT NULL,             -- queued|sending|sent|delivered|failed|received
         segments INTEGER DEFAULT 1,
         error TEXT,                       -- set when status = failed
         modem_ref INTEGER,                -- TP-MR of the (last) part; matches delivery reports
         created_at TEXT NOT NULL, sent_at TEXT, delivered_at TEXT, received_at TEXT)
  -- indexes: (created_at), (status), (msisdn)

webhook_deliveries(id TEXT PK,            -- "whd_" + ULID; sent as X-Gateway-Delivery
                   message_id TEXT NOT NULL REFERENCES messages(id),
                   attempt INTEGER NOT NULL,
                   status TEXT NOT NULL,   -- pending|delivered|failed
                   response_code INTEGER, next_retry_at TEXT,
                   delivered_at TEXT, created_at TEXT NOT NULL)

gateway_status(key TEXT PK, value TEXT NOT NULL, updated_at TEXT NOT NULL)
  -- written by the worker, read by the api: worker_heartbeat, modem_connected,
  -- signal_percent, operator, registration, modem_own_number

gateway_config(key TEXT PK, value TEXT NOT NULL, updated_at TEXT NOT NULL)
  -- runtime settings, editable in the panel: webhook_url, webhook_secret,
  -- own_number, restart_worker (a flag the worker reads and clears)

admin_users(id TEXT PK, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,   -- pbkdf2$<iterations>$<salt>$<hex>
            created_at TEXT NOT NULL)

events(id INTEGER PK AUTOINCREMENT, ts TEXT NOT NULL, source TEXT NOT NULL,
       level TEXT NOT NULL, message TEXT NOT NULL)   -- 7-day retention
  -- index: (ts)
```

Pragmas on every connection: `journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`.

Two processes share this file through a Docker volume. WAL makes that safe — one writer plus
concurrent readers — **provided both are on the same local filesystem**. A network mount breaks
SQLite locking and is not supported.

Schema changes are numbered SQL scripts in `gateway/shared/migrations/`, applied once at startup and
recorded in `schema_version`. They are **additive only**: a released migration is never edited,
because deployed databases have already applied it.

## 7. Configuration

`.env.example` is the annotated template; every variable is explained with its reasoning in
[configuration.md](configuration.md). Most are read by the application at startup — `API_PORT` and
`HOST_MODEM_DEVICE` are consumed by `docker-compose.yml` instead, before the container exists.

| Variable | Default | Meaning |
|---|---|---|
| `API_PORT` | `8080` | Host port that `docker-compose.yml` publishes. The container always listens on 8080 — this changes only the host side. |
| `ADMIN_USER` | `admin` | Username of the environment admin |
| `ADMIN_PASSWORD` | — | **Required**, at least 12 characters |
| `SECRET_KEY` | — | **Required**, at least 32 characters; signs the session cookie |
| `SESSION_COOKIE_SECURE` | `0` | `1` when the panel is served over TLS |
| `HOST_MODEM_DEVICE` | — | Host path of the AT port, mapped in as `/dev/modem` |
| `MODEM_FAKE` | `0` | `1` selects the hardware-free driver |
| `MESSAGES_PER_MINUTE` | `6` | Send throttle, in segments; must be > 0 |
| `RATE_LIMIT_PER_MINUTE` | `0` | API rate limit, one shared window; `0` disables it |
| `DATABASE_PATH` | `/data/gateway.db` | SQLite file inside the container |
| `TZ` | `Europe/Zurich` | Display timezone; storage is always UTC |

A missing or too-short `ADMIN_PASSWORD` or `SECRET_KEY`, or an unknown `TZ`, **prevents startup**.
All problems are reported together in one message, and that message never contains the values.

## 8. Non-functional requirements

**Resources.** Both containers together stay under roughly 300 MB of RAM, on a board that has 1 GB.
No Celery, no Redis, no Postgres, no Node toolchain.

**Security.** Tokens stored hashed only; admin passwords via PBKDF2; secrets never written to logs,
error messages or tracebacks; session cookie `HttpOnly` + `SameSite=Lax`. The gateway expects a
trusted network and a reverse proxy for TLS. The full threat model, including accepted limitations,
is [security.md](security.md).

**Offline capability.** The admin panel and its manual must render with no internet access: no CDN,
no remote fonts, no remote scripts. This is a hard constraint, not a preference.

**Operability.** Survives power loss (WAL, queue in the database, `restart: unless-stopped`).
Structured JSON logs on stdout. State is a single file, so backup and restore are a file copy taken
through SQLite's online backup API.

**Fairness.** A consumer SIM is not a bulk channel. Defaults are conservative on purpose, and the
documentation says so repeatedly, because getting a SIM disconnected is a real outcome.

**Testing.** pytest, with every modem interaction against the fake driver, so the suite runs on any
machine with no hardware. Token authentication, webhook signing and retry, and the queue state
machine are covered end to end.
