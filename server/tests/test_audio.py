from __future__ import annotations

import asyncio

import pytest

from app.services.audio_hub import AudioBroadcastHub


@pytest.mark.asyncio
async def test_audio_broadcast_hub():
    hub = AudioBroadcastHub()
    assert hub.active_subscribers_count == 0

    subscriber = hub.subscribe(target_rate=44100, target_channels=2)
    next_task = asyncio.create_task(subscriber.__anext__())
    # Yield control so subscribe generator enters and registers
    await asyncio.sleep(0.01)
    assert hub.active_subscribers_count == 1

    # Broadcast 16kHz mono chunk
    pcm_16k = bytes(320)  # 10ms of 16kHz mono PCM16
    await hub.broadcast_chunk(pcm_16k, src_rate=16000, src_channels=1)

    # Next item should be resampled to 44.1kHz stereo
    received = await next_task
    assert len(received) > 0

    # Clean up
    await subscriber.aclose()
    assert hub.active_subscribers_count == 0


def test_audio_endpoints(client):
    # 1. Voice demo HTML page
    res = client.get("/voice-demo")
    assert res.status_code == 200
    assert "Voice Intercom" in res.text

    # 2. Audio play endpoint with text
    res = client.post("/api/audio/play", json={"text": "Xin chào smart home"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True

    # 3. Audio play endpoint with empty body
    res = client.post("/api/audio/play", json={})
    assert res.status_code == 200
    assert res.json()["success"] is False
