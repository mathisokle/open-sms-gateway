# Dashboard

The operational overview: modem health, queue depth, today's and all-time counters, and
three charts. Everything on this page refreshes itself every ten seconds.

The *Live* badge in the header means the page is polling `/admin/partials/stats` and
swapping in fresh numbers without a reload. You can leave it open on a second screen.

## Today

Counters for the current day in the display timezone (`TZ`, default `Europe/Zurich`), not
in UTC. The day boundary is local midnight, converted to a UTC window for the query — so
these numbers roll over when your day rolls over, not at 01:00 or 02:00.

One tile is not day-scoped: **Queue** is a live count of everything still waiting, whatever
its age. A message queued last week and never sent is in it.

| Tile | Counts messages where |
|---|---|
| **Queue** | `status = queued` — waiting for the worker, regardless of age |
| **Sent today** | `status` is `sent` or `delivered`, and `sent_at` falls in today |
| **Delivered today** | `status = delivered` and `delivered_at` falls in today |
| **Received today** | `direction = inbound` and `received_at` falls in today |
| **Failed today** | `status = failed` and `created_at` falls in today |

Note the different timestamps. *Sent today* is keyed on when the modem accepted the
message, *Failed today* on when the message was created. A message queued yesterday and
sent this morning counts in today's *Sent*; the same message failing counts in yesterday's
*Failed*. This is deliberate: a failure belongs to the attempt that started it.

**Queue is the tile to watch.** A number that climbs and does not fall means the worker is
not draining the outbox — either it is down, the modem is disconnected, or you are pushing
messages faster than `MESSAGES_PER_MINUTE` allows. A queue that hovers at one or two during
a burst is the throttle doing its job.

## All time

| Tile | Meaning |
|---|---|
| **Total messages** | Every row in the messages table, both directions, every status |
| **Sent (total)** | Outbound that reached the network (`sent` or `delivered`) |
| **Received (total)** | Every inbound message |
| **Failed (total)** | Outbound the modem refused after all retries |
| **Success rate** | `(sent + delivered) / (sent + delivered + failed)` as a percentage |
| **Webhook success** | Webhook delivery records that ended `delivered`, over all records that concluded (`delivered` + `failed`) |
| **Active tokens** | API tokens that have not been revoked |

**Success rate does not *require* `delivered`.** Both `sent` and `delivered` count as
success, so the number does not collapse to 0% on the many SIM/network combinations that
never emit delivery reports. What it answers is "of the messages that reached a conclusion,
how many did the network accept". Messages still `queued` or `sending` are excluded; the
tile shows `—` until at least one message has concluded.

**Webhook success counts records, not attempts.** The gateway creates exactly one delivery
record per inbound SMS and increments its attempt counter in place, so a message that
succeeded on its third try contributes a single `delivered` record — not two failures and a
success. A dash means nothing has ever been attempted, usually because no webhook is
configured.

## Modem

| Row | Source | Notes |
|---|---|---|
| **Connected** | worker | `yes` only while the worker holds an open serial connection |
| **Signal** | `AT+CSQ` via gammu | Percentage plus a meter. Below ~20% expect failures |
| **Operator** | network info | Falls back to the MCC/MNC code if the name is not resolvable |
| **Gateway number** | Settings, or the SIM | Your manual setting wins; otherwise whatever `AT+CNUM` reports, which is usually nothing |
| **Registration** | network info | `home`, `roaming`, `searching`, … |
| **Worker last seen** | heartbeat | Written on every loop pass, roughly every 5 seconds |
| **Webhook** | config | `configured` if a webhook URL is set |

**Connected and Worker last seen answer different questions.** *Connected* is what the
worker last managed to write. If the worker process dies outright, that row keeps showing
the last known value forever — it is *Worker last seen* going stale that tells you the
worker is gone. `/healthz` returns 503 once the heartbeat is older than 120 seconds; the
dashboard shows you the same fact as a timestamp.

If *Gateway number* shows a link to Settings instead of a number, the SIM does not publish
its own MSISDN. That is normal and harmless. Enter it by hand in
[Settings](settings.md#gateway) so chats and the dashboard can show it.

## Outbound status

A donut over the current status of all outbound messages, ever: `delivered`, `sent`,
`queued` (which folds in `sending`), and `failed`. Inbound messages carry the status
`received` and do not appear here, so the centre total is the outbound total.

Because a message only ever has one current status, this is a snapshot, not a history. A
message that failed twice and then succeeded appears once, as `sent`.

A large `failed` slice with a healthy modem almost always means SIM credit or an operator
block — open [Messages](messages.md), filter by `failed`, and read the `error` field on a
detail page.

## Activity — last 24 hours

Stacked bars, one per hour. Green is outbound, cyan inbound. Hover any bar for exact
counts; the legend shows the busiest hour's total as `max N/h`, which is also the scale the
bars are normalised against — so the tallest bar is always full height and the chart shows
shape rather than absolute volume.

The buckets are cut on **UTC** hour boundaries and only the labels are rendered in the
display timezone. For a whole-hour offset such as `Europe/Zurich` that is invisible; on a
zone offset by 30 or 45 minutes the labels are shifted relative to the bucket edges.

Bucketing by `created_at` means an outbound bar marks when the message was *accepted*, not
when it left the modem. During a throttled burst the bar appears an hour before the
messages actually go out. That is the intended reading: the chart shows load offered to the
gateway.

Hours with no traffic show a thin baseline so the axis stays readable.

## Trend — last 7 days

Two lines over the last seven local days, again bucketed by `created_at`: outbound and
inbound per day. Today is the rightmost point and is partial until the day ends. Both lines
share one vertical scale, so they are directly comparable.

## Related

- Modem shows disconnected → [Troubleshooting](troubleshooting.md#the-modem-is-gone)
- Queue climbing → [Troubleshooting](troubleshooting.md#the-queue-is-not-draining)
- Failed messages → [Messages](messages.md)
- Webhook success below 100% → [Webhook log](webhook-log.md)
