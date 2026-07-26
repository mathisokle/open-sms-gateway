# Operations

Running a gateway over time: backups, updates, monitoring, reading logs, and what to do when the
hardware misbehaves. Symptom-first fault finding for operators lives in
[manual/troubleshooting.md](manual/troubleshooting.md); this document is the administrator's side.

## Health and monitoring

`GET /healthz` needs no authentication and is the one endpoint worth monitoring:

```bash
curl -s http://<gateway>:8080/healthz
```

```json
{
  "status": "ok",
  "modem": {"connected": true, "signal_percent": 68, "operator": "Example Mobile"},
  "queue_depth": 0,
  "worker_seen_at": "2026-01-15T18:00:00Z"
}
```

It returns **503** with `"status": "degraded"` when the worker's heartbeat is older than 120
seconds — meaning the worker process is gone or wedged, regardless of whether the api container
looks fine. That is the condition to alert on.

Two more signals worth watching:

- **`queue_depth` climbing and not draining** means the worker cannot send: no signal, exhausted
  credit, or a stuck modem.
- **`modem.connected: false`** while the container runs means the serial port disappeared. The
  worker retries forever, so this often heals itself.

A minimal external check, from another machine:

```bash
curl -fsS --max-time 10 http://<gateway>:8080/healthz | grep -q '"status": "ok"' \
  || echo "gateway unhealthy"
```

Do not monitor the gateway *with* the gateway. If SMS alerting is its job, something else has to
watch it.

## Reading logs

Both containers log structured JSON to stdout, one object per line.

```bash
docker compose logs -f --tail 100 api      # API requests, admin actions, webhook dispatch
docker compose logs -f --tail 100 worker   # modem loop, sending, receiving, reconnects
```

Readable with `jq`:

```bash
docker compose logs --no-log-prefix worker \
  | jq -r '.ts + "  " + .level + "  " + .message'
```

**Secrets, tokens, passwords and message bodies never appear in the logs.** That is a hard rule of
the codebase, not a setting. Logs reference message and delivery identifiers instead, so to
investigate a specific message you search for its `msg_...` id and look the body up in the panel.

The admin panel's **Logs** page shows a curated event log — significant, low-volume events from the
worker, the webhook dispatcher and admin actions — with 7-day retention. It is complementary to the
stdout logs, not a replacement.

## Backup and restore

The entire state of the gateway is one SQLite file: `/data/gateway.db`. Messages, tokens, webhook
configuration, admin users and the event log are all in it. Nothing else needs backing up except
your `.env`.

### Backup

The reliable way is the button in the panel: **Settings → Download database backup**. It uses
SQLite's online backup API, so the snapshot is consistent even while the worker is writing. Copying
the file with `cp` while the gateway runs can capture a torn WAL state — do not do that.

Equivalent from the shell:

```bash
docker compose exec api python3 -c \
  "import sqlite3; sqlite3.connect('/data/gateway.db').backup(sqlite3.connect('/data/backup.db'))"
docker compose cp api:/data/backup.db ./gateway-backup-$(date +%F).db
```

A scheduled version, keeping 30 days:

```bash
# crontab -e   (on the host, in the repo directory)
0 3 * * * cd /home/pi/open-sms-gateway && docker compose exec -T api python3 -c \
  "import sqlite3; sqlite3.connect('/data/gateway.db').backup(sqlite3.connect('/data/backup.db'))" \
  && docker compose cp api:/data/backup.db "/home/pi/backups/gateway-$(date +\%F).db" \
  && find /home/pi/backups -name 'gateway-*.db' -mtime +30 -delete
```

Store copies off the Pi. A backup on the same SD card that dies with it is not a backup.

### Restore

Stop the gateway first — restoring underneath a running process corrupts state.

```bash
docker compose down

docker run --rm \
  -v open-sms-gateway_gateway-data:/data \
  -v "$PWD:/host" \
  debian:bookworm-slim \
  cp /host/gateway-backup-2026-01-15.db /data/gateway.db

docker compose up -d
```

Confirm the volume name with `docker volume ls` — it is derived from the directory name, so a
differently-named clone gives a differently-named volume.

**Checkpoint after restore:** log in, and confirm under Messages that the expected history is
present. API tokens keep working: their hashes are in the restored file. Admin sessions survive too,
since they depend on `SECRET_KEY`, not on the database.

## Updating

```bash
cd open-sms-gateway

# 1. Back up (Settings → Download database backup, or the shell command above)
# 2. Fetch and rebuild
git pull
docker compose up -d --build

# 3. Verify
docker compose ps
curl -s localhost:8080/healthz
```

Schema migrations are numbered SQL scripts applied automatically at startup and recorded in
`schema_version`. They are **additive only** — an existing migration is never edited — so a newer
codebase can always open an older database.

Two things that surprise people when updating an older installation:

- **A `.env` that used to be accepted can now be rejected.** Minimum lengths for `ADMIN_PASSWORD`
  and `SECRET_KEY` are enforced at startup. The container will say so and refuse to run; fix the
  values and `docker compose up -d`.
- **Changing `SECRET_KEY` logs everyone out.** Changing `ADMIN_PASSWORD` does not affect existing
  sessions, nor the admin users created in the panel.

### Rolling back

```bash
docker compose down
git checkout <previous-tag-or-commit>
docker compose up -d --build
```

If the newer version applied a migration, restore the pre-update backup as well — old code does not
understand a newer schema.

## Capacity and retention

- **Throughput** is bounded by the modem and the throttle, not by the software: at the default
  6 segments/minute that is about 8 600 segments a day, and a consumer SIM will not tolerate
  anywhere near that sustained.
- **Storage** is negligible. A message row with a 160-character body is a few hundred bytes;
  100 000 messages is well under 100 MB.
- **The event log** self-prunes after 7 days.
- **Messages never expire on their own.** Settings → Data cleanup deletes messages older than a
  chosen age together with their webhook delivery rows.

`VACUUM` is not run automatically. After a large purge, reclaim the space explicitly:

```bash
docker compose exec api python3 -c \
  "import sqlite3; sqlite3.connect('/data/gateway.db').execute('VACUUM')"
```

## Recurring situations

**The dongle vanished.** `/healthz` reports 503 or `connected: false`, and the worker log shows
`modem loop error, reconnecting in …s`. The worker reconnects forever with 5→60 s backoff and queued
messages stay in the database, so this is usually self-healing. If it does not return: check
`ls -l /dev/serial/by-id/` on the host, replug the dongle, then
`docker compose restart worker`. Confirm ModemManager is still disabled.

**Messages fail while the modem stays connected.** Statuses run `queued → sending → failed` with a
reason in `error`. That pattern is the SIM's problem, not the gateway's — credit exhausted, the
recipient blocked, or operator filtering. Failed messages are **not** retried automatically;
receiving usually keeps working.

**A container restart-loops after an update.** `docker compose logs --tail 20 worker` names the
cause. Configuration problems print as `invalid configuration: …` and list every issue at once.

**Webhook deliveries pile up as failed.** Your receiver is returning non-2xx or timing out. The
backoff chain is 1m, 5m, 30m, 2h, 6h, then `failed`. Fix the receiver, then use the manual retry in
the Webhook Log. Check that you verify the signature over the raw body —
[manual/webhooks.md](manual/webhooks.md).

**Nothing is being received.** Confirm the number by texting it from a phone. If the panel shows
nothing within ~10 seconds, check that the worker is alive (`/healthz`) and that the SIM's message
store is not full — the worker deletes messages from the modem as it reads them, but a store filled
before the gateway existed can block new arrivals.

## Moving to new hardware

1. Back up the database and copy `.env` off the old Pi.
2. Prepare the new Pi per [installation.md](installation.md), up to and including step 5.
3. Put `.env` in place, correcting `HOST_MODEM_DEVICE` — the by-id path contains the dongle's
   serial number, so it differs if you also swapped the dongle.
4. `docker compose up -d --build`, then stop it and restore the database as described above.
5. Move the SIM. The gateway number setting travels inside the database.
