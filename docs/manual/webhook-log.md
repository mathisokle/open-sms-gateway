# Webhook log

One row per inbound SMS the gateway tried to push to your webhook URL, showing how many
attempts it has taken, the HTTP response code of the last one, the retry schedule and a
manual retry button for failures.

This is the screen you open when your application stopped receiving messages. For how to
build and secure the receiving end, see [Webhook integration](webhooks.md).

## What creates an entry

When the worker stores an inbound SMS **and** a webhook URL plus secret are configured, it
writes a delivery record with `status = pending` and `next_retry_at = now`. A dispatcher
task inside the API container wakes every ten seconds, picks up everything due, and POSTs
it.

No webhook configured means no delivery records at all. Inbound messages are still stored
and still available through [Messages](messages.md) and the polling API — the webhook is
purely an optional push channel.

## The list

The **100 most recent** delivery records, newest first.

| Column | Meaning |
|---|---|
| Created | When the delivery record was created — effectively when the SMS arrived |
| Message | Link to the message being delivered |
| Attempt | Number of attempts made so far, `0` before the first |
| Status | `pending`, `delivered` or `failed` |
| HTTP | Response code of the last attempt, `—` if the request never got one |
| Next retry | When the next attempt is due (`pending` only) |
| Delivered | When an attempt finally succeeded |

An empty **HTTP** column on a failed attempt means no response was received at all —
connection refused, DNS failure, TLS error or the 10-second timeout expired. A code that is
present but outside 2xx means your endpoint answered and rejected the delivery.

## Statuses and the retry schedule

Success is **any 2xx within 10 seconds**. Everything else — 3xx, 4xx, 5xx, timeout,
connection error — counts as a failure and schedules a retry:

| After attempt | Next attempt in |
|---|---|
| 1 | 1 minute |
| 2 | 5 minutes |
| 3 | 30 minutes |
| 4 | 2 hours |
| 5 | 6 hours |
| 6 | none — the record becomes `failed` |

So a delivery is tried six times over roughly nine hours before it is abandoned. A
permanent failure is recorded in [Logs](logs.md) as a warning.

The backoff is measured from the moment the attempt concludes, not from the start of the
dispatcher round. A slow round with many deliveries, each burning up to ten seconds,
therefore cannot silently compress the schedule.

Each attempt **rebuilds the JSON payload from the message row and re-signs it with the
current secret**. Two consequences worth knowing: rotating the secret takes effect on the
very next retry, so a receiver you have already updated starts verifying again without any
action here; and the `X-Gateway-Delivery` header keeps the same value across every attempt,
which is what makes it usable as an idempotency key on your side.

**A 3xx is a failure.** The dispatcher does not follow redirects — a redirect would move
your signed payload to a URL you did not configure. If your endpoint redirects (`http` to
`https` is the classic case), fix the configured URL to the final destination.

## Manual retry

The *Retry delivery* button appears on `failed` records only. It sets the record back to
`pending` with `next_retry_at = now`, so the dispatcher picks it up within ten seconds.

The attempt counter is **not** reset. A record that already used all six attempts is at
attempt 6, so the manual retry is a single extra try: if it fails, the record goes straight
back to `failed`. Press it again once you have actually fixed the receiving end, rather
than as a way to wait out an outage.

Retrying a delivery whose message has since been purged marks it `failed` immediately —
there is no payload left to send.

## Deliveries that stall without failing

If you clear the webhook URL or secret while deliveries are pending, the dispatcher does
**not** fail them. It logs a warning and defers, leaving them `pending` with a `next_retry_at`
in the past. Restore the configuration and they all flush on the next round.

This is why a batch of `pending` records with a *Next retry* in the past is not a bug —
check [Settings](settings.md#webhook-for-inbound-sms) first.

## Reading the log

| Symptom | Likely cause |
|---|---|
| No records at all, but inbound messages exist | No webhook URL configured |
| All records `pending`, next retry in the past | URL or secret was cleared |
| HTTP empty, attempts climbing | Endpoint unreachable, DNS, TLS, or slower than 10 s |
| HTTP 401/403 | Your signature check is rejecting the payload |
| HTTP 404 | Wrong path in the configured URL |
| HTTP 3xx | Redirect — point the configuration at the final URL |
| HTTP 500 | Your handler is throwing |
| Your endpoint logs 200 but the record is not `delivered` | Something between the gateway and your handler answered instead — a reverse proxy, WAF or tunnel returning its own status. The gateway records the code it actually received. |

The message-level view of the same data is on each message's detail page in
[Messages](messages.md), which is more convenient when you are chasing one specific SMS.

## Retention

Delivery records live as long as their message. *Data cleanup* on the
[Settings](settings.md#data-cleanup) page deletes both together.
