# Settings

Runtime configuration: the inbound webhook, the gateway number, a test-message form, the
read-only environment summary, data cleanup and the restart controls.

Anything on this page takes effect immediately without a restart. Anything **not** on this
page lives in `.env` and does need one — see [Environment variables](#environment-variables).

## Webhook for inbound SMS

The URL every inbound SMS is POSTed to, as signed JSON. Leave it empty to run
polling-only; messages are still stored and still readable through the API either way.

- The URL must start with `http://` or `https://`. Anything else is rejected with 422.
- Saving a URL for the first time **generates a signing secret automatically**.
- Changing an existing URL **keeps the existing secret**. If you are pointing the webhook at
  a different receiver, press *Rotate secret* as well — otherwise the new endpoint is handed
  the secret the old one already knows.
- Clearing the URL clears the secret too — no orphaned secret is left behind.
- Use `https://` in production. Over plain HTTP the message body crosses the network in
  the clear, and the signature only proves authenticity, not confidentiality.

### Signature secret

Shown in full on this page once a webhook is configured, in the form `whsec_<32 hex>`.
Every request carries:

```
X-Gateway-Signature: sha256=<hex hmac_sha256(secret, raw_request_body)>
X-Gateway-Delivery: whd_01J...
```

Verify the signature over the **raw bytes** of the request body, before any JSON parsing,
using a constant-time comparison. Worked examples in several languages are in
[Webhook integration](webhooks.md#verifying-the-signature).

*Rotate secret* generates a new one immediately. There is no overlap window: the very next
delivery is signed with the new secret, so deploy it to your receiver first, or accept a
few failed attempts that the retry schedule will recover once you catch up. Rotation
requires a configured URL (422 otherwise).

This page is served with `Cache-Control: no-store` because it displays the secret.

## Send test SMS

Queues one outbound message, exactly like the API would. The recipient must be **strict
E.164** (`+41791234567`) — this form does *not* accept the forgiving variants; the browser
enforces the pattern before submitting, and the server rejects anything else with 422. Only
the gateway-number field below normalises input, see
[Recipient numbers](sms-format.md#recipient-numbers). Leaving the text empty sends
`Test from Open SMS Gateway`.

You land on [Messages](messages.md) afterwards so you can watch the status. The event is
recorded in [Logs](logs.md) with only the last four digits of the number.

The character counter under the field shows the alphabet and segment count as you type —
see [Message format and syntax](sms-format.md).

## Gateway

### The gateway number

The SIM's own number, in E.164. This is **display only** — it is shown on the
[Dashboard](dashboard.md) and in the [Chats](chats.md) header so you know which number your
recipients see. It is never used for routing and never sent anywhere.

Set it by hand because most SIMs do not publish their MSISDN over `AT+CNUM`. If the SIM
does report one, your manual value still wins.

The field is forgiving: spaces, dashes, slashes and parentheses are stripped, and a leading
`00` becomes `+`. The result must match `^\+[1-9][0-9]{6,14}$` or you get a 422. Clearing
the field is allowed.

### The information table

Read-only, sourced from the environment and the database:

| Row | Where it comes from |
|---|---|
| Version | The application version |
| Send throttle | `MESSAGES_PER_MINUTE` — segments per minute the worker may submit |
| API rate limit | `RATE_LIMIT_PER_MINUTE`, or `off` when 0 |
| Timezone | `TZ` — the display timezone for every timestamp in this panel |
| Database size | Size of the SQLite file on disk |
| Messages stored | Row count in the messages table |

**Watch the database size.** It is the only part of the gateway that grows without bound.
Message bodies are stored in full and forever; use [Data cleanup](#data-cleanup) to keep it
in hand. On a host with a small SD card, that is worth a glance now and then.

## Data cleanup

Deletes messages older than 30, 90 or 365 days, together with their webhook delivery
records. Only those three values are accepted (422 otherwise), and the action is confirmed
before it runs.

This is **permanent and immediate**. There is no soft delete and no undo. Take a backup
first if the data matters — the *Download database backup* link on this page produces a
consistent snapshot of the whole database as a single file, using SQLite's backup API, so
it is safe to take while the gateway is running.

The purge is recorded in [Logs](logs.md) with the number of rows removed.

Note that reclaiming disk space is not immediate: SQLite marks the pages free for reuse but
does not shrink the file. To actually shrink it, run `VACUUM` against the database file
while the gateway is stopped.

## Danger zone

Two restart buttons, both behind a confirmation.

| Button | What happens |
|---|---|
| **Restart worker** | Sets a flag in the database. The worker notices it within ~5 seconds, exits cleanly, and Docker restarts the container |
| **Restart API** | The API process exits immediately after the redirect is sent; Docker restarts it |

Both rely on `restart: unless-stopped` in `docker-compose.yml`, which is why neither
container needs access to the Docker socket. If you run the processes outside Docker, they
will simply exit and stay down.

Restarting the API drops all open connections for a second or two. Restarting the worker
is safe at any time: queued messages stay queued, and anything caught mid-send is requeued
on the next start (see [Messages](messages.md#statuses)).

Use *Restart worker* after replugging the modem, or when the reconnect loop is stuck on a
device path that no longer exists.

## Environment variables

These are read at startup from `.env` and require a container restart to change. They are
not editable here.

| Variable | Default | Meaning |
|---|---|---|
| `API_PORT` | `8080` | Host port published by `docker-compose.yml`. The container always listens on 8080 |
| `ADMIN_USER` | `admin` | Fallback admin username |
| `ADMIN_PASSWORD` | — | Required, at least 12 characters |
| `SECRET_KEY` | — | Required, at least 32 characters. Signs session cookies |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` **only** when serving over TLS. Set it over plain HTTP and the browser drops the session cookie, so login silently loops back to the login page |
| `HOST_MODEM_DEVICE` | — | Host path of the modem's AT port, mapped to `/dev/modem` |
| `MODEM_FAKE` | `0` | `1` runs the fake modem driver — development and tests only |
| `MESSAGES_PER_MINUTE` | `6` | Send throttle in segments per minute. Must be greater than 0 |
| `RATE_LIMIT_PER_MINUTE` | `0` | API rate limit, one shared window. `0` disables it |
| `DATABASE_PATH` | `/data/gateway.db` | SQLite file inside the container |
| `TZ` | `Europe/Zurich` | Display timezone. Storage is always UTC |

The gateway refuses to start if `ADMIN_PASSWORD` or `SECRET_KEY` is missing or too short, or if `TZ`
is not a valid zone name. It reports every problem at once — never the values themselves — so one
restart is enough to see them all. Failing at startup beats failing on every page render, and beats
running with a weak session secret. See
[A container will not start](troubleshooting.md#a-container-will-not-start-restart-loop).

Changing `SECRET_KEY` invalidates every open admin session, which is also the supported way to log
everyone out globally. Changing `ADMIN_PASSWORD` affects neither sessions nor the users on the
[Users](users.md) page.

### Exposure and TLS

The gateway is designed for a trusted LAN. Before exposing it to the internet:

1. Put TLS in front of it — the commented-out Caddy service in `docker-compose.yml` does
   automatic certificates — or reach it over a VPN such as WireGuard or Tailscale.
2. Set `SESSION_COOKIE_SECURE=1` so the session cookie is never sent over plain HTTP.
3. Set a genuinely strong `ADMIN_PASSWORD`. The login is throttled to 10 attempts per
   minute, which slows an attacker down but does not save a weak password.
4. Consider setting `RATE_LIMIT_PER_MINUTE`.

Both containers run as root so the worker can open the modem device; `no-new-privileges` is
set on both. This gateway does not belong on the open internet unprotected.
