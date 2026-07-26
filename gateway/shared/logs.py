"""Structured JSON logging to stdout — one shared setup for api and worker (SPEC §8).

Hard rule (CONTRIBUTING.md): never log secrets, tokens, webhook bodies or SMS contents.
Log identifiers (message/delivery ids) instead of payloads.
"""

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, str] = {
            "ts": datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: int = logging.INFO) -> None:
    """Install a single JSON stdout handler on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if isinstance(handler.formatter, JsonFormatter):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
