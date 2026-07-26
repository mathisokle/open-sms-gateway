# REST API

Complete reference for API v1: authentication, the three message endpoints, the health
check, error format and rate limiting.

Base URL: `http://<gateway>:8080/api/v1`

There is no OpenAPI document and no Swagger UI. The interactive docs FastAPI ships pull
JavaScript and CSS from a CDN, which breaks the offline-LAN requirement, and an
unauthenticated `openapi.json` would publish the whole surface to anyone who can reach the
port. This page is the specification.

## Authentication

Every `/api/v1/*` endpoint requires a bearer token:

```
Authorization: Bearer sms_3f9a2c7e1b0d4a58c6e2f7a91d3b8c04
```

Create tokens in [API tokens](api-tokens.md). Tokens are looked up by SHA-256 hash;
unknown, malformed and revoked tokens all get `401` with a `WWW-Authenticate: Bearer`
header. There are no scopes — every token has full access.

`/healthz` is the only *API* endpoint without authentication. Outside `/api/v1` the admin
login page and the panel's static assets are necessarily reachable without credentials too.

## Errors

Every error the API produces **deliberately** — including validation failures — uses one
envelope:

```json
{"error": {"code": "validation_error", "message": "to: String should match pattern '^\\+[1-9][0-9]{6,14}$'"}}
```

An unhandled server fault is the exception: it is not wrapped, and comes back as a bare
`500` with a plain-text body. Treat any non-JSON response as a server-side failure rather
than assuming the envelope is always present.

| Status | `code` | Cause |
|---|---|---|
| 401 | `unauthorized` | Missing, malformed, unknown or revoked token |
| 403 | `forbidden` | Reserved; not currently produced |
| 404 | `not_found` | No message with that id |
| 422 | `validation_error` | Bad field, bad number format, body too long, bad cursor |
| 429 | `rate_limited` | `RATE_LIMIT_PER_MINUTE` exceeded |

Anything else uses `code: "error"`. The `message` field is human-readable and its exact
wording is not part of the contract — branch on `code` and the status, never on the text.

## Rate limiting

Optional and off by default. Set `RATE_LIMIT_PER_MINUTE` to enable a sliding 60-second
window; requests over the limit get `429`.

The window is **one shared budget for the whole API**, not per token — this is a
single-tenant relay, and one client can exhaust it for the others. It is enforced in memory
in the API process, so a restart resets it.

There is **no `Retry-After` header**, and the window slides rather than resetting on a fixed
tick: a slot frees up exactly 60 seconds after the request that took it, so the wait can be
anything from an instant to a full minute. Back off and retry rather than trying to compute
the exact moment.

The send throttle (`MESSAGES_PER_MINUTE`) is a different mechanism entirely: it limits how
fast the *worker* feeds the modem, not how fast the API accepts messages. Posting 100
messages in a second succeeds; they then leave at `MESSAGES_PER_MINUTE` **segments** per
minute — about 17 minutes for 100 single-segment messages at the default of 6, and
proportionally longer for multipart ones, since each segment costs a slot.

## The message object

```json
{
  "id": "msg_01J8ZQ4M7K3P2R5T8V0X1Y2Z3A",
  "direction": "outbound",
  "to": "+41791234567",
  "from": null,
  "body": "OSG: first message from the gateway.",
  "status": "sent",
  "segments": 1,
  "error": null,
  "created_at": "2026-07-25T18:00:00Z",
  "sent_at": "2026-07-25T18:00:05Z",
  "delivered_at": null,
  "received_at": null
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string | `msg_` plus a ULID. Lexicographically sortable by creation time |
| `direction` | string | `outbound` or `inbound` |
| `to` | string \| null | Recipient; `null` on inbound |
| `from` | string \| null | Sender; `null` on outbound |
| `body` | string | Verbatim message text |
| `status` | string | `queued`, `sending`, `sent`, `delivered`, `failed`, `received` |
| `segments` | integer | Parts the message occupies. On a `queued` message this is the gateway's own estimate; once sent, it is replaced by the part count the modem actually produced |
| `error` | string \| null | Modem error text when `status = failed` |
| `created_at` | string | UTC, `YYYY-MM-DDTHH:MM:SSZ` |
| `sent_at` | string \| null | When the modem accepted it |
| `delivered_at` | string \| null | When a delivery report arrived. Frequently `null` forever |
| `received_at` | string \| null | Network timestamp of an inbound message |

`to` and `from` are two views of one stored column, so exactly one of them is non-null on
any message. All timestamps are UTC with a `Z` suffix and second precision.

`from` on an inbound message is whatever the network reported and is **not** guaranteed to
be E.164 — short codes and alphanumeric sender IDs arrive as-is.

## Send a message

```
POST /api/v1/messages
```

```json
{"to": "+41791234567", "body": "OSG: disk usage on db-01 is at 91%."}
```

| Field | Rules |
|---|---|
| `to` | Required. E.164, `^\+[1-9][0-9]{6,14}$` |
| `body` | Required. 1–1600 characters, at most 10 segments |

`201 Created`:

```json
{"id": "msg_01J8ZQ...", "status": "queued", "segments": 1, "created_at": "2026-07-25T18:00:00Z"}
```

The response is deliberately small — it confirms acceptance, not delivery. Poll
`GET /api/v1/messages/{id}` or use a webhook if you need to know what happened next.

**`201` means queued, not sent.** The message is durably stored and will be sent when the
worker reaches it, which respects `MESSAGES_PER_MINUTE`. If the worker or the modem is
down, the message waits rather than failing.

The body is stored and transmitted verbatim: no trimming, no normalisation, no link
rewriting. Read [Message format and syntax](sms-format.md) before you write the first one.

```bash
curl -X POST http://<gateway>:8080/api/v1/messages \
  -H "Authorization: Bearer sms_..." \
  -H "Content-Type: application/json" \
  -d '{"to":"+41791234567","body":"OSG: hello"}'
```

### Idempotency

There is none. Posting the same payload twice queues two messages. If your caller retries
on timeouts, generate your own key, remember it, and check `GET /api/v1/messages` before
resending.

## List messages

```
GET /api/v1/messages
```

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `direction` | `outbound` \| `inbound` | — | |
| `status` | one of the six statuses | — | |
| `since` | `YYYY-MM-DDTHH:MM:SSZ` | — | `created_at >= since` |
| `until` | `YYYY-MM-DDTHH:MM:SSZ` | — | `created_at <= until` |
| `to` | E.164 | — | Implies `direction=outbound` |
| `from` | E.164 | — | Implies `direction=inbound` |
| `limit` | 1–200 | 50 | |
| `cursor` | opaque string | — | From a previous `next_cursor` |

```json
{"data": [ /* message objects */ ], "next_cursor": "MjAyNi0wNy0yNVQxODowMDowMFp8bXNnXzAxSjha..."}
```

Results are ordered **oldest first** by `(created_at, id)` — the opposite of the admin
panel, and the order that makes cursor paging stable.

`since` and `until` must match the exact format above, including the `Z`; anything else is
a 422. The format is enforced because timestamps are compared as strings, and only this
canonical shape compares correctly.

Combining `to` with `direction=inbound` (or `from` with `direction=outbound`) is accepted
but always returns an empty list.

### Paging

`next_cursor` is present only when the page was completely full — that is, when exactly
`limit` rows came back. Keep passing it until it is `null`.

```python
cursor, out = None, []
while True:
    params = {"direction": "inbound", "limit": 200}
    if cursor:
        params["cursor"] = cursor
    page = session.get(f"{BASE}/messages", params=params, headers=auth).json()
    out += page["data"]
    cursor = page["next_cursor"]
    if not cursor:
        break
```

The cursor encodes `created_at` and `id` of the last row, so paging is stable while new
messages arrive: they sort after the cursor and appear on a later page rather than shifting
the ones you already read. An invalid or truncated cursor is a 422.

Because a full page always returns a cursor, the last page of an exact multiple of `limit`
comes back empty with `next_cursor: null`. That is the normal termination, not an error.

### Polling for new inbound messages

```
GET /api/v1/messages?direction=inbound&since=2026-07-25T18:00:00Z&limit=200
```

Remember the `created_at` of the newest message you processed and pass it as `since` next
time. `since` is inclusive, so you will see that message again — deduplicate on `id`.
Polling every 10–30 seconds is plenty; the worker only reads the modem every 5 seconds.

Webhooks are the lower-latency option — see [Webhook integration](webhooks.md).

## Get one message

```
GET /api/v1/messages/{id}
```

Returns the full message object, or `404` with `code: "not_found"`.

This is how you follow an outbound message to its conclusion. Poll a few times over the
first half minute; if the status is still `queued` after that, look at the
[Dashboard](dashboard.md) rather than polling harder.

## Health check

```
GET http://<gateway>:8080/healthz
```

Note the path: `/healthz` sits at the **root**, not under `/api/v1`, so it stays reachable
and unauthenticated for container healthchecks and uptime monitors.

```json
{
  "status": "ok",
  "modem": {"connected": true, "signal_percent": 61, "operator": "Swisscom"},
  "queue_depth": 0,
  "worker_seen_at": "2026-07-25T18:00:00Z"
}
```

| Status code | `status` | Meaning |
|---|---|---|
| `200` | `ok` | The worker wrote a heartbeat within the last 120 seconds |
| `503` | `degraded` | It did not |

The check is about the **worker's liveness**, not the modem's. A worker that is up and
reconnecting to a missing modem still returns `200` with `"connected": false` — it is doing
its job. `503` means the worker process itself is not running the loop, and nothing will be
sent or received until it is.

`signal_percent` and `operator` are `null` when unknown.

## Client notes

- Always send `Content-Type: application/json` on POST.
- Bodies are UTF-8. Non-ASCII is fine on the wire; what it costs you is a
  [segment question](sms-format.md#character-budget), not an encoding one.
- Retry `429` after a short delay, and `5xx` with backoff. Never retry `422` — the request
  is wrong and will stay wrong.
- Treat unknown JSON fields as forward compatibility and ignore them.
