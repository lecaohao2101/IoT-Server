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


def test_one_socket_carries_several_utterances_back_to_back(client: TestClient):
    """The ESP32 holds this socket open for the whole conversation.

    Each ``audio.start`` opens a fresh utterance on the same session, so a second
    sentence must be recognised and acted on without reconnecting.
    """
    with client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "hello", "room": "living_room", "reply_encoding": "none"})
        assert ws.receive_json()["type"] == "session.ready"

        said = ["bật đèn phòng khách", "tắt đèn phòng khách"]
        devices = []
        for sentence in said:
            ws.send_json({"type": "audio.start"})
            ws.send_bytes(sentence.encode())
            ws.send_json({"type": "audio.end"})

            frames, audio = _collect(ws, "assistant.final")
            assert not audio, "reply_encoding=none must not stream audio"
            transcripts = [f["text"] for f in frames if f["type"] == "stt.final"]
            assert sentence in transcripts

            accepted = frames[-1]["plan"]["accepted"]
            devices.append([c["device_id"] for c in accepted])

        assert all("living_room_light" in d for d in devices)


async def test_mic_board_asking_for_no_audio_still_feeds_the_speaker_hub(container) -> None:
    """The board that hears is not the board that speaks.

    The ESP32 holding the microphone sends ``reply_encoding: "none"`` -- its reply
    plays on a Bluetooth speaker wired to another board, which pulls from the audio
    hub. Synthesis must follow the hub, not this socket's encoding.
    """
    import asyncio

    from app.api import ws_protocol as proto
    from app.core.security import Principal
    from app.services.session import VoiceSession

    class _NullSocket:
        async def send_json(self, payload): ...
        async def send_bytes(self, payload): ...

    session = VoiceSession(
        _NullSocket(),
        orchestrator=container.orchestrator,
        recognizer=container.stt,
        settings=container.settings,
        bus=container.bus,
        principal=Principal(kind="device", id="esp32_master"),
        audio_hub=container.audio_hub,
    )
    await session._on_hello(proto.HelloMessage(type="hello", reply_encoding="none"))

    # Nobody is listening yet: synthesising would burn TTS on silence.
    assert session._speak is False

    subscriber = container.audio_hub.subscribe(target_rate=16000, target_channels=1)
    pulled = asyncio.create_task(subscriber.__anext__())
    await asyncio.sleep(0.05)
    try:
        assert session._speak is True, "a connected speaker must bring synthesis back"

        await session.audio_chunk(b"\x01\x02" * 160)
        assert await asyncio.wait_for(pulled, timeout=1.0), "speaker hub got no audio"
    finally:
        await subscriber.aclose()


def _run_one_turn(test_client: TestClient) -> None:
    with test_client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "hello", "room": "living_room", "reply_encoding": "pcm16"})
        ws.receive_json()
        ws.send_bytes("bật đèn phòng khách".encode())
        ws.send_json({"type": "audio.end"})
        _collect(ws, "assistant.final")


def test_the_voice_path_logs_audio_in_transcript_out_and_speech_out(client, caplog):
    """One `fly logs` tail should answer: did audio arrive, what was heard, what was said."""
    import logging

    with caplog.at_level(logging.INFO):
        _run_one_turn(client)

    logged = {r.message: r for r in caplog.records}
    assert "stt audio received" in logged, "no record of the audio that arrived"
    assert "stt transcript" in logged, "no record of what was recognised"
    assert "turn complete" in logged, "no record of what was spoken back"

    heard = logged["stt audio received"]
    assert heard.bytes > 0
    assert heard.peak_level > 0, "a silent utterance must be visible as peak_level 0"

    assert logged["stt transcript"].text == "bật đèn phòng khách"
    assert logged["turn complete"].tts_audio_bytes > 0


def test_transcripts_can_be_kept_out_of_the_log_stream(settings, caplog):
    """LOG_TRANSCRIPTS=false must drop the words and keep the diagnostics."""
    import logging

    from app.main import create_app

    quiet = settings.model_copy(update={"log_transcripts": False})
    # create_app() runs setup_logging(), which clears every root handler --
    # caplog's included. Put it back rather than leaving the test silently blind.
    app = create_app(quiet)
    logging.getLogger().addHandler(caplog.handler)

    with TestClient(app) as muted, caplog.at_level(logging.INFO):
        _run_one_turn(muted)

    record = next(r for r in caplog.records if r.message == "stt transcript")
    assert not hasattr(record, "text"), "transcript text leaked despite LOG_TRANSCRIPTS=false"
    assert record.chars > 0, "the length is still useful and must survive"


def test_a_rejected_voice_socket_says_so_in_the_log(settings, caplog):
    """A device turned away at the handshake must not vanish silently.

    The close happens before accept(), so there is no access-log line either --
    this warning is the only evidence the firmware ever reached the server.
    """
    import logging

    from pydantic import SecretStr

    from app.main import create_app

    guarded = settings.model_copy(update={"api_key": SecretStr("right-key")})
    app = create_app(guarded)
    logging.getLogger().addHandler(caplog.handler)

    with TestClient(app) as guarded_client, caplog.at_level(logging.WARNING):
        try:
            with guarded_client.websocket_connect("/ws/voice?token=wrong-key") as ws:
                ws.receive_json()
        except Exception:  # noqa: BLE001 - the refusal type varies by Starlette version
            pass

    rejected = [r for r in caplog.records if r.message == "voice socket rejected"]
    assert rejected, "the rejection left no trace in the log"
    assert rejected[0].credential_offered == "query"
    assert "wrong-key" not in caplog.text, "the credential must never be logged"


def test_a_silent_reply_announces_itself(client, caplog):
    """A reply nobody can hear must be loud in the log.

    The mic board asks for no audio and the Bluetooth speaker pulls from the hub.
    When that speaker drops off -- it runs out of heap once A2DP pairs -- the turn
    still succeeds and still sets the lights, it just makes no sound. Without this
    warning that is indistinguishable from TTS being broken.
    """
    import logging

    with caplog.at_level(logging.WARNING), client.websocket_connect("/ws/voice") as ws:
        ws.send_json({"type": "hello", "reply_encoding": "none"})
        ws.receive_json()
        ws.send_bytes("bật đèn phòng khách".encode())
        ws.send_json({"type": "audio.end"})
        _collect(ws, "assistant.final")

    skipped = [r for r in caplog.records if r.message == "tts skipped -- nobody is listening"]
    assert skipped, "a silent reply went unreported"
    assert skipped[0].hub_subscribers == 0
    assert skipped[0].client_wants_audio is False
