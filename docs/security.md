# Security model

What this gateway protects, what it deliberately does not, and how to deploy it without giving
away your SIM. Read this before you make the panel reachable from anywhere but your LAN.

To report a vulnerability, see [../SECURITY.md](../SECURITY.md).

## The one-sentence version

**This gateway is designed for a trusted network.** Its authentication is solid, but it has no TLS
of its own, no CSRF tokens, and no per-client isolation — so putting it directly on the public
internet is not a supported configuration.

## What an attacker gets

Worth being explicit about the stakes, because they are higher than "some rows in a database":

- **Your phone number.** Anyone who can send through the gateway sends as *you*, from your real
  number. That is a reputation and, in most jurisdictions, a legal exposure.
- **Your messages.** Inbound and outbound bodies are stored in plaintext in SQLite. Password resets
  and 2FA codes sent to that number are readable by anyone with the database file or panel access.
- **Your SIM contract.** A compromised gateway can burn credit or get the SIM disconnected for spam.
- **A foothold.** The containers run as root (see [below](#containers-run-as-root)).

## What is protected

### API tokens

- Format `sms_` plus 32 hex characters — 128 bits of entropy from `secrets`.
- **Stored only as a SHA-256 hash.** The database never contains a usable token. There is no way to
  recover one, which is why the plaintext is displayed exactly once at creation.
- A `token_prefix` is stored alongside for identification in the UI.
- Lookup is by hash, so an invalid or revoked token is indistinguishable from a wrong one: `401`.
- Revocation is immediate and per token; several tokens coexist so you can rotate one consumer
  without touching the others.

Plain SHA-256 without a salt or a slow KDF is correct **here specifically**: the input is a
128-bit random string, not a human-chosen password, so there is no dictionary to run and no rainbow
table to build. Do not copy that reasoning to password storage.

### Admin passwords

- Panel-managed users in `admin_users`: **PBKDF2-HMAC-SHA256, 200 000 iterations**, 16-byte random
  salt per user, stored as `pbkdf2$<iterations>$<salt>$<hash>`, verified with `compare_digest`.
- The `ADMIN_USER` / `ADMIN_PASSWORD` pair from the environment is a permanent fallback that cannot
  be deleted in the panel, so a bad user-management mistake can never lock you out.
- `ADMIN_PASSWORD` must be at least 12 characters or the gateway refuses to start.

### Sessions

- A signed cookie (`itsdangerous`), **not** a server-side session store.
- `HttpOnly`, `SameSite=Lax`, 12-hour maximum age enforced at both signing and verification.
- `SESSION_COOKIE_SECURE=1` adds the `Secure` flag — set it whenever the panel is behind TLS.
- Because sessions are signed with `SECRET_KEY`, **changing `SECRET_KEY` invalidates every session
  at once.** That is the global logout.
- Deleting a database admin user ends their session immediately, since identity is re-checked.

### Login throttle

Admin login is limited to **10 attempts per minute**, answering `429` beyond that. It blunts online
guessing; it is not a substitute for a strong password, because the window is shared rather than
per-account or per-IP.

### Webhook signatures

Every inbound-SMS delivery carries:

```
X-Gateway-Signature: sha256=<hex>     # HMAC-SHA256 over the raw request body
X-Gateway-Delivery: whd_...           # stable across retries — an idempotency key
```

Your receiver must compute the HMAC over the **raw bytes**, before JSON parsing, and compare in
constant time. Re-serialising the parsed JSON changes byte order and whitespace, and the signature
will not match. See [manual/webhooks.md](manual/webhooks.md) for working code.

The signature authenticates *the gateway to your receiver*. It does not encrypt anything — use an
`https://` webhook URL so the message body is not readable in transit.

### Secrets never reach the logs

A hard rule of the codebase, enforced in the logging setup and in configuration error handling:
plaintext tokens, `webhook_secret`, `ADMIN_PASSWORD`, `SECRET_KEY` and SMS bodies are never logged.
Configuration errors name the *variable*, never the value — including the pydantic validation path,
which would otherwise dump the whole settings object into the traceback.

Logs reference `msg_...` and `whd_...` identifiers so an incident can be investigated without
message contents leaving the database.

### Other hardening in place

- **No OpenAPI document is served.** The default FastAPI docs UI loads JS and CSS from a CDN, which
  breaks the offline requirement, and `openapi.json` would expose the whole admin surface to
  unauthenticated clients.
- **Manual rendering escapes everything.** The in-repo Markdown renderer HTML-escapes every text run
  and allows only `#`, `/`, `http`, `https` and `mailto` link targets, so a stray tag in a document
  can never become live markup.
- **Doc slugs cannot traverse.** `/admin/docs/<slug>` is matched against a registry and a strict
  pattern; `../SPEC` and its encoded variants are 404.
- **`no-new-privileges:true`** on both containers.
- **The API is not mounted in the worker**, and the worker holds the only modem handle.

## Known limitations

These are conscious trade-offs for a single-operator tool on a Pi. Know them before you deploy.

### No CSRF tokens

The panel's state-changing forms are protected only by the `SameSite=Lax` cookie attribute. That
stops cross-site *form posts* in every current browser, but it is weaker than a per-form token.

**Consequence:** do not browse untrusted sites in the same browser profile in which you keep an
admin session open, and do not expose the panel publicly. If you need the panel on a hostile
network, put it behind an authenticating proxy or a VPN rather than relying on `SameSite`.

### No TLS of its own

The gateway speaks plain HTTP. On a LAN that is a deliberate simplification. Anywhere else, both
your API tokens and your message bodies would cross the network in the clear.

### Containers run as root

The worker needs the mapped serial device, and running as root avoids a volume-ownership migration
for existing installations. `no-new-privileges` is set, but a remote-code-execution bug in the api
container would be root inside that container.

### The database is not encrypted

`/data/gateway.db` is plaintext SQLite. Anyone with the volume, a backup file, or physical access to
the SD card can read every message. If the Pi can be stolen, use full-disk encryption at the OS
level and keep backups encrypted.

### The rate limit is global, not per client

`RATE_LIMIT_PER_MINUTE` counts all API requests in one shared window. It protects the modem and the
Pi, not fairness between clients: one misbehaving consumer can consume the whole allowance. It is
also in-memory, so it resets on restart and does not coordinate across processes.

### No audit trail of message content changes

The event log records some significant actions — admin users created, deleted or given a new
password, data purges, test messages, restarts — with 7-day retention. It is an operational log,
not a tamper-evident audit trail, and its coverage is deliberately partial: **API token creation,
revocation and webhook secret rotation write no event at all.** If you need to prove who issued a
credential and when, the gateway cannot tell you.

## Deploying safely

### Recommended: keep it on the LAN

Bind it to a trusted network, reach it over Tailscale/WireGuard when away, and do not forward a
port. Nothing further is needed.

### If it must be reachable: terminate TLS

`docker-compose.yml` ships a commented-out Caddy service that obtains certificates automatically.
Point a hostname at the Pi, write a `Caddyfile`, enable the service, and then:

```ini
SESSION_COOKIE_SECURE=1
```

Also worth doing:

- Restrict `/admin` to your own addresses at the proxy, and expose only `/api/v1` publicly if
  external clients need it.
- Set `RATE_LIMIT_PER_MINUTE` to a real value once the API is reachable from outside.
- Publish the API port only to the interface you intend — an unqualified `ports:` entry in Compose
  listens on every interface.

### Operational hygiene

- **One token per consumer, labelled.** Revoking then becomes a targeted action rather than an
  outage.
- **Rotate the webhook secret** if a receiver is ever compromised; update the receiver in the same
  change window, since rotation takes effect immediately.
- **Rotate `SECRET_KEY`** to force every admin session out.
- **Never commit `.env`.** It is gitignored; keep it that way.
- **Encrypt backups.** A database backup is a full copy of every message.
- **Watch `/healthz`** from outside the gateway, and treat a 503 as an incident: a wedged worker
  means alerts are not being delivered.

## Reporting a vulnerability

Please do not open a public issue for security problems. The process is in
[../SECURITY.md](../SECURITY.md).
