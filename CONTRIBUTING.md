# Contributing

Thanks for your interest in Open SMS Gateway. The whole test suite runs against a fake modem
driver, so **you do not need any hardware to contribute** — no dongle, no SIM, no Raspberry Pi.

## Development setup

```bash
git clone https://github.com/mathisokle/open-sms-gateway.git
cd open-sms-gateway
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

pytest                                             # the full suite, no hardware
ruff check . && ruff format --check .
```

`requirements-dev.txt` adds pytest, ruff and httpx. `python-gammu` is **not** in either file and
usually cannot be pip-installed: it is a system package (`python3-gammu` from apt) that the Docker
image provides. The gammu import therefore happens lazily inside `GammuDriver.connect()`, which is
what lets the suite and local development run without it.

Run the services locally:

```bash
export MODEM_FAKE=1 ADMIN_USER=admin ADMIN_PASSWORD=local-dev-password \
       SECRET_KEY=at-least-32-characters-of-local-dev-secret DATABASE_PATH=./dev.db

uvicorn gateway.api.main:app --reload --port 8080   # API + admin panel
python -m gateway.worker.main                       # worker, second terminal
```

### The fake modem

With `MODEM_FAKE=1` the worker loads `FakeDriver` instead of `GammuDriver`. It behaves like a real
modem for everything above the serial line, using two files next to your database:

- **`fake_sent.jsonl`** — every message the worker "sent", one JSON object per line. This is how you
  assert that sending worked.
- **`fake_inbound.jsonl`** — messages *you* write, which the worker reads on its next poll (about
  every 5 seconds) and stores as inbound. Both plain texts and delivery reports are supported.

To simulate someone texting the gateway:

```bash
echo '{"from": "+41791234567", "body": "Hello", "received_at": "2026-01-15T18:00:00Z"}' \
  >> ./fake_inbound.jsonl
```

The message appears under Chats within a few seconds, and a webhook delivery is created if a webhook
is configured. See `tests/test_modem_fake.py` for the exact accepted shapes.

## Ground rules

These are load-bearing. A change that breaks one of them will be asked to change.

- **Tests ship with the code.** Every feature and bugfix comes with tests, and the suite must stay
  green (`pytest`) and lint-clean (`ruff check .`, `ruff format --check .`).
- **Only the worker talks to the modem**, exclusively through the `ModemDriver` protocol. Tests
  never import or instantiate `GammuDriver` — use `FakeDriver` or a stub.
- **Never log or commit secrets.** API tokens are stored as SHA-256 hashes only; plaintext tokens,
  `webhook_secret`, passwords, `SECRET_KEY` and SMS bodies must never appear in a log, an error
  message or a traceback. `.env` is gitignored and stays that way.
- **Schema changes are additive migrations.** Add a new numbered file in
  `gateway/shared/migrations/` — never edit one that has been released, because deployed databases
  have already applied it.
- **All timestamps are UTC ISO 8601** in the database, the API and the logs. Only the admin panel
  localises, using `TZ`.
- **Keep it light.** It runs on any Linux host, but the budget is written against the smallest one
  — a Raspberry Pi 3 (arm64, 1 GB RAM): no heavy dependencies, no build step, no Node, no external
  CDNs, and the panel must work with no internet. Do not change the base image or the apt packages
  in the `Dockerfile` without a reason, and keep it building for both `arm64` and `amd64`.
- **English everywhere** — code, comments, documentation and the admin UI. There is no i18n layer;
  the panel's strings live directly in the templates.
- **Type hints everywhere**, and small modules.

## Working on the documentation

The operator manual in `docs/manual/*.md` is the **single source** for both the GitHub copy and the
panel's Docs section. Do not duplicate its content elsewhere; link to it.

It is rendered by `gateway/shared/markdown.py`, a deliberately small in-repo renderer, so the
manual must stay inside the subset it supports:

- ATX headings, paragraphs, fenced code blocks, GFM pipe tables, nested lists, blockquotes,
  thematic breaks, and inline code / bold / italic / links
- **no raw HTML and no images** — there is no image syntax, so `![...](...)` renders as a broken
  link and the dead-link test fails
- links to other manual pages are written the GitHub way, `settings.md#anchor`, and rewritten to
  `/admin/docs/settings#anchor` at render time
- every page starts with an `# H1` title followed by exactly one summary paragraph, and needs at
  least one `##` heading for its table of contents

`tests/test_admin_docs.py` enforces the shape and fails on any dead cross-page link or unresolved
`#fragment`, so a typo in a link is caught before merge. Screenshots and other images belong in the
README or the guides under `docs/`, which are GitHub-only and not rendered by the panel.

## Pull requests

1. Fork, branch from `main`, and keep the change focused.
2. Make sure `pytest`, `ruff check .` and `ruff format --check .` pass, and that
   `docker compose config` is still valid if you touched Compose or the Dockerfile.
3. Describe what changed and why. Screenshots are appreciated for UI changes.

Please open an issue before starting something large. The project is deliberately narrow in scope —
single SIM, single operator, no multi-tenancy — and a feature that widens that is likely to be
declined no matter how well it is written.

## Security

Do not report vulnerabilities in a public issue. See [SECURITY.md](SECURITY.md).
