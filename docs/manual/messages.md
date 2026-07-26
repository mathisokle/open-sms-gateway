# Messages

The message browser: every message in both directions with filters, and a detail page per
message showing timestamps, segment count, error text and webhook history.

Use this screen to audit traffic and diagnose a specific message. Use [Chats](chats.md) to
hold a conversation.

## The list

The **100 most recent** messages matching your filters, newest first, ordered by
`created_at` then `id`. Columns: created timestamp, direction, counterparty number, status
badge, the first 60 characters of the body, and a link to the detail page.

There is no pagination. Narrow with the filters, or use
`GET /api/v1/messages` with a cursor when you need to walk the full history — see
[REST API](rest-api.md#list-messages).

### Filters

| Filter | Values | Notes |
|---|---|---|
| Direction | `outbound`, `inbound` | |
| Status | `queued`, `sending`, `sent`, `delivered`, `failed`, `received` | |
| Number | any string | Exact match on the stored number, not a substring search |

Filters combine with AND and live in the query string, so a filtered view is a bookmarkable
URL: `/admin/messages?status=failed&direction=outbound`.

The number filter is exact. Search for `+41791234567`, not `791234567` — the stored value
includes the `+` and the country code.

## Statuses

| Status | Direction | Meaning |
|---|---|---|
| `queued` | outbound | Accepted, waiting for the worker. Survives restarts. |
| `sending` | outbound | Handed to the modem right now |
| `sent` | outbound | The network accepted it |
| `delivered` | outbound | A delivery report came back. Best effort — see below |
| `failed` | outbound | Rejected by the modem — up to 3 attempts for an ordinary error, immediately for a partial multipart send (retrying would duplicate the delivered parts); the reason is in `error` |
| `received` | inbound | Stored from the modem inbox |

`sending` should never persist for more than a few seconds. If it does, the worker died
mid-send; on its next start it requeues everything stuck in `sending` back to `queued` and
records a warning in [Logs](logs.md). That requeue is at-least-once by design: the message
may or may not have left the modem, and a possible duplicate is judged better than a
silent loss.

## Detail page

| Field | Notes |
|---|---|
| Direction | `outbound` or `inbound` |
| Number | The counterparty — recipient for outbound, sender for inbound |
| Status | Current status |
| Segments | Parts this message occupies. See [Character budget](sms-format.md#character-budget) |
| Error | Modem error text on failure, otherwise `—` |
| Created | When the gateway accepted or stored it |
| Sent | When the modem took it (outbound only) |
| Delivered | When a delivery report arrived (outbound only) |
| Received | Network timestamp of an inbound message |

Below that, the full message body, and — if the message triggered any — the webhook
delivery attempts for it, with attempt number, status, HTTP response code, next retry time
and delivery time.

### Reading the timestamps

All timestamps are stored in UTC and rendered in the display timezone (`TZ`). A gap between
*Created* and *Sent* is the send throttle at work, and is expected during a burst. A gap
between *Sent* and *Delivered* is network and handset latency, and is often minutes.

*Received* on an inbound message is the timestamp the **network** put on the message, not
when the gateway read it from the modem — the gateway's own view is *Created*. If the modem
was unreachable for an hour, those two are an hour apart, and *Received* is the truthful
one.

### Reading the error field

The `error` value is the modem's own message, passed through unchanged. Common shapes:

| Error contains | Usually means |
|---|---|
| `ERR_UNKNOWN`, `ERR_SMSC` | SIM credit exhausted, or the operator rejected the submit |
| `ERR_TIMEOUT` | The modem stopped answering; the worker will reconnect |
| `multipart send failed at part n/m` | Some parts went out, some did not — see below |
| `ERR_NOTSUPPORTED` | The modem rejected the encoding or the recipient format |

A **partial multipart failure** is never retried automatically. Retrying would re-send the
parts that already arrived, and the recipient would see a garbled, duplicated message.
The message is marked `failed` with the part number in `error`; assume the recipient got a
fragment, and resend deliberately if it matters.

## Failed messages are not resent

Nothing on this page resends. `failed` is terminal: fix the underlying cause, then submit
the message again through the API or a chat reply. This is intentional — an automatic
retry of a message that failed for a business reason (no credit, blocked recipient) just
burns the queue.

## Retention

Messages are kept forever unless you delete them. *Data cleanup* on the
[Settings](settings.md#data-cleanup) page removes messages older than 30, 90 or 365 days
together with their webhook delivery records. There is no automatic retention policy — the
database is the only part of the gateway that grows without bound, so check its size on the
Settings page occasionally. On a small host with an SD card that matters sooner.
