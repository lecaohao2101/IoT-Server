"""WebSocket voice path.

The mock recogniser transcribes UTF-8 payloads verbatim, so a test can push a
sentence as an "audio" frame and exercise the whole socket -- framing, the
utterance state machine, the turn, synthesis and the final summary -- without a
recording or a cloud key.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _collect(ws, stop_type: str, limit: int = 60):
    """Read frames until ``stop_type`` arrives, keeping text and binary apart."""
    frames: list[dict] = []
    audio: list[bytes] = []
    for _ in range(limit):
        message = ws.receive()
        if message.get("type") == "websocket.disconnect":
            break
        if message.get("bytes") is not None:
            audio.append(message["bytes"])
            continue
        import json

        payload = json.loads(message["text"])
        frames.append(payload)
        if payload.get("type") == stop_type:
            break
    return frames, audio


def test_full_voice_turn_produces_transcript_reply_and_audio(client: TestClient):
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "hello", "room": "living_room", "sample_rate": 16000})
        ready = ws.receive_json()
        assert ready["type"] == "session.ready"
        assert ready["room"] == "living_room"

        ws.send_bytes("bật đèn phòng khách".encode())
        ws.send_json({"type": "audio.end"})

        frames, audio = _collect(ws, "assistant.final")
        kinds = [f["type"] for f in frames]

        assert "stt.final" in kinds
        assert "assistant.delta" in kinds
        assert "tts.start" in kinds
        assert "tts.end" in kinds
        assert kinds[-1] == "assistant.final"
        assert audio, "no synthesised audio was streamed"

        final = frames[-1]
        assert final["text"]
        accepted = final["plan"]["accepted"]
        assert any(c["device_id"] == "living_room_light" for c in accepted)
        assert final["latency_ms"]["total"] >= 0


def test_tts_start_declares_the_encoding_the_client_asked_for(client: TestClient):
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "hello", "reply_encoding": "pcm16"})
        ws.receive_json()
        ws.send_json({"type": "text", "text": "bật đèn bếp"})
        frames, audio = _collect(ws, "assistant.final")
        start = next(f for f in frames if f["type"] == "tts.start")
        assert start["encoding"] == "pcm16"
        assert start["sample_rate"] == 16000
        # PCM is chunked to ~100 ms so the device can start playing immediately.
        assert all(len(chunk) <= 3200 for chunk in audio)


def test_reply_encoding_none_suppresses_audio(client: TestClient):
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "hello", "reply_encoding": "none"})
        ws.receive_json()
        ws.send_json({"type": "text", "text": "bật đèn bếp"})
        frames, audio = _collect(ws, "assistant.final")
        assert not audio
        assert "tts.start" not in [f["type"] for f in frames]


def test_text_frame_skips_recognition_entirely(client: TestClient):
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "hello", "room": "bedroom", "reply_encoding": "none"})
        ws.receive_json()
        ws.send_json({"type": "text", "text": "bật đèn phòng ngủ"})
        frames, _ = _collect(ws, "assistant.final")
        assert "stt.final" not in [f["type"] for f in frames]
        assert frames[-1]["plan"]["accepted"][0]["device_id"] == "bedroom_light"


def test_session_is_ready_even_without_a_hello(client: TestClient):
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_bytes("bật đèn bếp".encode())
        ready = ws.receive_json()
        assert ready["type"] == "session.ready"


def test_ping_is_answered(client: TestClient):
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "hello"})
        ws.receive_json()
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_unknown_control_frame_is_reported_not_fatal(client: TestClient):
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "hello"})
        ws.receive_json()
        ws.send_json({"type": "teleport"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "bad_message"

        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_malformed_json_is_reported(client: TestClient):
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "hello"})
        ws.receive_json()
        ws.send_text("{not json")
        assert ws.receive_json()["type"] == "error"


def test_cancel_is_acknowledged(client: TestClient):
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "hello"})
        ws.receive_json()
        ws.send_json({"type": "cancel"})
        assert ws.receive_json()["type"] == "cancelled"


def test_session_id_is_honoured_and_history_is_shared_with_rest(client: TestClient):
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json(
            {"type": "hello", "session_id": "shared-session", "reply_encoding": "none"}
        )
        assert ws.receive_json()["session_id"] == "shared-session"
        ws.send_json({"type": "text", "text": "bật đèn bếp"})
        _collect(ws, "assistant.final")

    history = client.get("/api/v1/chat/shared-session").json()
    assert [t["role"] for t in history["turns"]][:2] == ["user", "assistant"]


def test_events_socket_streams_state_changes(client: TestClient):
    with client.websocket_connect("/ws/events") as ws:
        assert ws.receive_json()["type"] == "session.ready"
        client.post(
            "/api/v1/devices/kitchen_light/command",
            json={"capability": "power", "value": "on"},
        )
        topics = []
        for _ in range(6):
            event = ws.receive_json()
            topics.append(event["topic"])
            if event["topic"].startswith("device.state"):
                assert event["device_id"] == "kitchen_light"
                break
        assert any(t.startswith("device.state") for t in topics)
