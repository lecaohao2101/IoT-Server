"""Shared fixtures.

Every test runs the real application graph -- real validator, real orchestrator,
real WebSocket protocol -- with the memory store and the loopback MQTT transport
swapped in. Nothing here mocks the code under test; it only removes the network.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.container import AppContainer
from app.domain.home import load_home_config
from app.main import create_app
from app.safety.rules import load_safety_policy
from app.safety.validator import CommandValidator

#: Giữa chiều, cách xa khung giờ yên tĩnh 22:00--06:00 của Asia/Ho_Chi_Minh.
DAYTIME = datetime(2026, 3, 2, 14, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Pin the policy clock so the suite does not depend on when it runs.

    Quiet hours change what the policy does with a door unlock: ask for
    confirmation by day, refuse outright by night. Tests that drive the
    orchestrator or the REST API never pass an explicit ``now``, so without this
    they pass all morning and fail every evening -- which is exactly how CI found
    it. ``test_safety.py`` pins its own clock per case and is unaffected.
    """
    monkeypatch.setattr(CommandValidator, "local_now", lambda self: DAYTIME)
    return DAYTIME


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="dev",
        debug=True,
        log_json=False,
        log_level="WARNING",
        api_key=None,
        redis_url=None,
        mqtt_enabled=False,
        stt_provider="mock",
        tts_provider="mock",
        llm_provider="mock",
        silence_timeout_ms=200,
        max_utterance_s=5.0,
        ws_recv_timeout_s=5.0,
    )


@pytest.fixture
def home(settings: Settings):
    return load_home_config(
        settings.resolve(settings.home_config_path), base_topic=settings.mqtt_base_topic
    )


@pytest.fixture
def policy(settings: Settings):
    return load_safety_policy(settings.resolve(settings.safety_config_path))


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def container(settings: Settings):
    built = await AppContainer.create(settings)
    try:
        yield built
    finally:
        await built.aclose()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def wait_for(predicate, timeout: float = 2.0, interval: float = 0.02) -> bool:
    """Poll until ``predicate()`` is truthy. Returns False on timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()
