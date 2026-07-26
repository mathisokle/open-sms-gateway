# Troubleshooting

Symptom-first runbook for the failures this gateway actually produces: modem loss, stalled
queues, failed sends, missing delivery reports, webhook problems and lockouts.

Start with the [Dashboard](dashboard.md) and [Logs](logs.md) — between them they identify
most problems in under a minute.

## First checks

```bash
curl http://<gateway>:8080/healthz            # worker alive? modem connected? queue depth?
docker compose ps                             # both containers up?
docker compose logs -f --tail 100 worker      # modem loop
docker compose logs -f --tail 100 api         # API, admin, webhook dispatcher
```

| What you see | Go to |
|---|---|
| `503`, `"status": "degraded"` | [The worker is not running](#the-worker-is-not-running) |
| `"connected": false` | [The modem is gone](#the-modem-is-gone) |
| `queue_depth` climbing | [The queue is not draining](#the-queue-is-not-draining) |
| Messages `failed` with an `error` | [Messages fail to send](#messages-fail-to-send) |
| Never `delivered` | [Why messages never reach "delivered"](#why-messages-never-reach-delivered) |
| Webhook deliveries failing | [Webhooks are not arriving](#webhooks-are-not-arriving) |

## The modem is gone

**Symptoms.** `/healthz` shows `"connected": false`. The dashboard shows *Connected: no*.
The worker log repeats `modem loop error, reconnecting in …s`.

**This is self-healing.** The worker reconnects forever with a backoff of 5, 10, 20, 40, 60
seconds, and queued messages stay in the database. Nothing is lost while it is down.

If it does not come back:

1. Check ModemManager is not holding the port. It ships **enabled** on most Debian and
   Ubuntu desktop and server installs and on many NAS distributions, and it grabs any modem
   it finds. Two processes on one AT port produce exactly this symptom:
   ```bash
   systemctl status ModemManager
   sudo systemctl disable --now ModemManager
   ```
2. Check the device is still there on the **host**:
   ```bash
   ls -l /dev/serial/by-id/
   ```
   You want the entry ending in `-if02-port0` — interface 02 is the SIM7600's AT port.
   Interfaces 00, 01, 03 and 04 exist too and will not work.
3. If the by-id path changed, update `HOST_MODEM_DEVICE` in `.env` and
   `docker compose up -d`. Never use `/dev/ttyUSB2` directly — the number moves between
   reboots and replugs.
4. If the path is gone entirely, the dongle dropped off USB. Replug it and give it 30
   seconds to enumerate.
5. Suspect power. The dongle draws sharp current spikes while transmitting, and an
   undersized supply causes exactly this symptom — fine at idle, resets under load. On a
   Raspberry Pi that means a solid 5 V / 2.5 A supply and a short, thick cable. On any host,
   move the dongle off an unpowered hub and onto a port that can actually source current.
6. As a last resort, `docker compose restart worker`, or *Restart worker* in
   [Settings](settings.md#danger-zone).

## The worker is not running

**Symptoms.** `/healthz` returns `503` with `"status": "degraded"`. *Worker last seen* on
the dashboard is minutes or hours old, while *Connected* still shows a stale `yes`.

The heartbeat is written on every loop pass, about every five seconds, and `/healthz` calls
it stale after 120 seconds. A stale heartbeat means the process is not looping — it is
dead, wedged, or was never started.

```bash
docker compose ps                  # is the worker container up?
docker compose logs --tail 200 worker
docker compose restart worker
```

The usual cause is that the container never started at all: `devices:` in
`docker-compose.yml` points at a path that no longer exists, and Docker refuses to create a
container with a missing device. `docker compose ps` shows it as exited rather than running.

Note the distinction from a *connected* modem problem: with `MODEM_FAKE=0` and no modem
reachable, the worker does **not** stop. It catches the failure, writes a disconnected
status, keeps its heartbeat fresh and retries forever — so the dashboard shows
*Connected: no* with a recent *Worker last seen*. A stale heartbeat means the process itself
is gone; a fresh heartbeat with `Connected: no` means it is alive and cannot reach the
hardware, which is [The modem is gone](#the-modem-is-gone).

In development, set `MODEM_FAKE=1` **and** comment out the `devices:` block.

While the worker is down the API keeps accepting messages. They queue and flush when it
returns.

## The queue is not draining

**Symptoms.** *Queue* on the dashboard climbs and does not fall, with the modem connected
and the worker's heartbeat fresh.

Work through these in order:

1. **The throttle is doing its job.** `MESSAGES_PER_MINUTE` defaults to 6, counted in
   **segments**, not messages. Ten 3-segment messages need 30 tokens — five minutes at the
   default. Check *Send throttle* in [Settings](settings.md#the-information-table) and the
   `segments` column in [Messages](messages.md).
2. **Messages stuck in `sending`.** Filter [Messages](messages.md) by `sending`. If rows sit
   there for more than a few seconds, the worker died mid-send. Restart it; on start it
   requeues everything stuck in `sending` and logs `requeued N message(s) interrupted
   mid-send`.
3. **The modem is accepting but not sending.** The worker log shows `sms sent` lines with
   no corresponding failures — messages leave the gateway, the problem is downstream. See
   the next section.

Raising `MESSAGES_PER_MINUTE` is rarely the answer. A consumer SIM that suddenly sends 60
messages a minute is exactly the pattern operators filter on.

## Messages fail to send

**Symptoms.** Messages go `queued → sending → failed` with a value in `error`, while the
modem is connected with a good signal.

Open the message in [Messages](messages.md) and read `error`:

| Error | Cause | Fix |
|---|---|---|
| `ERR_UNKNOWN`, SMSC errors | SIM credit exhausted, or the operator rejected the submit | Top up, check the contract. Receiving usually keeps working |
| `ERR_TIMEOUT` | Modem stopped responding | Self-healing; the worker reconnects |
| `multipart send failed at part n/m` | Some parts of a long message went out, some did not | Not retried — the recipient has a fragment. Resend deliberately, ideally shorter |
| `ERR_NOTSUPPORTED` | Encoding or recipient format rejected | Check the number is E.164 and the body is sane |

**Failed messages are never resent automatically.** After fixing the cause, submit them
again through the API or a chat reply.

A signal below roughly 20% causes intermittent failures that look random. Check the meter on
the [Dashboard](dashboard.md) before blaming software; a better antenna position fixes more
of these than any setting.

## Why messages never reach "delivered"

**Symptom.** Everything ends at `sent` / `✓`. `delivered_at` is always `null` and
*Delivered today* is always 0.

**This is normal and is not a fault.** `delivered` requires a network delivery report
(SMS-STATUS-REPORT) to come back and be matched to the message. That needs the operator to
generate reports, the SIM's plan to include them, and the modem firmware to surface them —
and the SIM7600 over gammu frequently does not. Many networks never send them at all.

The gateway does everything it can: it requests a report on every submit, listens for
`+CDS` notifications as well as polling the modem's storage, and matches reports to
messages by their TP-MR reference.

Treat `sent` as success. That is exactly why the dashboard's *Success rate* tile is built
on `sent` versus `failed` and ignores `delivered` — a delivery-based rate would read 0%
forever on this hardware.

Two details, if you are chasing a specific report:

- Matching is **strict when the report carries a reference**. A referenced report that
  matches nothing is logged and dropped rather than being applied to the most recent
  message, because guessing would mark the wrong one.
- A multipart message stores only the **last** part's reference, so reports for earlier
  parts do not match. Long messages are less likely to flip to `delivered` than short ones.

## Inbound messages are not arriving

1. Confirm they are being stored at all: [Messages](messages.md), filter `inbound`. If they
   are there, the problem is your webhook — see the next section.
2. Send an SMS to the gateway number from a phone and watch `docker compose logs -f worker`.
   You should see the message within about five seconds. Note that a poll finding an **empty**
   inbox logs nothing at all, so quiet output between messages is normal.
3. If the modem never reports anything, check that the SIM is registered
   (*Registration* on the [Dashboard](dashboard.md)) and that the SIM's inbox is not full.
   A full SIM storage silently stops accepting new messages — the worker deletes messages
   from the modem after reading, so this only happens if it has been down for a long time.
4. Verify the SIM-PIN is disabled. A modem waiting for a PIN registers but does nothing.

## Webhooks are not arriving

Open [Webhook log](webhook-log.md) and read the last few rows.

| What the log shows | Cause |
|---|---|
| No records at all | No webhook URL configured — [Settings](settings.md#webhook-for-inbound-sms) |
| `pending`, next retry in the past | The URL or secret was cleared; deliveries are deferred, not failed |
| Empty HTTP column | Endpoint unreachable: DNS, TLS, connection refused, or slower than 10 s |
| HTTP 401/403 | Your signature check is rejecting the payload |
| HTTP 3xx | You redirect; the dispatcher does not follow. Configure the final URL |
| HTTP 404 | Wrong path |
| HTTP 5xx | Your handler throws |

Two mistakes account for most signature failures: verifying against re-serialised JSON
instead of the raw request bytes, and a stale secret after a rotation. See
[Verifying the signature](webhooks.md#verifying-the-signature).

Remember the gateway resolves your URL from inside its container. `localhost` and
`127.0.0.1` point at the container itself — use the LAN address or a hostname.

## A container will not start (restart loop)

**Symptoms.** `docker compose ps` shows `Restarting`, and the log contains a configuration
error.

The gateway validates its configuration at startup and refuses to run rather than start with a weak
secret:

| Rule | Value | Message you get |
|---|---|---|
| `ADMIN_PASSWORD` | must be set, at least 12 characters | `invalid configuration: …` |
| `SECRET_KEY` | must be set, at least 32 characters (64 random recommended) | `invalid configuration: …` |
| `TZ` | must be a valid zone name | `invalid TZ '…'` |
| `MESSAGES_PER_MINUTE` | must be greater than 0 | a bare validation error for the field |

**Only the `ADMIN_PASSWORD` and `SECRET_KEY` problems are reported together.** `TZ` and
`MESSAGES_PER_MINUTE` are validated separately and abort before the others are collected, so
a bad timezone masks a weak password. Expect to need a second restart after fixing one of
those two.

```bash
docker compose logs --tail 20 worker   # read the actual message
nano .env                              # fix the values
docker compose up -d
```

This bites when updating an older installation: an `.env` that used to be accepted can suddenly be
rejected. The error message never contains the values themselves — the gateway will not print a
secret into a log even while complaining about it.

Two things worth knowing before you edit:

- Changing `SECRET_KEY` invalidates every open admin session. That is also the supported way to log
  everyone out globally.
- Changing `ADMIN_PASSWORD` affects neither existing sessions nor the admin users created in the
  panel.

## Locked out of the admin panel

The `.env` admin always works. Edit `.env`, set `ADMIN_USER` and `ADMIN_PASSWORD`
(at least 12 characters), and restart the API:

```bash
docker compose up -d --force-recreate api
```

If login returns *Too many attempts — wait a minute*, you have hit the 10-attempts-per-
minute limit. It is a global window, and it clears by itself after 60 seconds.

If the API refuses to start after an `.env` edit, read its log: it validates
`ADMIN_PASSWORD` (≥12 characters), `SECRET_KEY` (≥32 characters) and `TZ` at startup and
exits with an explicit message rather than failing later.

Changing `SECRET_KEY` invalidates every existing session cookie — everyone is logged out.
That is a legitimate way to end all sessions.

## The database is growing

Check *Database size* in [Settings](settings.md#the-information-table). Message bodies are
kept in full, forever, and nothing expires automatically except the 7-day event log.

1. Download a backup from the Settings page first.
2. Run *Data cleanup* for 30, 90 or 365 days.
3. Space is marked free but the file does not shrink. To actually reclaim it, `VACUUM` —
   no stop needed, and no extra packages:
   ```bash
   docker compose exec api python3 -c \
     "import sqlite3; sqlite3.connect('/data/gateway.db').execute('VACUUM')"
   ```

## Backup and restore

```bash
# Backup: Settings -> Download database backup (consistent snapshot), or from the shell.
# Note the second step: without it the copy stays inside the same volume it protects.
docker compose exec api python3 -c \
  "import sqlite3; sqlite3.connect('/data/gateway.db').backup(sqlite3.connect('/data/backup.db'))"
docker compose cp api:/data/backup.db ./gateway-backup-$(date +%F).db

# Restore: stop, put the file back, start
docker compose down
docker run --rm -v open-sms-gateway_gateway-data:/data -v "$PWD:/host" debian:bookworm-slim \
  sh -c "rm -f /data/gateway.db-wal /data/gateway.db-shm && \
         cp /host/gateway-backup-<stamp>.db /data/gateway.db"
docker compose up -d
```

Removing the `-wal` and `-shm` sidecars is not optional: the database runs in WAL mode, and
an unclean stop can leave a write-ahead log behind that SQLite would replay on top of your
restored file. Confirm the volume name with `docker volume ls` — it is derived from the
directory name, so a differently named clone gives a differently named volume.

The whole state of the gateway is that one file: messages, tokens, users, webhook
configuration and delivery history. Back it up before any upgrade.

## What is never in the logs

If you are searching the logs for a token, a webhook secret, a password or an SMS body, you
will not find it — none of them are ever written. Search for message ids (`msg_…`) and
delivery ids (`whd_…`) instead.
