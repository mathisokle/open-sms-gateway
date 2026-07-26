# Logs

The event log: significant operational events from the worker, the webhook dispatcher and
the admin panel, refreshed every five seconds.

This is a curated, low-volume record — not the full application log. For request-level
detail, read the containers' stdout with `docker compose logs`.

## The list

The **200 most recent** events, newest first: timestamp in the display timezone, source,
level badge and message. Filters for level and source live in the query string, so a
filtered view is bookmarkable.

| Source | Writes about |
|---|---|
| `worker` | Modem connect and disconnect, loop errors, restart requests, requeued sends |
| `webhook` | Deliveries that failed permanently |
| `admin` | User creation and deletion, password changes, purges, restarts, test messages |

| Level | Meaning |
|---|---|
| `info` | Something normal happened that is worth being able to point at later |
| `warning` | Degraded but self-healing (a requeued send, a permanently failed webhook), or a restart request |
| `error` | The modem loop threw and is backing off to reconnect |

Destructive admin actions are recorded at `info`, not `warning`: purging messages, deleting
an admin user and changing a password all land at `info`. Only the two restart buttons and a
permanently failed webhook delivery raise a `warning`. Filtering to `warning` therefore does
**not** give you an audit view of destructive actions — filter by source `admin` for that.

## Events you will see

| Message | Source | Meaning |
|---|---|---|
| `modem connected` | worker | The serial connection came up. Normal at start and after every reconnect |
| `modem loop error (…), reconnecting` | worker | The loop threw. The worker backs off 5→10→20→40→60 s and retries forever |
| `requeued N message(s) interrupted mid-send` | worker | The worker died while sending; those messages went back to `queued` |
| `worker restarting (admin request)` | worker | Someone pressed *Restart worker* |
| `delivery <id> failed permanently` | webhook | Six attempts over ~9 hours all failed |
| `test SMS queued to …4567` | admin | *Send test SMS* was used. Only the last four digits are recorded |
| `admin user '<name>' created` / `deleted` | admin | User management |
| `password changed for admin user '<name>'` | admin | User management |
| `purged N messages older than D days` | admin | Data cleanup ran |
| `api restart requested` / `worker restart requested` | admin | Restart buttons |

A repeating `modem loop error` every minute or so is the reconnect loop doing exactly what
it should while the hardware is unavailable — see
[Troubleshooting](troubleshooting.md#the-modem-is-gone). It is only alarming if it never
stops.

## Retention

Events are pruned to the **last 7 days** automatically, on every write. There is no setting
for this and no manual purge — the event log is a diagnostic buffer, not an audit archive.
If you need to keep something, copy it out or capture the container logs.

## What is never written here

By design, the event log contains no secrets and no message content:

- No plaintext API tokens, webhook secrets, passwords or session values.
- No SMS bodies.
- No full phone numbers — the test-SMS event records the last four digits only.

Also **not** recorded, which surprises people: API token creation, revocation and deletion,
and webhook secret rotation. Those actions write no event at all, so this log cannot answer
"who created that token and when". Treat it as an operational diagnostic buffer, not as a
security audit trail.

Events reference message and delivery IDs instead, which you can look up in
[Messages](messages.md) and [Webhook log](webhook-log.md). The same rule applies to the
stdout logs.

## Container logs

The event log is deliberately sparse. For everything else, both containers write structured
JSON lines to stdout:

```bash
docker compose logs -f --tail 100 api      # API, admin, webhook dispatcher
docker compose logs -f --tail 100 worker   # modem loop, send and receive

# readable with jq:
docker compose logs --no-log-prefix worker | jq -r '.ts + " " + .level + " " + .message'
```

The worker log is where you find per-message send detail (`sms sent: parts=2 …`), the modem
inbox dump on every poll that actually finds something, and full tracebacks for loop errors.
A poll that finds an empty modem store logs nothing, so silence between messages is normal
and not a sign that the loop has stopped — check *Worker last seen* on the
[Dashboard](dashboard.md#modem) for that. Those lines are too verbose for this screen, which
is why they are not here.
