# Open SMS Gateway — operator manual

The source of the manual that ships inside the admin panel under **Docs** (`/admin/docs`). These
files are the single source: the gateway renders them at runtime, and they are written to be
readable as-is on GitHub or copied into a wiki.

## Start here

| Page | Contents |
|---|---|
| [Getting started](getting-started.md) | What the gateway is, the two processes, five steps to the first SMS |
| [Message format and syntax](sms-format.md) | The house format, GSM-7 vs UCS-2, segments, links, limits, compliance |

## Admin menus

One page per entry in the panel's navigation.

| Page | Contents |
|---|---|
| [Dashboard](dashboard.md) | Every tile, chart and modem field, and how each number is computed |
| [Chats](chats.md) | Conversation view, status ticks, the reply box |
| [Messages](messages.md) | Message browser, filters, statuses, the detail page, error texts |
| [API tokens](api-tokens.md) | Creating, using, revoking and rotating bearer tokens |
| [Webhook log](webhook-log.md) | Delivery attempts, the retry schedule, manual retry |
| [Logs](logs.md) | The event log, what each event means, retention |
| [Users](users.md) | Admin accounts, the `.env` fallback, passwords and sessions |
| [Settings](settings.md) | Webhook, gateway number, data cleanup, restarts, environment variables |

## Integration and operations

| Page | Contents |
|---|---|
| [REST API](rest-api.md) | Full v1 reference: endpoints, parameters, errors, paging |
| [Webhook integration](webhooks.md) | Payload, signature verification, receiver rules |
| [Troubleshooting](troubleshooting.md) | Symptom-first runbook |

## Beyond the manual

This manual covers *using* a running gateway. Installing and administering one is documented
outside the panel, on GitHub:

| Guide | Contents |
|---|---|
| [Hardware](../hardware.md) | Dongle, SIM, power, the AT port, what to buy |
| [Installation](../installation.md) | Any Linux host with a free USB port, end to end (a Raspberry Pi 3 is the reference target) |
| [Configuration](../configuration.md) | Every environment variable and runtime setting |
| [Operations](../operations.md) | Backup, restore, updates, monitoring |
| [Security](../security.md) | Threat model and safe exposure |

## Conventions

- Links between manual pages use plain GitHub-style targets (`settings.md`,
  `sms-format.md#links-in-sms`). The admin panel rewrites them to its own routes when it renders,
  so both work.
- Every page starts with an `# H1` title followed by a one-paragraph summary. The panel lifts those
  two out for the page header and the index cards, so keep the shape.
- `##` and `###` headings become the on-page table of contents.
- Only the Markdown subset implemented by `gateway/shared/markdown.py` is available: headings,
  paragraphs, fenced code, pipe tables, nested lists, blockquotes, thematic breaks, and inline
  code, bold, italic and links. **No raw HTML, no images, no footnotes.**
- This index page is not part of the panel's page registry — it is a GitHub landing page, which is
  why it may link outside `docs/manual/`. The 13 registered pages may not.
