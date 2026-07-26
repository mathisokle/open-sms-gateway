# API tokens

Bearer tokens for the REST API: create, revoke and delete them here. The plaintext is
shown exactly once, at creation, and never again.

Every call to `/api/v1/*` needs one. The admin panel uses a session cookie instead and is
unaffected by anything on this page.

## Creating a token

Give it a label that says where it will be used — `production`, `monitoring`, `n8n` — and
press *Create token*. The next page shows the plaintext once:

```
sms_3f9a2c7e1b0d4a58c6e2f7a91d3b8c04
```

**Copy it now.** It is stored as a SHA-256 hash, so nobody, including you, can recover it
afterwards. If you lose it, revoke the token and create a new one — there is no other
recovery path and that is the point.

The token page is served with `Cache-Control: no-store` so the plaintext does not land in
a browser cache or a proxy.

### Format

`sms_` followed by 32 hexadecimal characters from a cryptographically secure source. The
first eight characters (`sms_3f9a`) are kept in the clear as the **prefix**, which is how
you tell tokens apart in the list without being able to reconstruct them.

## Using a token

```bash
curl -H "Authorization: Bearer sms_3f9a2c7e1b0d4a58c6e2f7a91d3b8c04" \
  http://<gateway>:8080/api/v1/messages
```

The header is `Authorization: Bearer <token>`, case-insensitive on the scheme, and extra
spaces between the scheme and the token are tolerated. A missing, malformed, unknown or
revoked token gets `401` with a `WWW-Authenticate: Bearer` header.

All tokens have identical, full access to the API. There are no scopes and no read-only
tokens — this is a single-tenant relay, and a token that can list messages could always
read the same data another way. Separate tokens exist so you can revoke one caller without
disturbing the others.

## The list

| Column | Meaning |
|---|---|
| Prefix | First 8 characters — how you identify a token |
| Label | Whatever you typed at creation |
| Created | When it was created |
| Last used | Last successful authentication |
| Status | `active` or `revoked` |

**Last used is deliberately coarse.** It is only rewritten when the stored value is more
than 60 seconds old, so a token hammering the API updates it once a minute rather than on
every request. SQLite serialises writers regardless of how fast the disk is, and the worker
competes for the same lock, so a write per API call would be a real contention problem on
whatever the gateway runs on. Treat this field as "active within the last minute", not as a
request log.

An empty *Last used* on a token you deployed hours ago means the caller has never
successfully authenticated — check that it is sending the header at all.

## Revoking

*Revoke* takes effect immediately: authentication looks up the hash with
`revoked_at IS NULL`, so the very next request with that token gets a `401`. There is no
cache and no grace period. Revoking twice keeps the original revocation timestamp.

Revoked tokens stay in the list on purpose, so *Last used* remains available as evidence
of when the credential was last exercised.

## Deleting

*Delete* removes the row entirely, and is only offered for tokens that are already revoked.
Attempting to delete an active token is rejected with `422`. Two steps rather than one so a
misplaced click cannot silently drop a live credential — and so that the audit trail
outlives the revocation by at least as long as it takes you to decide.

## Rotation

There is no expiry and no automatic rotation. To rotate without downtime:

1. Create a new token with a new label.
2. Deploy it to the caller.
3. Watch *Last used* on the new token move.
4. Revoke the old one.
5. Delete the old one once you are satisfied nothing broke.

## Security notes

- Tokens are stored **only** as SHA-256 hashes. The database backup contains no usable
  credentials.
- Plaintext tokens never appear in logs, events or error messages.
- The token is a bearer credential: anyone holding it can send SMS at your expense. Treat
  it like a password — environment variables or a secret store, never a repository.
- Over plain HTTP the token crosses the network in the clear. Run the gateway on a trusted
  LAN, or put TLS in front of it. See [Settings](settings.md#exposure-and-tls).
- Rate limiting (`RATE_LIMIT_PER_MINUTE`) is a single shared window across all tokens, not
  per token. One noisy client can exhaust it for everyone.
