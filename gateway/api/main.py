"""FastAPI application factory.

The engine is created in the lifespan (not at import time) so importing this
module never touches the filesystem; uvicorn gateway.api.main:app still works.
All error responses use the SPEC §5 format {"error": {"code", "message"}}.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from gateway.api import routes_admin, routes_api
from gateway.api.ratelimit import RateLimiter
from gateway.api.routes_admin import AdminAuthRequired
from gateway.api.webhooks import dispatcher_loop
from gateway.shared.config import Settings, get_settings
from gateway.shared.db import create_db_engine, run_migrations
from gateway.shared.logs import setup_logging
from gateway.shared.models import GatewayStatus, Message

HEARTBEAT_MAX_AGE_SECONDS = 120
LOGIN_ATTEMPTS_PER_MINUTE = 10  # brute-force brake on /admin/login (single-admin system)

_ERROR_CODES = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    422: "validation_error",
    429: "rate_limited",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging()
        app.state.settings = settings or get_settings()
        engine = create_db_engine(app.state.settings.database_path)
        run_migrations(engine)
        app.state.engine = engine
        app.state.rate_limiter = RateLimiter(app.state.settings.rate_limit_per_minute)
        app.state.login_limiter = RateLimiter(LOGIN_ATTEMPTS_PER_MINUTE)
        webhook_client = httpx.AsyncClient()
        app.state.webhook_task = asyncio.create_task(dispatcher_loop(engine, webhook_client))
        yield
        app.state.webhook_task.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.webhook_task
        await webhook_client.aclose()
        engine.dispose()

    # No OpenAPI schema / Swagger UI: the default docs pull JS/CSS from a CDN (violates the
    # offline-LAN rule) and openapi.json would expose the whole admin surface to any
    # unauthenticated client. This is a single-tenant relay with a documented REST API.
    app = FastAPI(title="SMS Gateway", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.exception_handler(AdminAuthRequired)
    async def admin_auth_handler(_request: Request, _exc: AdminAuthRequired) -> RedirectResponse:
        return RedirectResponse("/admin/login", status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _ERROR_CODES.get(exc.status_code, "error")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": str(exc.detail)}},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        message = f"{location}: {first.get('msg', 'invalid request')}" if location else "invalid request"
        return JSONResponse(
            status_code=422, content={"error": {"code": "validation_error", "message": message}}
        )

    @app.get("/healthz")
    def healthz(request: Request) -> JSONResponse:
        with Session(request.app.state.engine) as session:
            rows = {row.key: row.value for row in session.query(GatewayStatus).all()}
            queue_depth = session.query(Message).filter(Message.status == "queued").count()
        heartbeat = rows.get("worker_heartbeat")
        fresh = False
        if heartbeat:
            age = datetime.now(UTC) - datetime.fromisoformat(heartbeat)
            fresh = age.total_seconds() <= HEARTBEAT_MAX_AGE_SECONDS
        signal = rows.get("signal_percent") or None
        body = {
            "status": "ok" if fresh else "degraded",
            "modem": {
                "connected": rows.get("modem_connected") == "1",
                "signal_percent": int(signal) if signal else None,
                "operator": rows.get("operator") or None,
            },
            "queue_depth": queue_depth,
            "worker_seen_at": heartbeat,
        }
        return JSONResponse(status_code=200 if fresh else 503, content=body)

    app.include_router(routes_api.router, prefix="/api/v1")
    app.include_router(routes_admin.public_router)
    app.include_router(routes_admin.router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    return app


app = create_app()
