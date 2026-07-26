# Tests

All tests run without hardware: `FakeDriver` instead of a modem, and a fresh temporary SQLite
database per test. `GammuDriver` is never imported or instantiated (a ground rule from
`CONTRIBUTING.md`).

File names follow the module under test (`test_worker.py`, `test_api_messages.py`,
`test_admin_*.py`, `test_webhooks.py`, …). [Module map](../docs/CODE-MAP.md) lists which
tests cover which part of the system.

```bash
pytest                       # the whole suite
pytest tests/test_worker.py  # a single file
```
