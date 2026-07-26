"""SMS text encoding helpers shared by API (segment count at enqueue) and worker."""

import math

# GSM 03.38 basic character set; extension chars need an escape septet and count double.
# frozensets: O(1) membership so count_segments stays O(n) on long bodies.
GSM7_BASIC = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXTENSION = frozenset("\f€[]{}^~\\|")  # \f (form feed) is a GSM 03.38 extension char

# Hard cap on an enqueued SMS body. 10 segments (1530 GSM-7 / 670 UCS-2 chars) is far
# beyond any legitimate message and bounds cost + modem occupancy per request.
MAX_SEGMENTS = 10
MAX_BODY_CHARS = 1600


def count_segments(body: str) -> int:
    """Number of SMS segments (SPEC §4.1.5): GSM-7 160/153, UCS-2 70/67."""
    if all(ch in GSM7_BASIC or ch in GSM7_EXTENSION for ch in body):
        septets = sum(2 if ch in GSM7_EXTENSION else 1 for ch in body)
        return 1 if septets <= 160 else math.ceil(septets / 153)
    code_units = len(body.encode("utf-16-be")) // 2
    return 1 if code_units <= 70 else math.ceil(code_units / 67)


def body_too_long(body: str) -> bool:
    """True if the body exceeds the char cap or would exceed MAX_SEGMENTS."""
    return len(body) > MAX_BODY_CHARS or count_segments(body) > MAX_SEGMENTS
