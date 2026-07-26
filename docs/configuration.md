# Configuration

The gateway is configured in two places. **Environment variables** in `.env` are read at startup and
need a restart to take effect. **Runtime settings** live in the database and are editable in the
admin panel while the gateway runs.

## Environment variables

Set these in `.env` next to `docker-compose.yml`. `.env.example` is the annotated template. The file
is gitignored — never commit it.

### Required

| Variable | Constraint | Meaning |
|---|---|---|
| `ADMIN_PASSWORD` | at least 12 characters | Password for the `ADMIN_USER` login. This account is the permanent fallback and cannot be deleted from the panel, so you can never lock yourself out. |
| `SECRET_KEY` | at least 32 characters | Signs the admin session cookie. Generate with `openssl rand -hex 32`. Changing it invalidates every open session — which is also how you log everyone out globally. |

Both are validated at startup. If either is missing or too short, the containers **refuse to
start** and print every problem at once rather than running with weak protection:

```
invalid configuration: ADMIN_PASSWORD must be at least 12 characters;
SECRET_KEY is not set. Fix these in your .env (see .env.example), then restart:
docker compose up -d
```

The values themselves never appear in that message or anywhere in the logs.

### Modem

| Variable | Default | Meaning |
|---|---|---|
| `HOST_MODEM_DEVICE` | — | Host path of the dongle's **AT port**, mapped into the worker as `/dev/modem`. Always use a `/dev/serial/by-id/...-if02-port0` path, never `/dev/ttyUSB2`: the numbering changes across reboots. See [hardware.md](hardware.md#the-serial-port-that-matters). |
| `MODEM_FAKE` | `0` | `1` swaps the real driver for `FakeDriver` — the whole panel and API work with no hardware. Development and tests only. With `MODEM_FAKE=1` you must comment out the `devices:` block in `docker-compose.yml`, since the host path does not exist. |
| `MESSAGES_PER_MINUTE` | `6` | Send throttle. Must be greater than zero — `0` would stall the outbox silently. |

`MESSAGES_PER_MINUTE` counts **segments, not messages**, because that is what operators count: a
three-part message consumes three tokens. The default of 6 is deliberately conservative. Raising it
does not make your SIM faster; it makes you more visible to your operator's spam filtering. Treat
20/min as a ceiling for a consumer SIM, and read your contract first.

### API and admin

| Variable | Default | Meaning |
|---|---|---|
| `API_PORT` | `8080` | Host port published for the API and the admin panel. |
| `ADMIN_USER` | `admin` | Username of the environment-provided admin account. |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` when the panel is served over HTTPS. The cookie is then only sent over TLS. Setting it to `1` without TLS makes login impossible. |
| `RATE_LIMIT_PER_MINUTE` | `0` (off) | API requests per minute, counted in **one shared window** across all tokens. Over the limit the API answers `429`. |

The rate limit is per gateway, not per token, on purpose: it protects the modem and the Pi, not a
billing quota. One noisy client can therefore consume the whole allowance — that is acceptable in a
single-operator deployment where you control every client.

### Storage and display

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_PATH` | `/data/gateway.db` | SQLite file inside the container, on the `gateway-data` volume. Must be on a real local filesystem — WAL mode is not safe on a network mount. |
| `TZ` | `Europe/Zurich` | Display timezone for the admin panel. Storage is always UTC. An unknown zone fails at startup rather than on every page render. |

## Runtime settings (admin panel)

These live in the `gateway_config` table and take effect without a restart. Both the api and the
worker read them fresh from the shared database.

| Setting | Where | Meaning |
|---|---|---|
| Webhook URL | Settings | Target for inbound SMS. Empty means polling-only — no delivery rows are created. |
| Signature secret | Settings | Generated automatically when a URL is first saved, and rotatable. Rotating it takes effect immediately; update your receiver at the same time or signatures will fail. |
| Gateway number | Settings | The SIM's own number, display-only, shown on the dashboard and in chat headers. Manual because the modem cannot read it reliably. |
| Restart worker | Settings | Sets a flag the worker notices within ~5 seconds; it then exits and Docker restarts it. |

Admin accounts created under **Users** are stored in `admin_users` with PBKDF2-HMAC-SHA256 hashes.
Deleting such a user ends their session immediately. The `ADMIN_USER` from the environment is not
listed there and cannot be removed.

## Tuning for your situation

**Alerting only, a few messages a day.** Defaults are right. Leave the rate limit off and keep the
throttle at 6.

**A busier two-way workflow.** Raise `MESSAGES_PER_MINUTE` cautiously to 10–12 and watch for
failures that look like operator filtering. Set `RATE_LIMIT_PER_MINUTE` to something above your
expected peak so a buggy client cannot fill the queue.

**Exposed through a reverse proxy.** Set `SESSION_COOKIE_SECURE=1`, enable the Caddy service in
`docker-compose.yml`, and read [security.md](security.md) before opening any port.

**Long retention.** Nothing expires by default except the event log (7 days). Messages accumulate
until you purge them under Settings → Data cleanup. A year of light use is a few megabytes.

## Verifying what is actually loaded

The Settings page shows the effective values of the send throttle, rate limit, timezone, database
size and message count. It is the quickest way to confirm a `.env` change was picked up:

```bash
docker compose up -d          # after editing .env
docker compose logs --tail 20 worker
```

A configuration change that did not take effect almost always means `.env` is not in the same
directory as `docker-compose.yml`, or the containers were reloaded rather than recreated.
