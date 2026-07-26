# Documentation

Two kinds of documents live here. The **operator manual** in [manual/](manual/) is written for the
person running a gateway, and the admin panel renders it under `/admin/docs` with no internet
access. Everything else is written for the person installing, operating or modifying the gateway,
and is meant to be read on GitHub.

## Running a gateway

Read in this order if you are starting from a box of parts:

| Guide | Answers |
|---|---|
| [hardware.md](hardware.md) | What to buy, which port on the dongle matters, why the power supply is not optional |
| [installation.md](installation.md) | Getting from a fresh Linux install to a working gateway, with a checkpoint after every step |
| [configuration.md](configuration.md) | Every environment variable and every runtime setting, with the reasoning behind the defaults |
| [operations.md](operations.md) | Backup, restore, updates, monitoring, reading logs, capacity |
| [security.md](security.md) | What the gateway protects, what it does not, and how to expose it safely |

## Using a gateway

The operator manual is the user-facing documentation. Start at
[manual/getting-started.md](manual/getting-started.md), or jump to what you need:

- [manual/rest-api.md](manual/rest-api.md) — the complete REST API v1 reference
- [manual/webhooks.md](manual/webhooks.md) — receiving inbound SMS on your own endpoint
- [manual/sms-format.md](manual/sms-format.md) — character budgets, alphabets and multipart messages
- [manual/troubleshooting.md](manual/troubleshooting.md) — symptom-first runbook
- [manual/README.md](manual/README.md) — every page, including one per admin menu

## Understanding the code

| Document | Contents |
|---|---|
| [SPEC.md](SPEC.md) | The behavioural contract: what the gateway does, its data model and its API surface. Authoritative when documents disagree. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How it is built: process split, the worker loop, the webhook pipeline, and the decisions behind them. |
| [CODE-MAP.md](CODE-MAP.md) | Which module owns what, and which tests cover it. The fastest way into the codebase. |

Contribution setup and ground rules are in [../CONTRIBUTING.md](../CONTRIBUTING.md).

## Conventions used throughout

- **Timestamps** are UTC ISO 8601 (`2026-01-15T18:00:00Z`) everywhere in the database, the API and
  the logs. The admin panel renders them in the timezone set by `TZ`.
- **Phone numbers** are E.164 with a leading `+` and no spaces (`+41791234567`).
- **Identifiers** carry a type prefix: `msg_` for messages, `tok_` for API tokens, `whd_` for
  webhook deliveries. They are ULIDs, so lexicographic order is creation order.
- **`<gateway>`** in a command means the host or IP your gateway runs on — the same placeholder the
  operator manual uses; **`sms_...`** means an API token.
- Examples use the fictional numbers `+4179xxxxxxx` and the domain `example.com`.

## Screenshots

[screenshots/](screenshots/) holds the admin-panel images used by the README. They are generated
from the fake modem driver with demo data, at a 1600x1000 viewport. They are excluded from the
Docker build context — there is no reason to ship them to a Pi.
