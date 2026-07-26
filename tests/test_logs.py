"""Structured JSON logs, one shared setup for api and worker (SPEC §8)."""

import json
import logging

from gateway.shared.logs import JsonFormatter, setup_logging


def make_record(msg: str, *, level: int = logging.INFO, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord(
        name="gateway.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )


def test_formatter_emits_one_json_line_with_core_fields() -> None:
    record = logging.LogRecord(
        name="gateway.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    line = JsonFormatter().format(record)

    entry = json.loads(line)
    assert entry["message"] == "hello world"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "gateway.test"
    assert entry["ts"].endswith("Z")
    assert "\n" not in line


def test_formatter_includes_exception_text() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = make_record("failed", level=logging.ERROR, exc_info=sys.exc_info())

    entry = json.loads(JsonFormatter().format(record))

    assert entry["level"] == "ERROR"
    assert "ValueError: boom" in entry["exc"]


def test_formatter_keeps_umlauts_readable() -> None:
    entry = json.loads(JsonFormatter().format(make_record("message with umlauts äöü")))

    assert entry["message"] == "message with umlauts äöü"


def test_setup_logging_is_idempotent() -> None:
    setup_logging()
    setup_logging()

    root = logging.getLogger()
    json_handlers = [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]
    assert len(json_handlers) == 1
