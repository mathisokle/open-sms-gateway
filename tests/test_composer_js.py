"""The browser-side SMS counter must agree with the server.

static/composer.js duplicates the GSM-7 tables and the segment arithmetic from
gateway/shared/sms.py so the operator sees the cost while typing. Duplication is only
acceptable while it stays exact: the server rejects at 422 what the counter shows as
over the limit, and a drifting table would make the UI quietly lie.

These tests parse the JavaScript source and re-run its algorithm in Python.
"""

import math
import re
from pathlib import Path

import pytest

from gateway.shared.sms import GSM7_BASIC, GSM7_EXTENSION, MAX_BODY_CHARS, MAX_SEGMENTS, count_segments

COMPOSER_JS = Path(__file__).resolve().parents[1] / "gateway" / "api" / "static" / "composer.js"
_SOURCE = COMPOSER_JS.read_text(encoding="utf-8")
_ESCAPES = (("\\n", "\n"), ("\\r", "\r"), ("\\f", "\f"), ('\\"', '"'), ("\\\\", "\\"))


def _region(start: str, end: str) -> str:
    """Slice the source between two markers — the tables contain ';' and '"', so no regex."""
    begin = _SOURCE.index(start) + len(start)
    return _SOURCE[begin : _SOURCE.index(end, begin)]


def _literals(text: str) -> str:
    joined = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', text))
    for escape, literal in _ESCAPES:
        joined = joined.replace(escape, literal)
    return joined


JS_BASIC = frozenset(_literals(_region("var GSM7_BASIC =", "// Extension set")))
JS_EXTENSION = frozenset(_literals(_region("var GSM7_EXTENSION =", "var MAX_SEGMENTS")))


def js_count_segments(body: str) -> int:
    """The algorithm from composer.js, driven by the tables parsed out of it."""
    septets = 0
    gsm7 = True
    for char in body:
        if char in JS_BASIC:
            septets += 1
        elif char in JS_EXTENSION:
            septets += 2
        else:
            gsm7 = False
    if gsm7:
        return 1 if septets <= 160 else math.ceil(septets / 153)
    code_units = len(body.encode("utf-16-le")) // 2  # == JavaScript String.length
    return 1 if code_units <= 70 else math.ceil(code_units / 67)


def test_gsm7_tables_are_identical() -> None:
    assert JS_BASIC == GSM7_BASIC
    assert JS_EXTENSION == GSM7_EXTENSION


def test_limits_are_identical() -> None:
    assert f"var MAX_SEGMENTS = {MAX_SEGMENTS};" in _SOURCE
    assert f"var MAX_BODY_CHARS = {MAX_BODY_CHARS};" in _SOURCE


@pytest.mark.parametrize(
    "body",
    [
        "",
        "OSG: hello",
        "A" * 160,  # last single GSM-7 segment
        "A" * 161,  # first concatenated one
        "A" * 306,
        "A" * 307,
        "[" * 80,  # extension chars cost two septets each
        "[" * 81,
        "ä" * 70,  # still GSM-7 basic
        "€" * 10,
        "ö" * 200,
        "Don’t reply",  # typographic apostrophe forces UCS-2
        "…—•",
        "\U0001f600" * 35,  # surrogate pairs: two UTF-16 code units each
        "\U0001f600" * 36,
        "a\nb\rc",
        "{}|~^\\[]",
        "x" * 459,
    ],
)
def test_segment_counts_match_the_server(body: str) -> None:
    assert js_count_segments(body) == count_segments(body)
