"""Admin panel: server-rendered Jinja2 + htmx, session-cookie auth (SPEC §3, §4.5).

`public_router` carries login/logout (no session required); `router` carries all
protected pages and actions. Missing/invalid session raises AdminAuthRequired,
which the app-level handler turns into a 303 redirect to /admin/login.
"""

import os
import re
import secrets
import sqlite3
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from jinja2 import pass_context
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from gateway import __version__
from gateway.api.auth import hash_token
from gateway.api.docs_pages import load_page, neighbours, sections
from gateway.api.passwords import hash_password, verify_password
from gateway.shared import ids
from gateway.shared.clock import utc_now_iso
from gateway.shared.configstore import (
    OWN_NUMBER,
    RESTART_WORKER,
    WEBHOOK_SECRET,
    WEBHOOK_URL,
    get_config,
    set_config,
)
from gateway.shared.events import record_event
from gateway.shared.models import AdminUser, ApiToken, Event, GatewayStatus, Message, WebhookDelivery
from gateway.shared.sms import MAX_SEGMENTS, body_too_long, count_segments

E164_RE = re.compile(r"^\+[1-9][0-9]{6,14}$")

TEMPLATES_DIR = Path(__file__).parent / "templates"
SESSION_COOKIE = "gateway_admin"
SESSION_MAX_AGE_SECONDS = 12 * 3600
TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"
GITHUB_URL = "https://github.com/mathisokle/open-sms-gateway"
LOGIN_FAILED = "Login failed."
LOGIN_RATE_LIMITED = "Too many attempts — wait a minute."

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

templates.env.globals["APP_VERSION"] = __version__
templates.env.globals["GITHUB_URL"] = GITHUB_URL


def new_webhook_secret() -> str:
    return "whsec_" + secrets.token_hex(16)


def new_api_token() -> str:
    """Plaintext token per SPEC §2: sms_<32 hex>. Stored only as SHA-256 hash."""
    return "sms_" + secrets.token_hex(16)


@pass_context
def _localdt(context: dict, value: str | None) -> str:
    """Render a UTC ISO timestamp in the configured display timezone (SPEC §4.6)."""
    if not value:
        return "—"
    tz = ZoneInfo(context["request"].app.state.settings.tz)
    return datetime.fromisoformat(value).astimezone(tz).strftime("%Y-%m-%d %H:%M")


templates.env.filters["localdt"] = _localdt


class AdminAuthRequired(Exception):
    """Raised when an /admin route is hit without a valid session cookie."""


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt="admin-session")


def create_session_value(secret_key: str, username: str) -> str:
    return _serializer(secret_key).dumps({"u": username})


def require_admin(request: Request) -> None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise AdminAuthRequired
    settings = request.app.state.settings
    try:
        data = _serializer(settings.secret_key).loads(raw, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired) as exc:
        raise AdminAuthRequired from exc
    # kill-switch: deleting an admin user must end their session at once. The .env admin
    # is the permanent fallback and always valid; DB users must still exist.
    username = data.get("u") if isinstance(data, dict) else None
    if username == settings.admin_user:
        return
    with Session(request.app.state.engine) as session:
        exists = session.query(AdminUser).filter(AdminUser.username == username).one_or_none()
    if exists is None:
        raise AdminAuthRequired


public_router = APIRouter(prefix="/admin")
router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


# --- login / logout (no session required) ---


@public_router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": None})


# computed once at import: unknown usernames must cost a PBKDF2 round too (timing parity)
_TIMING_DUMMY_HASH = hash_password("timing-equalizer")


def _credentials_valid(request: Request, username: str, password: str) -> bool:
    """DB admin users first, .env admin as always-working fallback (lockout safety)."""
    with Session(request.app.state.engine) as session:
        user = session.query(AdminUser).filter(AdminUser.username == username).one_or_none()
    if user is not None:
        if verify_password(password, user.password_hash):
            return True
    else:
        verify_password(password, _TIMING_DUMMY_HASH)
    settings = request.app.state.settings
    # compare as bytes: compare_digest raises TypeError on non-ASCII str input
    user_ok = secrets.compare_digest(username.encode(), settings.admin_user.encode())
    password_ok = bool(settings.admin_password) and secrets.compare_digest(
        password.encode(), settings.admin_password.encode()
    )
    return user_ok and password_ok


@public_router.post("/login")
def login(
    request: Request,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
):
    if not request.app.state.login_limiter.allow("admin-login"):
        return templates.TemplateResponse(
            request, "login.html", {"error": LOGIN_RATE_LIMITED}, status_code=429
        )
    if not _credentials_valid(request, username, password):
        return templates.TemplateResponse(request, "login.html", {"error": LOGIN_FAILED}, status_code=401)
    settings = request.app.state.settings
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_value(settings.secret_key, username),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )
    return response


@public_router.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# --- dashboard (SPEC §4.5) ---


def _today_bounds_utc(tz_name: str) -> tuple[str, str]:
    tz = ZoneInfo(tz_name)
    start_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_local.astimezone(UTC).strftime(TIMESTAMP_FMT)
    end = (start_local + timedelta(days=1)).astimezone(UTC).strftime(TIMESTAMP_FMT)
    return start, end


CHART_WIDTH = 480
CHART_PLOT_HEIGHT = 96
CHART_HEIGHT = 118  # plot + x-axis labels


def _activity_chart(session: Session, tz_name: str) -> dict:
    """Stacked per-hour bars (outbound/inbound) for the last 24h, as SVG geometry."""
    now_hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = now_hour - timedelta(hours=23)
    counts = [{"out": 0, "inb": 0} for _ in range(24)]
    rows = (
        session.query(Message.created_at, Message.direction)
        .filter(Message.created_at >= start.strftime(TIMESTAMP_FMT))
        .all()
    )
    for created_at, direction in rows:
        index = int((datetime.fromisoformat(created_at) - start).total_seconds() // 3600)
        if 0 <= index < 24:
            counts[index]["out" if direction == "outbound" else "inb"] += 1
    peak = max((c["out"] + c["inb"] for c in counts), default=0) or 1
    tz = ZoneInfo(tz_name)
    slot = CHART_WIDTH / 24
    bars = []
    for index, count in enumerate(counts):
        h_out = round(count["out"] / peak * CHART_PLOT_HEIGHT, 1)
        h_inb = round(count["inb"] / peak * CHART_PLOT_HEIGHT, 1)
        hour_local = (start + timedelta(hours=index)).astimezone(tz)
        bars.append(
            {
                "x": round(index * slot + 2, 1),
                "width": round(slot - 4, 1),
                "h_out": h_out,
                "y_out": round(CHART_PLOT_HEIGHT - h_out, 1),
                "h_inb": h_inb,
                "y_inb": round(CHART_PLOT_HEIGHT - h_out - h_inb, 1),
                "out": count["out"],
                "inb": count["inb"],
                "label": hour_local.strftime("%H"),
                "show_label": index % 4 == 0 or index == 23,
            }
        )
    return {
        "bars": bars,
        "peak": peak,
        "width": CHART_WIDTH,
        "height": CHART_HEIGHT,
        "plot": CHART_PLOT_HEIGHT,
    }


SENT_STATUSES = ("sent", "delivered")

DONUT_RADIUS = 40
DONUT_CIRCUMFERENCE = 2 * 3.14159 * DONUT_RADIUS


def _status_donut(session: Session) -> dict:
    """Outbound status distribution as animated SVG donut segments."""
    counts = [
        ("delivered", session.query(Message).filter(Message.status == "delivered").count()),
        ("sent", session.query(Message).filter(Message.status == "sent").count()),
        ("queued", session.query(Message).filter(Message.status.in_(("queued", "sending"))).count()),
        ("failed", session.query(Message).filter(Message.status == "failed").count()),
    ]
    total = sum(count for _, count in counts)
    segments = []
    offset = 0.0
    for status, count in counts:
        if not count:
            continue
        dash = count / total * DONUT_CIRCUMFERENCE
        segments.append(
            {"status": status, "count": count, "dash": round(dash, 1), "offset": round(-offset, 1)}
        )
        offset += dash
    return {"total": total, "segments": segments, "circumference": round(DONUT_CIRCUMFERENCE, 1)}


TREND_WIDTH = 480
TREND_PLOT = 92
TREND_HEIGHT = 112


def _daily_trend(session: Session, tz_name: str) -> dict:
    """Sent vs received per local day, last 7 days — SVG polyline geometry."""
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    days = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    start_iso = days[0].astimezone(UTC).strftime(TIMESTAMP_FMT)
    rows = session.query(Message.created_at, Message.direction).filter(Message.created_at >= start_iso).all()
    out_counts = [0] * 7
    in_counts = [0] * 7
    for created_at, direction in rows:
        local_day = datetime.fromisoformat(created_at).astimezone(tz)
        index = (local_day.date() - days[0].date()).days
        if 0 <= index < 7:
            (out_counts if direction == "outbound" else in_counts)[index] += 1
    peak = max(max(out_counts), max(in_counts), 1)
    step = TREND_WIDTH / 6
    points = []
    for index in range(7):
        x = round(index * step, 1)
        points.append(
            {
                "x": x,
                "y_out": round(TREND_PLOT - out_counts[index] / peak * (TREND_PLOT - 10), 1),
                "y_in": round(TREND_PLOT - in_counts[index] / peak * (TREND_PLOT - 10), 1),
                "out": out_counts[index],
                "inb": in_counts[index],
                "label": days[index].strftime("%d.%m"),
            }
        )
    line_out = " ".join(f"{p['x']},{p['y_out']}" for p in points)
    line_in = " ".join(f"{p['x']},{p['y_in']}" for p in points)
    return {
        "points": points,
        "line_out": line_out,
        "line_in": line_in,
        "peak": peak,
        "width": TREND_WIDTH,
        "height": TREND_HEIGHT,
        "plot": TREND_PLOT,
    }


def _dashboard_context(request: Request) -> dict:
    settings = request.app.state.settings
    start, end = _today_bounds_utc(settings.tz)
    with Session(request.app.state.engine) as session:
        status_rows = {row.key: row.value for row in session.query(GatewayStatus).all()}
        queue_depth = session.query(Message).filter(Message.status == "queued").count()
        sent_today = (
            session.query(Message)
            .filter(Message.status.in_(SENT_STATUSES), Message.sent_at >= start, Message.sent_at < end)
            .count()
        )
        received_today = (
            session.query(Message)
            .filter(
                Message.direction == "inbound",
                Message.received_at >= start,
                Message.received_at < end,
            )
            .count()
        )
        delivered_today = (
            session.query(Message)
            .filter(
                Message.status == "delivered",
                Message.delivered_at >= start,
                Message.delivered_at < end,
            )
            .count()
        )
        failed_today = (
            session.query(Message)
            .filter(Message.status == "failed", Message.created_at >= start, Message.created_at < end)
            .count()
        )
        total_messages = session.query(Message).count()
        sent_total = session.query(Message).filter(Message.status.in_(SENT_STATUSES)).count()
        received_total = session.query(Message).filter(Message.direction == "inbound").count()
        failed_total = session.query(Message).filter(Message.status == "failed").count()
        webhook_ok = session.query(WebhookDelivery).filter(WebhookDelivery.status == "delivered").count()
        webhook_failed = session.query(WebhookDelivery).filter(WebhookDelivery.status == "failed").count()
        active_tokens = session.query(ApiToken).filter(ApiToken.revoked_at.is_(None)).count()
        webhook_configured = bool(get_config(session, WEBHOOK_URL))
        # manual setting wins; otherwise what the SIM reports (often empty)
        own_number = get_config(session, OWN_NUMBER) or status_rows.get("modem_own_number") or None
        chart = _activity_chart(session, settings.tz)
        donut = _status_donut(session)
        trend = _daily_trend(session, settings.tz)
    # Share of concluded outbound that did not fail. Delivered-based rates would sit
    # at 0% forever on modems without status reports (e.g. SIM7600 via gammu).
    concluded = sent_total + failed_total
    success_rate = round(sent_total / concluded * 100) if concluded else None
    webhook_total = webhook_ok + webhook_failed
    webhook_rate = round(webhook_ok / webhook_total * 100) if webhook_total else None
    return {
        "status": status_rows,
        "queue_depth": queue_depth,
        "sent_today": sent_today,
        "received_today": received_today,
        "delivered_today": delivered_today,
        "failed_today": failed_today,
        "total_messages": total_messages,
        "sent_total": sent_total,
        "received_total": received_total,
        "failed_total": failed_total,
        "success_rate": success_rate,
        "webhook_rate": webhook_rate,
        "active_tokens": active_tokens,
        "webhook_configured": webhook_configured,
        "own_number": own_number,
        "chart": chart,
        "donut": donut,
        "trend": trend,
    }


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html", _dashboard_context(request))


@router.get("/partials/stats", response_class=HTMLResponse)
def dashboard_stats_partial(request: Request) -> HTMLResponse:
    """Polled by htmx every few seconds to keep the dashboard live."""
    return templates.TemplateResponse(request, "_stats.html", _dashboard_context(request))


@router.get("/backup")
def download_backup(request: Request) -> FileResponse:
    """Consistent sqlite snapshot via the backup API, served as a download."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    source = sqlite3.connect(request.app.state.settings.database_path)
    target = sqlite3.connect(tmp)
    try:
        source.backup(target)
    except BaseException:
        os.unlink(tmp)  # no orphaned copy of the DB (with SMS bodies) in /tmp on failure
        raise
    finally:
        target.close()
        source.close()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return FileResponse(
        tmp,
        filename=f"gateway-backup-{stamp}.db",
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store"},  # DB dump must never be cached
        background=BackgroundTask(os.unlink, tmp),
    )


# --- API tokens (SPEC §4.5) ---


@router.get("/tokens", response_class=HTMLResponse)
def tokens_page(request: Request) -> HTMLResponse:
    with Session(request.app.state.engine) as session:
        tokens = session.query(ApiToken).order_by(ApiToken.created_at).all()
        session.expunge_all()
    return templates.TemplateResponse(request, "tokens.html", {"tokens": tokens})


@router.post("/tokens", response_class=HTMLResponse)
def create_token_action(request: Request, label: Annotated[str, Form()] = "") -> HTMLResponse:
    plaintext = new_api_token()
    with Session(request.app.state.engine) as session:
        session.add(
            ApiToken(
                id=ids.token_id(),
                token_hash=hash_token(plaintext),
                token_prefix=plaintext[:8],
                label=label or None,
                created_at=utc_now_iso(),
            )
        )
        session.commit()
    # the ONLY place the plaintext ever appears (SPEC §3) — never cache it
    return templates.TemplateResponse(
        request, "token_created.html", {"token": plaintext}, headers={"Cache-Control": "no-store"}
    )


@router.post("/tokens/{token_id}/revoke")
def revoke_token(request: Request, token_id: str) -> RedirectResponse:
    with Session(request.app.state.engine) as session:
        token = session.get(ApiToken, token_id)
        if token is None:
            raise HTTPException(status_code=404, detail="token not found")
        if token.revoked_at is None:  # keep the original revocation time on a double-revoke
            token.revoked_at = utc_now_iso()
            session.commit()
    return RedirectResponse("/admin/tokens", status_code=303)


@router.post("/tokens/{token_id}/delete")
def delete_token(request: Request, token_id: str) -> RedirectResponse:
    """Remove a token row entirely — only allowed once it is revoked."""
    with Session(request.app.state.engine) as session:
        token = session.get(ApiToken, token_id)
        if token is None:
            raise HTTPException(status_code=404, detail="token not found")
        if token.revoked_at is None:
            raise HTTPException(status_code=422, detail="revoke the token before deleting it")
        session.delete(token)
        session.commit()
    return RedirectResponse("/admin/tokens", status_code=303)


# --- settings: webhook target (SPEC §4.3) ---


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    settings = request.app.state.settings
    with Session(request.app.state.engine) as session:
        webhook_url = get_config(session, WEBHOOK_URL)
        webhook_secret = get_config(session, WEBHOOK_SECRET)
        own_number = get_config(session, OWN_NUMBER)
        message_count = session.query(Message).count()
    try:
        db_size_mb = round(os.path.getsize(settings.database_path) / 1_048_576, 2)
    except OSError:
        db_size_mb = None
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "webhook_url": webhook_url,
            "webhook_secret": webhook_secret,
            "own_number": own_number,
            "info": {
                "version": __version__,
                "throttle": settings.messages_per_minute,
                "rate_limit": settings.rate_limit_per_minute,
                "tz": settings.tz,
                "db_size_mb": db_size_mb,
                "message_count": message_count,
            },
        },
        headers={"Cache-Control": "no-store"},  # page shows the webhook secret
    )


@router.post("/purge-messages")
def purge_messages(request: Request, days: Annotated[int, Form()] = 90) -> RedirectResponse:
    """Delete messages (and their deliveries) older than the given number of days."""
    if days not in (30, 90, 365):
        raise HTTPException(status_code=422, detail="days must be 30, 90 or 365")
    cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime(TIMESTAMP_FMT)
    with Session(request.app.state.engine) as session:
        # subquery instead of an id list: .in_(<list>) hits SQLite's bound-variable limit
        old_messages = select(Message.id).where(Message.created_at < cutoff)
        purged = session.query(Message).filter(Message.created_at < cutoff).count()
        session.query(WebhookDelivery).filter(WebhookDelivery.message_id.in_(old_messages)).delete(
            synchronize_session=False
        )
        session.query(Message).filter(Message.created_at < cutoff).delete(synchronize_session=False)
        record_event(session, "admin", "info", f"purged {purged} messages older than {days} days")
        session.commit()
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/settings/own-number")
def set_own_number(request: Request, own_number: Annotated[str, Form()] = "") -> RedirectResponse:
    # forgiving input: strip separators, accept 00-prefix international format
    own_number = re.sub(r"[\s\-/()]", "", own_number.strip())
    if own_number.startswith("00"):
        own_number = "+" + own_number[2:]
    if own_number and not E164_RE.fullmatch(own_number):
        raise HTTPException(status_code=422, detail="number must be international, e.g. +41791234567")
    with Session(request.app.state.engine) as session:
        set_config(session, OWN_NUMBER, own_number or None)
        session.commit()
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/settings/webhook")
def set_webhook(request: Request, webhook_url: Annotated[str, Form()] = "") -> RedirectResponse:
    webhook_url = webhook_url.strip()
    if webhook_url and not webhook_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="webhook url must start with http:// or https://")
    with Session(request.app.state.engine) as session:
        if webhook_url:
            set_config(session, WEBHOOK_URL, webhook_url)
            if not get_config(session, WEBHOOK_SECRET):
                set_config(session, WEBHOOK_SECRET, new_webhook_secret())
        else:
            set_config(session, WEBHOOK_URL, None)
            set_config(session, WEBHOOK_SECRET, None)
        session.commit()
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/settings/webhook/rotate-secret")
def rotate_webhook_secret(request: Request) -> RedirectResponse:
    with Session(request.app.state.engine) as session:
        if not get_config(session, WEBHOOK_URL):  # no orphan secret without a target URL
            raise HTTPException(status_code=422, detail="configure a webhook URL first")
        set_config(session, WEBHOOK_SECRET, new_webhook_secret())
        session.commit()
    return RedirectResponse("/admin/settings", status_code=303)


# --- chats: conversation view per number ---


def _enqueue_outbound(session: Session, to: str, body: str) -> Message:
    message = Message(
        id=ids.message_id(),
        direction="outbound",
        msisdn=to,
        body=body,
        status="queued",
        segments=count_segments(body),
        created_at=utc_now_iso(),
    )
    session.add(message)
    return message


@router.get("/chats", response_class=HTMLResponse)
def chats_page(request: Request) -> HTMLResponse:
    with Session(request.app.state.engine) as session:
        recent = session.query(Message).order_by(Message.created_at.desc()).limit(1000).all()
        session.expunge_all()
    chats: dict[str, dict] = {}
    for message in recent:  # newest first -> first hit per number is the latest message
        chat = chats.setdefault(message.msisdn, {"msisdn": message.msisdn, "last": message, "count": 0})
        chat["count"] += 1
    return templates.TemplateResponse(request, "chats.html", {"chats": list(chats.values())})


def _chat_context(request: Request, msisdn: str) -> dict:
    with Session(request.app.state.engine) as session:
        messages = (
            session.query(Message)
            .filter(Message.msisdn == msisdn)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(300)
            .all()
        )
        own_number = get_config(session, OWN_NUMBER)
        session.expunge_all()
    messages.reverse()  # cap keeps the newest 300; render oldest -> newest
    return {"msisdn": msisdn, "messages": messages, "own_number": own_number}


@router.get("/chats/{msisdn}", response_class=HTMLResponse)
def chat_page(request: Request, msisdn: str) -> HTMLResponse:
    return templates.TemplateResponse(request, "chat.html", _chat_context(request, msisdn))


@router.get("/partials/chat/{msisdn}", response_class=HTMLResponse)
def chat_partial(request: Request, msisdn: str) -> HTMLResponse:
    return templates.TemplateResponse(request, "_chat.html", _chat_context(request, msisdn))


@router.post("/chats/{msisdn}/send")
def chat_send(request: Request, msisdn: str, body: Annotated[str, Form()] = ""):
    if not E164_RE.fullmatch(msisdn):
        raise HTTPException(status_code=422, detail="invalid number")
    text = body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="message must not be empty")
    if body_too_long(text):
        raise HTTPException(status_code=422, detail=f"message exceeds {MAX_SEGMENTS} SMS segments")
    with Session(request.app.state.engine) as session:
        _enqueue_outbound(session, msisdn, text)
        session.commit()
    if request.headers.get("HX-Request"):
        # htmx form: return the refreshed thread so the bubble appears without a reload
        return templates.TemplateResponse(request, "_chat.html", _chat_context(request, msisdn))
    return RedirectResponse(f"/admin/chats/{msisdn}", status_code=303)


# --- message browser (SPEC §4.5) ---


@router.get("/messages", response_class=HTMLResponse)
def messages_page(
    request: Request,
    direction: str | None = None,
    status: str | None = None,
    msisdn: str | None = None,
) -> HTMLResponse:
    with Session(request.app.state.engine) as session:
        query = session.query(Message)
        if direction:
            query = query.filter(Message.direction == direction)
        if status:
            query = query.filter(Message.status == status)
        if msisdn:
            query = query.filter(Message.msisdn == msisdn)
        messages = query.order_by(Message.created_at.desc(), Message.id.desc()).limit(100).all()
        session.expunge_all()
    return templates.TemplateResponse(
        request,
        "messages.html",
        {
            "messages": messages,
            "filters": {
                "direction": direction or "",
                "status": status or "",
                "msisdn": msisdn or "",
            },
        },
    )


@router.get("/messages/{message_id}", response_class=HTMLResponse)
def message_detail(request: Request, message_id: str) -> HTMLResponse:
    with Session(request.app.state.engine) as session:
        message = session.get(Message, message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        deliveries = (
            session.query(WebhookDelivery)
            .filter(WebhookDelivery.message_id == message_id)
            .order_by(WebhookDelivery.created_at)
            .all()
        )
        session.expunge_all()
    return templates.TemplateResponse(
        request, "message_detail.html", {"message": message, "deliveries": deliveries}
    )


# --- webhook log (SPEC §4.5) ---


@router.get("/webhooks", response_class=HTMLResponse)
def webhooks_page(request: Request) -> HTMLResponse:
    with Session(request.app.state.engine) as session:
        deliveries = (
            session.query(WebhookDelivery)
            .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
            .limit(100)
            .all()
        )
        session.expunge_all()
    return templates.TemplateResponse(request, "webhooks.html", {"deliveries": deliveries})


@router.post("/webhooks/{delivery_id}/retry")
def retry_delivery(request: Request, delivery_id: str) -> RedirectResponse:
    """Manual re-trigger: back to pending and immediately due (SPEC §4.3)."""
    with Session(request.app.state.engine) as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        if delivery is None:
            raise HTTPException(status_code=404, detail="delivery not found")
        if delivery.status != "failed":  # only failed deliveries may be re-triggered
            raise HTTPException(status_code=409, detail="only failed deliveries can be retried")
        delivery.status = "pending"
        delivery.next_retry_at = utc_now_iso()
        session.commit()
    return RedirectResponse("/admin/webhooks", status_code=303)


# --- test SMS from the dashboard ---


@router.post("/test-sms")
def send_test_sms(
    request: Request, to: Annotated[str, Form()], body: Annotated[str, Form()] = ""
) -> RedirectResponse:
    if not E164_RE.fullmatch(to):
        raise HTTPException(status_code=422, detail="recipient must be E.164 (+41…)")
    text = body.strip() or "Test from Open SMS Gateway"
    if body_too_long(text):
        raise HTTPException(status_code=422, detail=f"message exceeds {MAX_SEGMENTS} SMS segments")
    with Session(request.app.state.engine) as session:
        session.add(
            Message(
                id=ids.message_id(),
                direction="outbound",
                msisdn=to,
                body=text,
                status="queued",
                segments=count_segments(text),
                created_at=utc_now_iso(),
            )
        )
        record_event(session, "admin", "info", f"test SMS queued to …{to[-4:]}")
        session.commit()
    return RedirectResponse("/admin/messages", status_code=303)


# --- admin users ---


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request) -> HTMLResponse:
    with Session(request.app.state.engine) as session:
        users = session.query(AdminUser).order_by(AdminUser.created_at).all()
        session.expunge_all()
    return templates.TemplateResponse(
        request, "users.html", {"users": users, "env_admin": request.app.state.settings.admin_user}
    )


@router.post("/users")
def create_user(
    request: Request, username: Annotated[str, Form()], password: Annotated[str, Form()]
) -> RedirectResponse:
    username = username.strip()
    if not username or len(password) < 8:
        raise HTTPException(status_code=422, detail="username required, password min. 8 characters")
    if len(username) > 64 or not username.isprintable():
        raise HTTPException(status_code=422, detail="username too long or contains control characters")
    if username == request.app.state.settings.admin_user:
        raise HTTPException(status_code=422, detail="username collides with the .env admin")
    with Session(request.app.state.engine) as session:
        exists = session.query(AdminUser).filter(AdminUser.username == username).one_or_none()
        if exists is not None:
            raise HTTPException(status_code=422, detail="username already exists")
        session.add(
            AdminUser(
                id="usr_" + secrets.token_hex(8),
                username=username,
                password_hash=hash_password(password),
                created_at=utc_now_iso(),
            )
        )
        record_event(session, "admin", "info", f"admin user '{username}' created")
        session.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/password")
def change_user_password(
    request: Request, user_id: str, password: Annotated[str, Form()]
) -> RedirectResponse:
    if len(password) < 8:
        raise HTTPException(status_code=422, detail="password min. 8 characters")
    with Session(request.app.state.engine) as session:
        user = session.get(AdminUser, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        user.password_hash = hash_password(password)
        record_event(session, "admin", "info", f"password changed for admin user '{user.username}'")
        session.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/delete")
def delete_user(request: Request, user_id: str) -> RedirectResponse:
    with Session(request.app.state.engine) as session:
        user = session.get(AdminUser, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        record_event(session, "admin", "info", f"admin user '{user.username}' deleted")
        session.delete(user)
        session.commit()
    return RedirectResponse("/admin/users", status_code=303)


# --- event log ---


def _events_context(request: Request, level: str | None, source: str | None) -> dict:
    with Session(request.app.state.engine) as session:
        query = session.query(Event)
        if level:
            query = query.filter(Event.level == level)
        if source:
            query = query.filter(Event.source == source)
        events = query.order_by(Event.id.desc()).limit(200).all()
        session.expunge_all()
    return {
        "events": events,
        "filters": {"level": level or "", "source": source or ""},
    }


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, level: str | None = None, source: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "logs.html", _events_context(request, level, source))


@router.get("/partials/logs", response_class=HTMLResponse)
def logs_partial(request: Request, level: str | None = None, source: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "_logs.html", _events_context(request, level, source))


# --- built-in manual (docs/manual/*.md, rendered by gateway.shared.markdown) ---


@router.get("/docs", response_class=HTMLResponse)
def docs_index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "docs_index.html", {"sections": sections()})


@router.get("/docs/{slug}", response_class=HTMLResponse)
def docs_page(request: Request, slug: str) -> HTMLResponse:
    page = load_page(slug)
    if page is None:
        raise HTTPException(status_code=404, detail="documentation page not found")
    previous_page, next_page = neighbours(slug)
    return templates.TemplateResponse(
        request,
        "docs_page.html",
        {"page": page, "previous_page": previous_page, "next_page": next_page},
    )


# --- restarts (no docker socket needed: containers use restart: unless-stopped) ---


def _terminate_process() -> None:  # pragma: no cover - replaced in tests, exits in prod
    time.sleep(0.5)  # let the redirect response reach the browser first
    os._exit(0)


@router.post("/restart-worker")
def restart_worker(request: Request) -> RedirectResponse:
    """Sets a flag; the worker clears it and exits — docker restarts the container."""
    with Session(request.app.state.engine) as session:
        set_config(session, RESTART_WORKER, utc_now_iso())
        record_event(session, "admin", "warning", "worker restart requested")
        session.commit()
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/restart-api")
def restart_api(request: Request) -> RedirectResponse:
    with Session(request.app.state.engine) as session:
        record_event(session, "admin", "warning", "api restart requested")
        session.commit()
    return RedirectResponse("/admin/settings", status_code=303, background=BackgroundTask(_terminate_process))
