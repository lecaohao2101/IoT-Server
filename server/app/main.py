"""Application entry point: ASGI app, lifespan, middleware and error mapping."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    routes_audio,
    routes_chat,
    routes_devices,
    routes_hardware,
    routes_system,
    ws_voice,
)
from app.config import Settings, get_settings
from app.container import AppContainer
from app.core.errors import AppError
from app.core.utils import new_id
from app.logging_setup import setup_logging, trace_id_var

log = logging.getLogger(__name__)

DESCRIPTION = """
Backend for a voice-controlled smart apartment.

* **WebSocket** `/ws/voice` — bidirectional audio for the ESP32 and the mobile app
* **WebSocket** `/ws/events` — live device-state feed for dashboards
* **REST** `/api/v1/...` — catalogue, state, direct control and text conversation

Every command, whether spoken or tapped, passes the same rule-based validator
before it is published to MQTT.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    container = await AppContainer.create(settings)
    app.state.container = container
    log.info(
        "server ready",
        extra={"env": settings.app_env, "port": settings.port, "auth": settings.auth_enabled},
    )
    try:
        yield
    finally:
        await container.aclose()
        log.info("server stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(
        settings.log_level, as_json=settings.log_json, tail_size=settings.log_tail_size
    )

    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = settings

    # Clients authenticate with a Bearer token, never a cookie, so credentialed
    # cross-origin requests are not needed. Combining them with a wildcard origin
    # is also invalid per the CORS spec -- and here it would have let any page a
    # user visits drive the apartment through their browser.
    wildcard = "*" in settings.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=not wildcard,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if wildcard and settings.app_env != "dev":
        log.warning(
            "CORS allows every origin. Set CORS_ORIGINS to the domains that should "
            "be able to call this server.",
            extra={"env": settings.app_env},
        )

    @app.middleware("http")
    async def trace_and_time(request: Request, call_next):
        trace_id = request.headers.get("x-request-id") or new_id("req")
        trace_id_var.set(trace_id)
        started = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        response.headers["x-request-id"] = trace_id
        response.headers["x-response-time-ms"] = f"{elapsed_ms:.1f}"
        if request.url.path not in {"/healthz", "/readyz", "/api/v1/logs"}:
            log.info(
                "http request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round(elapsed_ms, 1),
                },
            )
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.http_status >= 500:
            log.error("request failed", extra={"code": exc.code, "reason": exc.message})
        return JSONResponse(status_code=exc.http_status, content={"error": exc.to_dict()})

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Request body or query is invalid",
                    "details": {"errors": _safe_errors(exc)},
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "Internal server error"}},
        )

    app.include_router(routes_system.router)
    app.include_router(routes_devices.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_hardware.router)
    app.include_router(routes_audio.router)
    app.include_router(ws_voice.router)
    return app


def _safe_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Strip the echoed input out of validation errors: it may contain audio or tokens."""
    return [
        {"loc": [str(p) for p in err.get("loc", ())], "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ][:20]


app = create_app()


def main() -> None:  # pragma: no cover - console entry point
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_config=None,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
