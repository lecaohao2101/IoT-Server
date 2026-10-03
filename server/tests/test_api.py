"""End-to-end tests over the real ASGI app (memory store, loopback MQTT)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

# ------------------------------------------------------------------ system


def test_healthz_is_dependency_free(client: TestClient):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_the_wiring(client: TestClient):
    body = client.get("/readyz").json()
    assert body["status"] == "ok"
    assert body["store"]["kind"] == "memory"
    assert body["transport"]["kind"] == "loopback"


def test_system_info_exposes_the_catalogue(client: TestClient):
    body = client.get("/api/v1/system/info").json()
    assert body["home"]["device_count"] > 0
    assert body["providers"]["llm"].startswith("mock")
    assert body["audio"]["sample_rate"] == 16000


def test_request_id_is_echoed(client: TestClient):
    response = client.get("/healthz", headers={"x-request-id": "trace-123"})
    assert response.headers["x-request-id"] == "trace-123"
    assert "x-response-time-ms" in response.headers


# ----------------------------------------------------------------- devices


def test_devices_are_listed_with_seeded_state(client: TestClient):
    body = client.get("/api/v1/devices").json()
    assert body["count"] == len(body["devices"])
    light = next(d for d in body["devices"] if d["id"] == "living_room_light")
    assert light["state"]["power"] == "off"
    assert light["capabilities"]["brightness"]["maximum"] == 100


def test_devices_can_be_filtered_by_room(client: TestClient):
    body = client.get("/api/v1/devices", params={"room": "bedroom"}).json()
    assert body["count"] > 0
    assert {d["room"] for d in body["devices"]} == {"bedroom"}


def test_unknown_room_filter_is_a_404(client: TestClient):
    response = client.get("/api/v1/devices", params={"room": "garage"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unknown_device_is_a_404(client: TestClient):
    assert client.get("/api/v1/devices/nope").status_code == 404


def test_rooms_list_their_devices(client: TestClient):
    rooms = client.get("/api/v1/rooms").json()
    kitchen = next(r for r in rooms if r["id"] == "kitchen")
    assert "kitchen_light" in kitchen["device_ids"]


# ----------------------------------------------------------------- control


def test_command_is_accepted_dispatched_and_echoed_into_state(client: TestClient):
    response = client.post(
        "/api/v1/devices/living_room_light/command",
        json={"capability": "power", "value": "on"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["dispatched"] == 1
    assert body["plan"]["accepted"][0]["value"] == "on"

    # The loopback transport echoes the command back on the state topic.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        state = client.get("/api/v1/devices/living_room_light").json()["state"]
        if state.get("power") == "on":
            break
        time.sleep(0.05)
    assert state["power"] == "on"


def test_command_is_clamped_not_refused(client: TestClient):
    body = client.post(
        "/api/v1/devices/living_room_light/command",
        json={"capability": "brightness", "value": 500},
    ).json()
    assert body["plan"]["accepted"][0]["value"] == 100
    assert body["plan"]["accepted"][0]["notes"]


def test_command_to_a_read_only_capability_is_rejected(client: TestClient):
    body = client.post(
        "/api/v1/devices/balcony_sensor/command",
        json={"capability": "temperature", "value": 20},
    ).json()
    assert body["dispatched"] == 0
    assert body["plan"]["rejected"][0]["code"] == "read_only"


def test_sensitive_command_is_not_dispatched_from_the_api(client: TestClient):
    body = client.post(
        "/api/v1/devices/front_door_lock/command",
        json={"capability": "locked", "value": False},
    ).json()
    assert body["dispatched"] == 0
    assert body["plan"]["pending_confirmation"]


def test_batch_commands_share_one_plan(client: TestClient):
    body = client.post(
        "/api/v1/commands",
        json={
            "commands": [
                {"device_id": "bedroom_light", "capability": "power", "value": "on"},
                {"device_id": "bedroom_light", "capability": "brightness", "value": 35},
            ]
        },
    ).json()
    assert body["dispatched"] == 2
    assert len(body["plan"]["accepted"]) == 2


def test_malformed_command_body_is_422(client: TestClient):
    response = client.post(
        "/api/v1/devices/living_room_light/command", json={"capability": "", "value": "on"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


# ------------------------------------------------------------------ scenes


def test_scene_activation_dispatches_every_action(client: TestClient):
    body = client.post("/api/v1/scenes/movie_time/activate").json()
    assert body["dispatched"] == len(body["plan"]["accepted"]) > 0


def test_unknown_scene_is_a_404(client: TestClient):
    assert client.post("/api/v1/scenes/nope/activate").status_code == 404


# -------------------------------------------------------------------- chat


def test_chat_turn_controls_a_device(client: TestClient):
    body = client.post(
        "/api/v1/chat", json={"text": "bật đèn phòng khách", "room": "living_room"}
    ).json()
    assert body["speech"]
    accepted = body["plan"]["accepted"]
    assert any(c["device_id"] == "living_room_light" and c["value"] == "on" for c in accepted)
    assert body["dispatched"] >= 1


def test_chat_keeps_session_history(client: TestClient):
    session = "sess-test-1"
    client.post("/api/v1/chat", json={"text": "bật đèn bếp", "session_id": session})
    history = client.get(f"/api/v1/chat/{session}").json()
    roles = [t["role"] for t in history["turns"]]
    assert roles[:2] == ["user", "assistant"]

    assert client.delete(f"/api/v1/chat/{session}").status_code == 204
    assert client.get(f"/api/v1/chat/{session}").json()["turns"] == []


def test_chat_asks_back_when_the_target_is_ambiguous(client: TestClient):
    body = client.post("/api/v1/chat", json={"text": "làm gì đó đi"}).json()
    assert body["needs_clarification"] is True
    assert not body["plan"]


def test_speak_returns_playable_wav(client: TestClient):
    response = client.post("/api/v1/speak", json={"text": "Xin chào"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content[:4] == b"RIFF"
    assert len(response.content) > 44


# -------------------------------------------------------------------- auth


@pytest.fixture
def secured_client(settings: Settings):
    # Rebuild rather than model_copy: copying skips validation, and api_key must
    # arrive as a SecretStr the way the environment would deliver it.
    secured = Settings(**{**settings.model_dump(), "api_key": "s3cret", "_env_file": None})
    with TestClient(create_app(secured)) as test_client:
        yield test_client


def test_protected_routes_require_a_credential(secured_client: TestClient):
    assert secured_client.get("/api/v1/devices").status_code == 401
    assert secured_client.get("/healthz").status_code == 200


def test_bearer_token_is_accepted(secured_client: TestClient):
    response = secured_client.get(
        "/api/v1/devices", headers={"Authorization": "Bearer s3cret"}
    )
    assert response.status_code == 200


def test_api_key_header_is_accepted(secured_client: TestClient):
    assert secured_client.get("/api/v1/devices", headers={"x-api-key": "s3cret"}).status_code == 200


def test_websocket_without_a_token_is_closed(secured_client: TestClient):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), secured_client.websocket_connect("/ws/voice") as ws:
        ws.receive_json()
