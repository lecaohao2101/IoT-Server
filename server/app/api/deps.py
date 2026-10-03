"""FastAPI dependencies: container access and credential checks."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Query, Request, WebSocket

from app.container import AppContainer
from app.core.errors import UnauthorizedError
from app.core.security import Principal, authenticate, extract_bearer


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - only during a failed startup
        raise RuntimeError("application container is not initialised")
    return container


def get_ws_container(websocket: WebSocket) -> AppContainer:
    container = getattr(websocket.app.state, "container", None)
    if container is None:  # pragma: no cover
        raise RuntimeError("application container is not initialised")
    return container


Container = Annotated[AppContainer, Depends(get_container)]
WsContainer = Annotated[AppContainer, Depends(get_ws_container)]


def require_principal(
    container: Container,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
    x_device_id: Annotated[str | None, Header()] = None,
) -> Principal:
    token = extract_bearer(authorization) or x_api_key
    return authenticate(container.settings, token=token, device_id=x_device_id)


CurrentPrincipal = Annotated[Principal, Depends(require_principal)]


def authenticate_websocket(
    container: AppContainer,
    websocket: WebSocket,
    token: str | None,
    device_id: str | None,
) -> Principal:
    """Resolve a WebSocket credential from the query string or the headers.

    ESP32 WebSocket stacks frequently cannot set headers on the upgrade request,
    so ``?token=`` is a supported, documented path -- not a fallback.
    """
    header_token = extract_bearer(websocket.headers.get("authorization")) or websocket.headers.get(
        "x-api-key"
    )
    resolved = token or header_token
    if not resolved and container.settings.auth_enabled:
        raise UnauthorizedError("Missing credential")
    return authenticate(
        container.settings,
        token=resolved,
        device_id=device_id or websocket.headers.get("x-device-id"),
    )


WsToken = Annotated[str | None, Query(alias="token")]
WsDeviceId = Annotated[str | None, Query(alias="device_id")]
