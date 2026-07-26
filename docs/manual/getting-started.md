# Getting started

A ten-minute tour of the gateway: what it does, what the admin panel is for, and the
five steps that take you from a fresh install to a delivered SMS.

## What this gateway is

Open SMS Gateway is a **single-tenant SMS relay**. One SIM card, one phone number, one
operator. It turns any always-on Linux host with a free USB port — a Raspberry Pi, a mini
PC, a NAS, a thin client, an old laptop, an x86 server, or a VM with USB passthrough — plus
a SIM7600E-H USB modem into a small HTTP service that can send and receive text messages:

- **Outbound** — your application calls `POST /api/v1/messages`, the gateway queues the
  message and a worker process feeds it to the modem, throttled.
- **Inbound** — the worker polls the modem every five seconds and stores every received
  SMS. The API process then optionally pushes it to your application as a signed webhook.
- **Operations** — this admin panel shows what the modem, the queue and the webhook
  dispatcher are doing, and lets you send, browse and troubleshoot by hand.

What it is **not**: a bulk-messaging platform. A consumer SIM sends a handful of messages
per minute before the operator starts filtering. See [Etiquette and compliance](sms-format.md#etiquette-and-compliance).

## The two processes

The gateway runs as two containers built from one image, sharing one SQLite file.

| Process | Job | Talks to |
|---|---|---|
| `api` | REST API, this admin panel, webhook dispatcher | HTTP clients, your webhook receiver |
| `worker` | Send queue, modem inbox, status and heartbeat | The modem, exclusively |

Only the worker ever opens the serial port. If the worker is down, the API still accepts
messages — they simply stay `queued` until the worker comes back. Nothing is lost.

The webhook dispatcher deliberately lives in the `api` process, not the worker: a slow or
unreachable receiver must never be able to stall the modem loop.

## Five steps to your first SMS

Open the admin panel at `http://<gateway>:8080/admin` and log in with the `ADMIN_USER` and
`ADMIN_PASSWORD` from your `.env`, then:

1. **Set the gateway number.** Go to [Settings](settings.md) and enter the SIM's own number
   in E.164 form (`+41791234567`). It is display-only, but the dashboard and the chat view
   use it, and you will want to know which number your recipients see.
2. **Check the modem.** The [Dashboard](dashboard.md) must show *Connected: yes*, a signal
   percentage and an operator name. If it does not, start at [Troubleshooting](troubleshooting.md).
3. **Send a test message.** Use *Send test SMS* on the [Settings](settings.md) page. Watch it
   move from `queued` to `sent` in [Messages](messages.md) within a few seconds.
4. **Create an API token.** [API tokens](api-tokens.md) → *Create token*. The plaintext is
   shown exactly once. Copy it now.
5. **Send from your application.**

```bash
curl -X POST http://<gateway>:8080/api/v1/messages \
  -H "Authorization: Bearer sms_..." \
  -H "Content-Type: application/json" \
  -d '{"to": "+41791234567", "body": "OSG: first message from the gateway."}'
```

The full endpoint reference is in [REST API](rest-api.md).

## Receiving messages

Every inbound SMS is stored and visible in [Messages](messages.md) and [Chats](chats.md)
immediately — no configuration required. To get them pushed to your application, configure
a webhook URL in [Settings](settings.md); the gateway then POSTs a signed JSON payload per
message and retries with backoff. See [Webhook integration](webhooks.md).

If you prefer polling, `GET /api/v1/messages?direction=inbound&since=...` covers it without
any webhook at all.

## Message lifecycle

Every message is a row in one table with a `direction` and a `status`.

```
outbound:  queued ──> sending ──> sent ──> delivered
                         │                 (only if the network reports it)
                         └──> failed

inbound:   received
```

| Status | Meaning |
|---|---|
| `queued` | Accepted and waiting for the worker. Survives restarts and power loss. |
| `sending` | Handed to the modem right now. |
| `sent` | The modem accepted it and the network took it. |
| `delivered` | The network sent back a delivery report. **Best effort** — many SIMs and networks never do. |
| `failed` | The modem rejected it: up to three attempts for an ordinary error, or immediately for a multipart message that broke partway through, because retrying would duplicate the parts already delivered. The reason is in the `error` field. |
| `received` | An inbound message. |

A message stuck at `sent` is normal, not a fault. See
[Why messages never reach "delivered"](troubleshooting.md#why-messages-never-reach-delivered).

## Where to go next

| If you want to… | Read |
|---|---|
| write messages that survive contact with real phones | [Message format and syntax](sms-format.md) |
| call the API from your own code | [REST API](rest-api.md) |
| receive inbound messages in your application | [Webhook integration](webhooks.md) |
| understand a screen in this panel | the page for that menu entry |
| fix something that is broken | [Troubleshooting](troubleshooting.md) |
