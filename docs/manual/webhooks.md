# Webhook integration

How to receive inbound SMS as a signed HTTP push: the payload, the headers, signature
verification in four languages, and the rules a correct receiver has to follow.

Configure the URL and see the secret in [Settings](settings.md#webhook-for-inbound-sms).
Watch what happened in [Webhook log](webhook-log.md).

## The flow

```
worker  (polls the modem every 5s)
   modem inbox ──> message row (status = received)
               └─> webhook_deliveries row (pending, due now)
                   [only if a webhook URL and secret are configured]

api     (dispatcher wakes every 10s)
   due rows ──> POST to your URL
            ├─> 2xx within 10s ──> delivered
            └─> anything else  ──> attempt++, retry after 1m / 5m / 30m / 2h / 6h
                                   then failed
```

**Latency.** An inbound SMS reaches your endpoint in roughly 7 seconds typically and about
15 seconds worst case: up to 5 s waiting for the worker's next modem poll, plus up to 10 s
waiting for the dispatcher's next round. Build alerting expectations around the worst case,
not the typical one.

The dispatcher runs inside the API container, not the worker, so a slow endpoint of yours
can never block the modem loop — inbound SMS keep being received and stored regardless. It
does hold up other *webhook* deliveries, though: one round processes every due delivery
sequentially, each waiting up to the full 10-second timeout. A dead endpoint with a backlog
of ten pending deliveries therefore stretches a round to well over a minute.

## The request

```
POST /your/endpoint HTTP/1.1
Content-Type: application/json
X-Gateway-Signature: sha256=9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
X-Gateway-Delivery: whd_01J8ZQ4M7K3P2R5T8V0X1Y2Z3A
```

```json
{"id":"msg_01J8ZQ...","type":"sms.received","from":"+41791234567","to":"gateway","body":"Yes, that works","received_at":"2026-07-25T18:00:00Z"}
```

| Field | Notes |
|---|---|
| `id` | The message id. Also usable against `GET /api/v1/messages/{id}` |
| `type` | Always `sms.received` today. Branch on it so new types do not break you |
| `from` | The sender as the network reported it. **Not guaranteed E.164** |
| `to` | The literal string `gateway`. Single-tenant: there is only one number |
| `body` | The message text, verbatim, including newlines and emoji |
| `received_at` | Network timestamp, UTC, `YYYY-MM-DDTHH:MM:SSZ` |

The JSON is serialised compactly (no spaces after separators) with non-ASCII characters
emitted literally as UTF-8, not `\u` escaped. Both matter for signature verification: the
signature covers those exact bytes.

| Header | Meaning |
|---|---|
| `X-Gateway-Signature` | `sha256=` plus the hex HMAC-SHA256 of the raw body under your secret |
| `X-Gateway-Delivery` | Delivery record id. **Stable across all retries** — your idempotency key |

## Verifying the signature

Compute `HMAC-SHA256(secret, raw_request_body)`, hex-encode it, prefix `sha256=`, and
compare against the header in constant time.

Two rules that break implementations:

1. **Use the raw bytes.** Not the parsed object re-serialised — key order, spacing and
   Unicode escaping would all differ and the HMAC would not match. Read the body as bytes
   first, verify, then parse.
2. **Compare in constant time.** `==` on a signature string leaks timing.

### Python (FastAPI)

```python
import hashlib
import hmac
import json

from fastapi import FastAPI, Header, HTTPException, Request

SECRET = "whsec_..."
app = FastAPI()


@app.post("/sms")
async def receive(request: Request, x_gateway_signature: str = Header("")) -> dict:
    raw = await request.body()
    expected = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_gateway_signature):
        raise HTTPException(status_code=401, detail="bad signature")
    payload = json.loads(raw)
    enqueue(payload)  # hand off, do not process inline
    return {"ok": True}
```

### Node (Express)

```js
const express = require("express");
const crypto = require("crypto");

const SECRET = "whsec_...";
const app = express();

// raw body, not express.json() — the signature covers the exact bytes
app.post("/sms", express.raw({ type: "application/json" }), (req, res) => {
  const expected =
    "sha256=" + crypto.createHmac("sha256", SECRET).update(req.body).digest("hex");
  const got = req.get("X-Gateway-Signature") || "";
  const ok =
    expected.length === got.length &&
    crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(got));
  if (!ok) return res.sendStatus(401);
  const payload = JSON.parse(req.body.toString("utf8"));
  enqueue(payload);
  res.sendStatus(200);
});
```

### PHP

```php
$raw = file_get_contents('php://input');
$expected = 'sha256=' . hash_hmac('sha256', $raw, $secret);
$got = $_SERVER['HTTP_X_GATEWAY_SIGNATURE'] ?? '';
if (!hash_equals($expected, $got)) {
    http_response_code(401);
    exit;
}
$payload = json_decode($raw, true);
```

### Shell (for testing)

```bash
printf '%s' "$RAW_BODY" | openssl dgst -sha256 -hmac "$SECRET" -r | cut -d' ' -f1
```

Prefix the result with `sha256=` to get the exact header value. The `-r` plus `cut` matters:
without them `openssl dgst` prints a `SHA2-256(stdin)= <hex>` prefix that you cannot compare
to the header directly.

## Rules for a correct receiver

**Answer 2xx quickly.** The dispatcher gives you 10 seconds. Anything slower counts as a
failure and is retried. Acknowledge first, process afterwards — write the payload to a
queue or a table and return 200 immediately.

**Be idempotent.** The same message can arrive more than once, and the three causes need
different keys:

| Cause | Deduplicate on |
|---|---|
| A network retry after your 200 was lost in transit | `X-Gateway-Delivery` — it is stable across every attempt of one delivery |
| A manual retry from the [Webhook log](webhook-log.md) | the payload's `id` — the same message, the same delivery record |
| The worker re-reading an SMS the modem never deleted, after a crash | **neither key helps** |

That third case is the one to plan for: a re-read produces a brand-new message row with a
fresh `id` *and* a fresh delivery record, so both identifiers differ while the text is
identical. If duplicates would be harmful, deduplicate on content — sender plus body plus a
short `received_at` window. It is rare, and it is the deliberate cost of never losing a
message.

**Do not expect ordering.** Deliveries are processed by due time; a message that failed
once and succeeded on retry arrives after messages received later. Order by `received_at`
if order matters.

**Do not redirect.** The dispatcher does not follow redirects, and treats 3xx as a failure.
Configure the final URL directly.

**Reject unsigned requests.** Your endpoint is reachable by anyone who finds it. Without
signature verification, anyone can inject fake SMS into your system.

**Treat `body` as hostile input.** It is attacker-controlled text from an arbitrary phone.
Escape it before rendering, parameterise it before querying, never pass it to a shell.

**Do not trust `from` to be E.164.** Short codes (`ADVERT`, `12345`) arrive as-is and will
break a strict phone-number parser.

## Failure handling

Six attempts across roughly nine hours (immediately, then +1m, +5m, +30m, +2h, +6h), after
which the delivery is `failed` and a warning lands in [Logs](logs.md). Failed deliveries
are never retried automatically; there is a manual button in the
[Webhook log](webhook-log.md).

Because the retry window is long but finite, **a webhook is not a durable queue**. If your
receiver was down for a day, reconcile with the polling API afterwards:

```
GET /api/v1/messages?direction=inbound&since=<last one you processed>&limit=200
```

Belt and braces: many operators run the webhook for latency and a slow reconciliation poll
for completeness.

## Rotating the secret

Rotation is instant with no overlap window — the next delivery uses the new secret. Either:

- accept a short gap: rotate, then deploy; the failed attempts recover on their retry
  schedule, or
- make the receiver accept two secrets during the changeover, deploy that first, then
  rotate, then remove the old one.

## Testing without a modem

Run the gateway with `MODEM_FAKE=1` in your `.env`. Under Docker you must also comment out
the `devices:` block in the `worker` service of `docker-compose.yml` — with no dongle
present the mapped host path does not exist and the container refuses to start.

Then feed the fake driver an inbound message. `/data` lives inside the containers, on the
`gateway-data` volume, so write to it from inside the worker rather than from the host:

```bash
docker compose exec worker sh -c \
  "echo '{\"from\":\"+41791234567\",\"body\":\"test inbound\"}' >> /data/fake_inbound.jsonl"
```

Running the worker directly on your machine instead, the file sits next to whatever
`DATABASE_PATH` points at:

```bash
echo '{"from":"+41791234567","body":"test inbound"}' >> ./fake_inbound.jsonl
```

The worker picks it up on its next 5-second pass, stores it, and — if a webhook is
configured — dispatches it exactly as a real message. The file is consumed on read.

## Local development

To receive webhooks on your machine without exposing it, point the webhook URL at a tunnel,
or run a throwaway receiver on the LAN:

```python
# python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn
# .venv/bin/uvicorn recv:app --host 0.0.0.0 --port 9000
#
# A venv is not optional on current Debian, Ubuntu and Raspberry Pi OS: a system-wide
# pip install aborts with "error: externally-managed-environment" (PEP 668).
from fastapi import FastAPI, Request

app = FastAPI()


@app.post("/sms")
async def sms(request: Request) -> dict:
    print(request.headers.get("X-Gateway-Delivery"), await request.body())
    return {"ok": True}
```

Then set the webhook URL to `http://<your-ip>:9000/sms`. Remember that the gateway resolves
that URL from **inside its container**, so `localhost` will not work — use the LAN address.
