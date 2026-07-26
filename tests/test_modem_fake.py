"""FakeDriver, the hardware-free modem driver (ARCHITECTURE §2).

Inbound is fed via fake_inbound.jsonl, sent messages land in fake_sent.jsonl.
"""

import json
from pathlib import Path

import pytest

from gateway.worker.modem import DeliveryReport, FakeDriver, InboundSMS


@pytest.fixture()
def driver(tmp_path: Path) -> FakeDriver:
    drv = FakeDriver(tmp_path)
    drv.connect()
    return drv


def test_send_appends_to_sent_log_and_returns_one_segment(driver: FakeDriver, tmp_path: Path) -> None:
    result = driver.send_sms("+41791234567", "Hello")

    assert result.segments == 1
    assert result.reference == 1
    lines = (tmp_path / "fake_sent.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["to"] == "+41791234567"
    assert entry["body"] == "Hello"
    assert entry["segments"] == 1
    assert entry["reference"] == 1
    assert entry["sent_at"].endswith("Z")


def test_send_references_increment(driver: FakeDriver) -> None:
    first = driver.send_sms("+41791234567", "a")
    second = driver.send_sms("+41791234567", "b")

    assert (first.reference, second.reference) == (1, 2)


def test_sent_log_accumulates_across_sends(driver: FakeDriver, tmp_path: Path) -> None:
    driver.send_sms("+41791234567", "first")
    driver.send_sms("+41797654321", "second")

    lines = (tmp_path / "fake_sent.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(line)["body"] for line in lines] == ["first", "second"]


def test_long_gsm7_text_is_split_into_concatenated_segments(driver: FakeDriver) -> None:
    assert driver.send_sms("+41791234567", "a" * 160).segments == 1
    assert driver.send_sms("+41791234567", "a" * 161).segments == 2
    assert driver.send_sms("+41791234567", "a" * 306).segments == 2
    assert driver.send_sms("+41791234567", "a" * 307).segments == 3


def test_gsm7_extension_chars_count_double(driver: FakeDriver) -> None:
    assert driver.send_sms("+41791234567", "€" * 80).segments == 1  # 160 septets
    assert driver.send_sms("+41791234567", "€" * 81).segments == 2  # 162 septets


def test_non_gsm7_text_uses_ucs2_limits(driver: FakeDriver) -> None:
    assert driver.send_sms("+41791234567", "漢" * 70).segments == 1
    assert driver.send_sms("+41791234567", "漢" * 71).segments == 2
    assert driver.send_sms("+41791234567", "漢" * 134).segments == 2
    assert driver.send_sms("+41791234567", "漢" * 135).segments == 3


def test_fetch_inbound_parses_delivery_reports(driver: FakeDriver, tmp_path: Path) -> None:
    (tmp_path / "fake_inbound.jsonl").write_text(
        '{"type": "report", "for": "+41791234567", "reference": 3}\n'
        '{"type": "report", "for": "+41790000000", "reference": 4, "delivered": false, '
        '"timestamp": "2026-07-24T10:00:00Z"}\n',
        encoding="utf-8",
    )

    events = driver.fetch_inbound()

    assert events[0] == DeliveryReport(
        msisdn="+41791234567", reference=3, delivered=True, timestamp=events[0].timestamp
    )
    assert events[0].timestamp.endswith("Z")
    assert events[1] == DeliveryReport(
        msisdn="+41790000000", reference=4, delivered=False, timestamp="2026-07-24T10:00:00Z"
    )


def test_fetch_inbound_reads_and_deletes_file(driver: FakeDriver, tmp_path: Path) -> None:
    inbound_file = tmp_path / "fake_inbound.jsonl"
    inbound_file.write_text(
        '{"from": "+41791112233", "body": "Hi", "received_at": "2026-07-24T10:00:00Z"}\n'
        '{"from": "+41794445566", "body": "Yo"}\n',
        encoding="utf-8",
    )

    inbound = driver.fetch_inbound()

    assert inbound[0] == InboundSMS(
        msisdn="+41791112233", body="Hi", received_at="2026-07-24T10:00:00Z", segments=1
    )
    assert inbound[1].msisdn == "+41794445566"
    assert inbound[1].received_at.endswith("Z")  # defaults to now when not given
    assert not inbound_file.exists(), "inbound file must be consumed (read & delete semantics)"
    assert driver.fetch_inbound() == []


def test_fetch_inbound_without_file_returns_empty_list(driver: FakeDriver) -> None:
    assert driver.fetch_inbound() == []


def test_status_reports_connected_fake_modem(driver: FakeDriver) -> None:
    status = driver.status()

    assert status.connected is True
    assert status.operator == "FAKE"
    assert status.signal_percent == 100
    assert status.registration == "home"
