"""Modem driver abstraction — the ONLY place that talks to the modem.

- ModemDriver protocol: connect(), send_sms(to, body) -> segments, fetch_inbound(), status()
- FakeDriver (MODEM_FAKE=1): reads simulated inbound from <data_dir>/fake_inbound.jsonl,
  appends sent messages to <data_dir>/fake_sent.jsonl. Used by ALL tests.
- GammuDriver: python-gammu StateMachine on /dev/modem, at115200. `import gammu` happens
  lazily inside connect() so the package is not required for local development and tests.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any, Protocol

from gateway.shared.clock import utc_now_iso
from gateway.shared.sms import GSM7_BASIC, GSM7_EXTENSION, count_segments

logger = logging.getLogger("gateway.modem")


class ModemError(Exception):
    """Raised by drivers when the modem rejects or fails an operation."""


class ModemConnectionError(ModemError):
    """Connection-level failure (serial port gone): reconnect, do not fail the message."""


class PartialSendError(ModemError):
    """A multipart send failed after some parts already went out. Must NOT be retried
    at the message level (that would re-send the delivered parts)."""


@dataclass(frozen=True)
class InboundSMS:
    msisdn: str
    body: str
    received_at: str  # UTC ISO 8601
    segments: int = 1


@dataclass(frozen=True)
class DeliveryReport:
    """Network status report (DLR) for a previously sent SMS."""

    msisdn: str
    reference: int | None  # TP-MR of the sent message, if known
    delivered: bool
    timestamp: str  # UTC ISO 8601


@dataclass(frozen=True)
class SendResult:
    segments: int
    reference: int | None  # TP-MR assigned by the modem (matches later DLRs)


InboundEvent = InboundSMS | DeliveryReport


@dataclass(frozen=True)
class ModemStatus:
    connected: bool
    signal_percent: int | None
    operator: str | None
    registration: str | None
    own_number: str | None = None  # SIM MSISDN, if the SIM has it provisioned


class ModemDriver(Protocol):
    def connect(self) -> None: ...

    def send_sms(self, to: str, body: str) -> SendResult: ...

    def fetch_inbound(self) -> list[InboundEvent]: ...

    def status(self) -> ModemStatus: ...


class FakeDriver:
    """File-backed fake modem for development and tests (MODEM_FAKE=1).

    fake_inbound.jsonl lines are either text messages ({"from", "body", ...}) or
    delivery reports ({"type": "report", "for": "+41...", "reference": 1, "delivered": true}).
    """

    def __init__(self, data_dir: str | Path) -> None:
        self._data_dir = Path(data_dir)
        self._inbound_path = self._data_dir / "fake_inbound.jsonl"
        self._sent_path = self._data_dir / "fake_sent.jsonl"
        self._next_reference = 0

    def connect(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def send_sms(self, to: str, body: str) -> SendResult:
        segments = count_segments(body)
        self._next_reference += 1
        entry = {
            "to": to,
            "body": body,
            "segments": segments,
            "reference": self._next_reference,
            "sent_at": utc_now_iso(),
        }
        with self._sent_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return SendResult(segments=segments, reference=self._next_reference)

    def fetch_inbound(self) -> list[InboundEvent]:
        if not self._inbound_path.exists():
            return []
        raw = self._inbound_path.read_text(encoding="utf-8")
        self._inbound_path.unlink()
        events: list[InboundEvent] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("type") == "report":
                events.append(
                    DeliveryReport(
                        msisdn=data["for"],
                        reference=data.get("reference"),
                        delivered=bool(data.get("delivered", True)),
                        timestamp=data.get("timestamp") or utc_now_iso(),
                    )
                )
                continue
            events.append(
                InboundSMS(
                    msisdn=data["from"],
                    body=data["body"],
                    received_at=data.get("received_at") or utc_now_iso(),
                    segments=int(data.get("segments", 1)),
                )
            )
        return events

    def status(self) -> ModemStatus:
        return ModemStatus(connected=True, signal_percent=100, operator="FAKE", registration="home")


# Gammu error class names that mean "connection to the modem is gone" -> reconnect,
# everything else is treated as a message-level failure (retry, then failed).
_GAMMU_CONNECTION_ERRORS = (
    "ERR_DEVICENOTEXIST",
    "ERR_DEVICEOPENERROR",
    "ERR_DEVICEREADERROR",
    "ERR_DEVICEWRITEERROR",
    "ERR_NOTCONNECTED",
    "ERR_TIMEOUT",
)


class GammuDriver:
    """Real SIM7600 driver: python-gammu StateMachine, at115200 on /dev/modem.

    Never touched by tests (CONTRIBUTING.md ground rule) — all tests run against FakeDriver.
    The gammu import is lazy so local development and tests work without the package.
    """

    def __init__(self, device: str = "/dev/modem", connection: str = "at115200") -> None:
        self._device = device
        self._connection = connection
        self._gammu: Any = None
        self._sm: Any = None
        self._own_number: str | None = None
        self._pending: list[InboundEvent] = []

    def connect(self) -> None:
        try:
            import gammu
        except ImportError as exc:
            raise ModemConnectionError(f"python-gammu is not installed: {exc}") from exc
        self._gammu = gammu
        # drop any prior StateMachine first: a non-connection error (e.g. ERR_BUSY in
        # status()) leaves self._sm set and still holding the tty fd; opening a second
        # instance can fail on single-open serial drivers -> permanent backoff loop.
        self._sm = None
        sm = gammu.StateMachine()
        sm.SetConfig(0, {"Device": self._device, "Connection": self._connection})
        try:
            sm.Init()
        except gammu.GSMError as exc:
            raise ModemConnectionError(str(exc)) from exc
        self._sm = sm
        self._own_number = self._read_own_number()
        # SIM7600 delivers status reports as direct notifications (+CDS) instead of
        # storing them — without this they never reach the storage polling below.
        try:
            sm.SetIncomingCallback(self._on_incoming)
        except Exception as exc:
            logger.warning("SetIncomingCallback failed: %s: %s", exc.__class__.__name__, exc)
        else:
            try:
                sm.SetIncomingSMS(True)
                logger.info("incoming notifications enabled (delivery reports via +CDS)")
            except Exception as exc:
                logger.warning(
                    "SetIncomingSMS failed (%s: %s); storage polling only",
                    exc.__class__.__name__,
                    exc,
                )

    def _on_incoming(self, _sm: Any, callback_type: str, data: Any) -> None:
        """gammu incoming callback — runs while any gammu command processes the serial buffer."""
        try:
            if callback_type != "SMS" or not isinstance(data, dict):
                return
            if data.get("Type") == "Status_Report":
                delivery_status = data.get("DeliveryStatus")
                self._pending.append(
                    DeliveryReport(
                        msisdn=data.get("Number") or "",
                        reference=data.get("MessageReference"),
                        delivered=delivery_status is not None and delivery_status < 32,
                        timestamp=self._stamp_to_iso(data.get("SMSCDateTime") or data.get("DateTime")),
                    )
                )
                logger.info(
                    "status report via notification: ref=%s status=%s",
                    data.get("MessageReference"),
                    delivery_status,
                )
            elif data.get("Number") and data.get("Text"):
                # full SMS routed directly instead of stored — capture it or it is lost
                self._pending.append(
                    InboundSMS(
                        msisdn=data["Number"],
                        body=data.get("Text") or "",
                        received_at=self._stamp_to_iso(data.get("DateTime")),
                        segments=1,
                    )
                )
                logger.info("inbound sms via notification (not stored on modem)")
            # location-only notifications are ignored: storage polling picks those up
        except Exception:
            logger.exception("failed to handle incoming modem notification")

    def _read_own_number(self) -> str | None:
        """SIM 'own numbers' storage (AT+CNUM) — often empty, so best-effort only."""
        try:
            entry = self._sm.GetMemory(Type="ON", Location=1)
            for item in entry.get("Entries", []):
                value = item.get("Value")
                if isinstance(value, str) and value.startswith("+"):
                    return value
        except Exception:  # missing/unsupported ON storage must never break connect
            logger.info("SIM does not expose its own number (AT+CNUM empty)")
        return None

    def _machine(self) -> Any:
        if self._sm is None:
            raise ModemConnectionError("modem not connected — call connect() first")
        return self._sm

    def _wrap_error(self, exc: Exception) -> ModemError:
        connection_classes = tuple(
            getattr(self._gammu, name) for name in _GAMMU_CONNECTION_ERRORS if hasattr(self._gammu, name)
        )
        if isinstance(exc, connection_classes):
            self._sm = None
            return ModemConnectionError(str(exc))
        return ModemError(str(exc))

    def _send_part(self, part: Any) -> Any:
        try:
            return self._machine().SendSMS(part)
        except self._gammu.GSMError as exc:
            raise self._wrap_error(exc) from exc

    def _send_one(self, part: Any, *, with_report: bool) -> tuple[Any, bool]:
        """Send one encoded part; returns (modem result, whether a DLR was requested).

        Kept as one helper so the DLR-fallback resend goes through the same error
        classification as the first try — otherwise a failure on the retry would escape
        send_sms() unclassified and the message would be retried as a whole.
        """
        if with_report:
            # Gammu convention for requesting a delivery report on a submit.
            part["Type"] = "Status_Report"
        try:
            return self._send_part(part), with_report
        except (ValueError, TypeError):
            # this python-gammu build rejects the DLR field — send without it,
            # the message must never fail because of the report request
            logger.warning("gammu rejected DLR request field, sending without report request")
            part.pop("Type", None)
            return self._send_part(part), False

    def send_sms(self, to: str, body: str) -> SendResult:
        needs_unicode = any(ch not in GSM7_BASIC and ch not in GSM7_EXTENSION for ch in body)
        smsinfo = {
            "Class": -1,
            "Unicode": needs_unicode,
            "Entries": [{"ID": "ConcatenatedTextLong", "Buffer": body}],
        }
        reference: int | None = None
        try:
            parts = self._gammu.EncodeSMS(smsinfo)
        except self._gammu.GSMError as exc:
            raise self._wrap_error(exc) from exc
        request_report = True
        for index, part in enumerate(parts):
            part["SMSC"] = {"Location": 1}
            part["Number"] = to
            try:
                result, request_report = self._send_one(part, with_report=request_report)
            except ModemError as exc:
                if index == 0:
                    # nothing went out yet: a connection loss requeues (loop reconnects),
                    # any other modem error is a retryable message-level failure
                    raise
                # parts already left the modem — retrying the message would duplicate them
                # at the recipient, so fail it even when the cause was a connection loss.
                # _wrap_error already dropped the StateMachine, so the loop reconnects on
                # the next modem call anyway.
                raise PartialSendError(
                    f"multipart send failed at part {index + 1}/{len(parts)}: {exc}"
                ) from exc
            if isinstance(result, int):
                reference = result  # TP-MR of the (last) part; DLRs carry it back
        logger.info("sms sent: parts=%d dlr_requested=%s reference=%s", len(parts), request_report, reference)
        return SendResult(segments=len(parts), reference=reference)

    @staticmethod
    def _stamp_to_iso(stamp: Any) -> str:
        # gammu returns naive local time; astimezone() interprets it in the container TZ
        return stamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if stamp else utc_now_iso()

    def fetch_inbound(self) -> list[InboundEvent]:
        """Read all stored SMS, split off status reports, link multipart, delete from modem."""
        sm = self._machine()
        pending, self._pending = self._pending, []  # events captured via +CDS/+CMT callbacks
        try:
            return self._drain_storage(sm, pending)
        except Exception:
            # a failed storage read must not lose the callback-captured events
            self._pending = pending + self._pending
            raise

    def _drain_storage(self, sm: Any, pending: list[InboundEvent]) -> list[InboundEvent]:
        raw: list[Any] = []
        try:
            entry = None
            while True:
                if entry is None:
                    entry = sm.GetNextSMS(Folder=0, Start=True)
                else:
                    entry = sm.GetNextSMS(Folder=0, Location=entry[0]["Location"])
                raw.append(entry)
        except self._gammu.ERR_EMPTY:
            pass
        except self._gammu.GSMError as exc:
            raise self._wrap_error(exc) from exc

        events: list[InboundEvent] = list(pending)
        locations: list[int] = []
        texts: list[Any] = []
        if raw:
            # diagnostic: shows whether the modem stores status reports where we read
            logger.info(
                "modem inbox entries: %s",
                [
                    {
                        "type": g[0].get("Type"),
                        "state": g[0].get("State"),
                        "folder": g[0].get("Folder"),
                        "ref": g[0].get("MessageReference"),
                    }
                    for g in raw
                ],
            )
        for group in raw:
            first = group[0]
            if first.get("Type") == "Status_Report":
                # GSM 03.40: DeliveryStatus 0-31 means completed/delivered
                delivery_status = first.get("DeliveryStatus")
                events.append(
                    DeliveryReport(
                        msisdn=first["Number"],
                        reference=first.get("MessageReference"),
                        delivered=delivery_status is not None and delivery_status < 32,
                        timestamp=self._stamp_to_iso(first.get("SMSCDateTime") or first.get("DateTime")),
                    )
                )
                locations.extend(part["Location"] for part in group)
            else:
                texts.append(group)

        for group in self._gammu.LinkSMS(texts):
            decoded = self._gammu.DecodeSMS(group)
            if decoded is not None:
                body = "".join(e.get("Buffer") or "" for e in decoded["Entries"])
            else:
                body = "".join(part.get("Text") or "" for part in group)
            first = group[0]
            events.append(
                InboundSMS(
                    msisdn=first["Number"],
                    body=body,
                    received_at=self._stamp_to_iso(first.get("DateTime")),
                    segments=len(group),
                )
            )
            locations.extend(part["Location"] for part in group)
        for location in locations:
            try:
                sm.DeleteSMS(Folder=0, Location=location)
            except self._gammu.GSMError as exc:
                # events are already collected — raising here would lose them all;
                # an undeleted message is re-read next cycle (duplicate beats loss)
                logger.warning("could not delete SMS at location %s: %s", location, exc)
        return events

    def status(self) -> ModemStatus:
        sm = self._machine()
        try:
            signal = sm.GetSignalQuality().get("SignalPercent")
            network = sm.GetNetworkInfo()
        except self._gammu.GSMError as exc:
            raise self._wrap_error(exc) from exc
        operator = network.get("NetworkName") or None
        code = network.get("NetworkCode")
        if not operator and code:
            # resolve raw MCC/MNC ("228 02") to the carrier name via gammu's database
            operator = getattr(self._gammu, "GSMNetworks", {}).get(code) or code
        return ModemStatus(
            connected=True,
            signal_percent=signal if signal is not None and signal >= 0 else None,
            operator=operator,
            registration=network.get("State") or None,
            own_number=self._own_number,
        )
