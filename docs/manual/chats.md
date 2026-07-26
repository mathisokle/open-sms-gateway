# Chats

A conversation view grouped by phone number, with a reply box. This is the screen for
talking to one person; [Messages](messages.md) is the screen for auditing traffic.

## The conversation list

Every number the gateway has exchanged a message with, newest activity first. Each row
shows the number, the timestamp of the last message, a one-line preview with an arrow for
direction (`↑` outbound, `↓` inbound), and a badge with the message count.

The list is built from the **1000 most recent messages**. On a busy gateway an old,
long-quiet conversation eventually drops off this screen — the messages are never deleted,
they are simply outside the window. Find them via [Messages](messages.md) filtered by
number, which takes you to the same thread.

For the same reason the count badge is "messages within the last 1000", not the lifetime
total for that number.

## A conversation

Messages for one number, oldest at the top, capped at the **most recent 300**. Outbound
messages sit on the right in green, inbound on the left. Date separators mark day changes,
and the thread refreshes every five seconds, so an incoming reply appears on its own.

The header shows the counterparty's number and, when you have set it, the gateway number
the conversation runs over.

### Status ticks

The mark after the timestamp on an outbound bubble is the message status:

| Mark | Status | Meaning |
|---|---|---|
| `…` | `queued` / `sending` | Accepted, not yet away |
| `✓` | `sent` | The network took it |
| `✓✓` | `delivered` | The network confirmed handset delivery |
| `✗` | `failed` | Gave up — open the message detail for the reason |

**`✓` is the normal end state.** `✓✓` requires the modem and the operator to produce
delivery reports, and many do not. Never treat the absence of `✓✓` as a failure; see
[Troubleshooting](troubleshooting.md#why-messages-never-reach-delivered).

Inbound messages carry no tick — there is nothing to confirm.

## Replying

The reply box queues an outbound message to that number, exactly like the API does. The
bubble appears immediately with `…` and updates as the worker picks it up.

What the box does with your text:

- Leading and trailing whitespace is stripped. An empty result is rejected (422).
- The body is capped at 1600 characters and 10 segments, same as the API.
- The counter under the box shows characters, the detected alphabet and the segment count
  as you type. Read [Message format and syntax](sms-format.md#the-live-counter) for what it
  is telling you.
- It is a single-line field, so it cannot produce line breaks. Multi-line messages go
  through the [REST API](rest-api.md).

When a reply is rejected — empty after trimming, over 10 segments, or a counterparty that is
not valid E.164, which is the case for short codes and alphanumeric sender IDs — the form
posts through htmx and the thread simply does not update. There is no inline error message,
so a reply box that appears to do nothing is almost always one of those three cases. Reload
the page to confirm the message really was not queued.

There is no draft saving and no undo. Once the message is queued the only way to stop it is
to be faster than the worker's five-second poll — assume you cannot be.

## Replying to a number you have never messaged

The chat view only lists numbers with history. To start a new conversation, use *Send test
SMS* on the [Settings](settings.md) page or post to the API; the thread appears here as
soon as the message exists.

## Number handling

Opening `/admin/chats/<number>` with a number the gateway has never seen renders an **empty
thread rather than an error** — the URL is not validated when reading. Validation happens on
send: replying to anything that is not strict E.164 (`+41791234567`) is rejected with 422.

Conversations are grouped by the exact string stored on the message, so a number that
arrives inbound in a different format than you send to would appear as two separate
threads. In practice networks are consistent, but see
[Inbound messages](sms-format.md#inbound-messages) — short codes and alphanumeric sender
IDs are stored verbatim and cannot be replied to.
