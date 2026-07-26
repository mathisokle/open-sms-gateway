# Users

Admin accounts for this panel. Create additional logins, change their passwords, and delete
them. The account from your `.env` file is always present and cannot be removed here.

These accounts control the admin panel only. API access is governed by
[API tokens](api-tokens.md), which are entirely separate.

## The two kinds of account

| Kind | Defined in | Can be deleted here | Password changed here |
|---|---|---|---|
| **`.env` admin** | `ADMIN_USER` / `ADMIN_PASSWORD` | no | no |
| **Database user** | this page | yes | yes |

The `.env` admin is the **lockout escape hatch**. Login checks database users first, then
falls back to the environment credentials, so however badly you mangle the user list you
can always get back in by editing `.env` and restarting the API container. That is also why
deleting users is not treated as a dangerous operation: each delete asks for a single
confirmation, and nothing stops you from removing every database user — you cannot lock
yourself out by doing so.

It also means the `.env` admin's password is only changeable by editing `.env` and
restarting. It is shown in the list with an `.env` badge and a *managed via .env* note
instead of a password field.

## Creating a user

Username and password, then *Create*.

| Rule | Value |
|---|---|
| Username length | 1–64 characters |
| Username characters | Must be printable — no control characters |
| Username uniqueness | Must not already exist, and must not equal `ADMIN_USER` |
| Password length | At least 8 characters |

A username that collides with the `.env` admin is rejected with 422: allowing it would make
which credential wins ambiguous, and the fallback must stay predictable.

Note the asymmetry with `.env`, where `ADMIN_PASSWORD` must be at least **12** characters
and the API refuses to start otherwise. The 8-character floor here is a minimum, not a
recommendation — these accounts have full control of the gateway.

## Changing a password

Each database user's row carries a password field and a *Change password* button. The new
password takes effect immediately, but **existing sessions are not invalidated** — a signed
session cookie stays valid for its full 12 hours regardless. If you are changing a password
because it leaked, delete the user instead, then recreate it; deletion does end the
session (see below).

## Deleting a user

*Delete* removes the account after a confirmation prompt, and it acts as a kill switch:
every request from an `/admin` page re-checks that the session's username still exists in
the database, so a deleted user's open tabs are bounced to the login screen on their next
click. There is no wait for the cookie to expire.

The `.env` admin is exempt from that check — it has no database row to look up and is
always accepted.

## Passwords and sessions

- Passwords are stored as **PBKDF2-HMAC-SHA256** hashes, 200 000 iterations, with a 16-byte
  random salt per user, in the form `pbkdf2$<iterations>$<salt>$<hash>`. Verification is a
  constant-time comparison. Plaintext is never stored or logged. The iteration count is
  deliberately high enough to be felt: logging in takes a noticeable fraction of a second,
  which is the point.
- Login is rate limited to **10 attempts per minute** across the whole panel (not per
  username); exceeding it returns `429` and the message *Too many attempts — wait a minute*.
- An unknown username still costs a full password-hashing round, so response timing does
  not reveal whether an account exists.
- The session cookie `gateway_admin` is signed with `SECRET_KEY`, `HttpOnly`,
  `SameSite=Lax`, and valid for **12 hours**. Set `SESSION_COOKIE_SECURE=1` when serving
  over TLS so it is never sent over plain HTTP.
- *Log out* deletes the cookie. It does not invalidate the signature server-side — a copy
  of the cookie captured beforehand remains valid until it expires. Over a trusted LAN this
  is a non-issue; over the internet, run TLS.

## Practical advice

The single-operator setup this gateway is built for rarely needs more than the `.env`
admin. Add database users when you want to hand someone their own login so you can revoke
it individually, and keep the `.env` credentials as the break-glass account you do not use
day to day.

Because there is one role and no permissions, every account here can send messages, read
every message body, create API tokens, purge data and restart both containers. There is no
read-only admin.
