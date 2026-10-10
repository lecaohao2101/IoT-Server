"""Audio streaming and speaker endpoints for external audio devices and web clients.

Provides:
- GET /api/audio/stream: Persistent HTTP PCM stream (default: 44.1kHz stereo) for ESP32 Bluetooth speaker
- WS  /ws/speaker: Dedicated WebSocket audio stream for smart speakers
- POST /api/audio/play: Synthesize and broadcast custom text to all speakers
- GET  /voice-demo: Interactive browser-based two-way voice assistant (STT + LLM + TTS)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.ai.base import AudioEncoding
from app.api.deps import Container, WsContainer

log = logging.getLogger(__name__)

router = APIRouter(tags=["audio"])


class AudioPlayRequest(BaseModel):
    text: str | None = Field(default=None, description="Text to synthesize with TTS and play")
    url: str | None = Field(default=None, description="Audio/YouTube URL (compatibility)")


@router.get(
    "/api/audio/stream",
    summary="Continuous PCM audio stream for external speakers",
    response_class=StreamingResponse,
)
async def audio_stream(
    container: Container,
    rate: int = Query(default=44100, ge=8000, le=48000, description="Output sample rate (Hz)"),
    channels: int = Query(default=2, ge=1, le=2, description="Channels: 1 = mono, 2 = stereo"),
) -> StreamingResponse:
    """Stream PCM audio to an external speaker gateway (like ESP32 Bluetooth A2DP)."""

    async def _generator():
        # Yield a tiny silent primer so the client HTTP connection registers immediately
        primer_bytes = bytes(channels * 2 * 256)
        yield primer_bytes

        subscriber = container.audio_hub.subscribe(target_rate=rate, target_channels=channels)
        try:
            async for chunk in subscriber:
                if chunk:
                    yield chunk
        except asyncio.CancelledError:
            pass

    headers = {
        "Content-Type": "application/octet-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Audio-Sample-Rate": str(rate),
        "X-Audio-Channels": str(channels),
        "X-Audio-Format": "pcm_s16le",
    }
    return StreamingResponse(_generator(), media_type="application/octet-stream", headers=headers)


@router.websocket("/ws/speaker")
async def speaker_websocket(
    websocket: WebSocket,
    container: WsContainer,
    rate: int = Query(default=44100, ge=8000, le=48000),
    channels: int = Query(default=2, ge=1, le=2),
) -> None:
    """Dedicated WebSocket audio stream for external smart speakers."""
    await websocket.accept()
    log.info("Speaker WebSocket connected", extra={"rate": rate, "channels": channels})
    subscriber = container.audio_hub.subscribe(target_rate=rate, target_channels=channels)

    async def _send_audio():
        async for chunk in subscriber:
            if chunk:
                await websocket.send_bytes(chunk)

    sender_task = asyncio.create_task(_send_audio())
    try:
        # Keep connection open and drain incoming control/ping frames
        while True:
            _ = await websocket.receive()
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        sender_task.cancel()
        log.info("Speaker WebSocket disconnected")


@router.post(
    "/api/audio/play",
    summary="Play TTS text or URL through speakers",
    status_code=status.HTTP_200_OK,
)
async def play_audio(request: AudioPlayRequest, container: Container) -> dict[str, Any]:
    """Broadcast TTS speech or custom audio to all connected speakers."""
    text = (request.text or "").strip()
    if text:
        audio = await container.tts.synthesize(text, encoding=AudioEncoding.PCM16)
        if not audio.is_empty:
            asyncio.create_task(
                container.audio_hub.broadcast_utterance(
                    audio.audio, src_rate=audio.sample_rate, src_channels=1
                )
            )
        return {"success": True, "message": f"Broadcasting speech: '{text}'"}

    if request.url:
        return {"success": True, "message": f"Received URL: {request.url}"}

    return {"success": False, "message": "Nothing to play. Provide 'text' or 'url'."}


@router.get(
    "/voice-demo",
    summary="Interactive two-way voice intercom demo page",
    response_class=HTMLResponse,
)
async def voice_demo_page() -> HTMLResponse:
    """Standalone web test page for real-time two-way voice with STT and TTS."""
    html_content = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Smart Apartment — Voice Intercom (STT & TTS)</title>
    <style>
        :root {
            --bg: #0f172a; --card: #1e293b; --accent: #38bdf8; --accent-hover: #0284c7;
            --text: #f8fafc; --muted: #94a3b8; --active: #ef4444; --green: #22c55e;
        }
        body {
            font-family: system-ui, -apple-system, sans-serif;
            background: var(--bg); color: var(--text);
            margin: 0; padding: 20px; display: flex; flex-direction: column; align-items: center;
            min-height: 100vh;
        }
        .container {
            max-width: 600px; width: 100%; background: var(--card);
            border-radius: 16px; padding: 28px; box-shadow: 0 10px 25px rgba(0,0,0,0.4);
            border: 1px solid #334155; text-align: center;
        }
        h1 { margin-top: 0; font-size: 24px; color: var(--accent); }
        p.subtitle { color: var(--muted); font-size: 14px; margin-bottom: 24px; }
        .mic-btn {
            width: 110px; height: 110px; border-radius: 50%;
            background: #334155; border: 4px solid var(--accent);
            cursor: pointer; display: flex; align-items: center; justify-content: center;
            margin: 20px auto; transition: all 0.2s ease; outline: none;
        }
        .mic-btn.recording {
            background: var(--active); border-color: #fca5a5;
            animation: pulse 1.5s infinite;
        }
        .mic-icon { width: 44px; height: 44px; fill: white; }
        @keyframes pulse {
            0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7); }
            70% { transform: scale(1.06); box-shadow: 0 0 0 20px rgba(239, 68, 68, 0); }
            100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
        }
        .status-badge {
            display: inline-block; padding: 6px 14px; border-radius: 20px;
            font-size: 13px; font-weight: 600; margin-bottom: 20px;
            background: #334155; color: var(--muted);
        }
        .status-badge.connected { background: rgba(34, 197, 94, 0.2); color: var(--green); border: 1px solid var(--green); }
        .status-badge.talking { background: rgba(239, 68, 68, 0.2); color: var(--active); border: 1px solid var(--active); }
        .log-box {
            background: #0f172a; border-radius: 10px; padding: 16px;
            text-align: left; font-size: 14px; min-height: 180px; max-height: 280px;
            overflow-y: auto; border: 1px solid #1e293b;
        }
        .chat-bubble { margin-bottom: 12px; line-height: 1.5; }
        .user-msg { color: var(--accent); }
        .ai-msg { color: #a7f3d0; font-weight: 500; }
        .system-msg { color: var(--muted); font-size: 12px; }
        .footer { margin-top: 20px; font-size: 13px; color: var(--muted); }
    </style>
</head>
<body>
    <div class="container">
        <h1>🎙️ Smart Apartment Voice Intercom</h1>
        <p class="subtitle">Trò chuyện 2 chiều thời gian thực (Google STT + Gemini + Google TTS)</p>
        
        <div id="statusBadge" class="status-badge">Đang khởi tạo...</div>
        
        <div style="margin-bottom: 16px; display: flex; justify-content: center; gap: 8px;">
            <input type="password" id="tokenInput" placeholder="Nhập API Key / Token..." style="background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 6px 12px; color: #fff; width: 240px; font-size: 13px;">
            <button id="saveTokenBtn" style="background: var(--accent); border: none; border-radius: 6px; padding: 6px 14px; font-weight: bold; cursor: pointer; color: #0f172a;">Lưu & Kết nối</button>
        </div>
        
        <div>
            <button id="micBtn" class="mic-btn" title="Nhấn để nói chuyện">
                <svg class="mic-icon" viewBox="0 0 24 24">
                    <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/>
                    <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
                </svg>
            </button>
        </div>
        
        <p id="actionHint" style="font-size: 14px; color: var(--muted);">Nhấn mic để bắt đầu nói, thả ra khi nói xong</p>

        <div class="log-box" id="chatBox">
            <div class="system-msg">[Hệ thống] Sẵn sàng trò chuyện qua WebSocket /ws/voice. Hãy nhấn micro và ra lệnh (Ví dụ: "Bật đèn phòng khách", "Mở điều hòa 26 độ", "Chào bạn").</div>
        </div>

        <div class="footer">
            Loa ngoài ESP32: stream tại <code>/api/audio/stream</code> | WebSocket: <code>/ws/voice</code>
        </div>
    </div>

    <script>
        const micBtn = document.getElementById('micBtn');
        const statusBadge = document.getElementById('statusBadge');
        const chatBox = document.getElementById('chatBox');
        const actionHint = document.getElementById('actionHint');
        const tokenInput = document.getElementById('tokenInput');
        const saveTokenBtn = document.getElementById('saveTokenBtn');

        const urlParams = new URLSearchParams(window.location.search);
        let currentToken = urlParams.get('token') || localStorage.getItem('voiceToken') || localStorage.getItem('logKey') || '';
        if (currentToken) tokenInput.value = currentToken;

        saveTokenBtn.onclick = () => {
            currentToken = tokenInput.value.trim();
            localStorage.setItem('voiceToken', currentToken);
            localStorage.setItem('logKey', currentToken);
            initWebSocket();
        };

        let ws = null;
        let audioContext = null;
        let micStream = null;
        let processor = null;
        let isRecording = false;
        let playContext = null;
        let nextPlayTime = 0;

        function addLog(text, className) {
            const div = document.createElement('div');
            div.className = 'chat-bubble ' + className;
            div.innerHTML = text;
            chatBox.appendChild(div);
            chatBox.scrollTop = chatBox.scrollHeight;
        }

        function initWebSocket() {
            if (ws) {
                ws.onclose = null;
                ws.close();
            }
            const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const query = currentToken ? `?token=${encodeURIComponent(currentToken)}` : '';
            const wsUrl = `${proto}//${window.location.host}/ws/voice${query}`;
            statusBadge.textContent = 'Đang kết nối WebSocket...';
            statusBadge.className = 'status-badge';

            ws = new WebSocket(wsUrl);
            ws.binaryType = 'arraybuffer';

            ws.onopen = () => {
                statusBadge.textContent = '● Đã kết nối Voice Server';
                statusBadge.className = 'status-badge connected';
                ws.send(JSON.stringify({
                    type: 'hello',
                    sample_rate: 16000,
                    channels: 1,
                    reply_encoding: 'pcm16'
                }));
            };

            ws.onmessage = async (event) => {
                if (typeof event.data === 'string') {
                    const msg = JSON.parse(event.data);
                    if (msg.type === 'stt.final') {
                        addLog(`👤 <b>Bạn:</b> ${msg.text}`, 'user-msg');
                    } else if (msg.type === 'assistant.delta') {
                        // Delta response
                    } else if (msg.type === 'assistant.final') {
                        addLog(`🤖 <b>AI:</b> ${msg.text}`, 'ai-msg');
                    }
                } else if (event.data instanceof ArrayBuffer) {
                    playAudioChunk(event.data);
                }
            };

            ws.onclose = (event) => {
                if (event.code === 1008) {
                    statusBadge.textContent = 'Lỗi xác thực: Sai hoặc thiếu API Key';
                    statusBadge.className = 'status-badge';
                } else {
                    statusBadge.textContent = 'Mất kết nối. Đang thử lại...';
                    statusBadge.className = 'status-badge';
                    setTimeout(initWebSocket, 3000);
                }
            };
        }

        function playAudioChunk(arrayBuffer) {
            if (!playContext) {
                playContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
            }
            if (playContext.state === 'suspended') {
                playContext.resume();
            }

            const pcm16 = new Int16Array(arrayBuffer);
            const float32 = new Float32Array(pcm16.length);
            for (let i = 0; i < pcm16.length; i++) {
                float32[i] = pcm16[i] / 32768.0;
            }

            const buffer = playContext.createBuffer(1, float32.length, 16000);
            buffer.copyToChannel(float32, 0);

            const source = playContext.createBufferSource();
            source.buffer = buffer;
            source.connect(playContext.destination);

            const now = playContext.currentTime;
            if (nextPlayTime < now) nextPlayTime = now;
            source.start(nextPlayTime);
            nextPlayTime += buffer.duration;
        }

        async function startRecording() {
            if (!ws || ws.readyState !== WebSocket.OPEN) {
                alert('Chưa kết nối được tới server.');
                return;
            }

            try {
                micStream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: 16000, channelCount: 1 } });
                audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
                const source = audioContext.createMediaStreamSource(micStream);
                
                processor = audioContext.createScriptProcessor(2048, 1, 1);
                processor.onaudioprocess = (e) => {
                    if (!isRecording) return;
                    const inputData = e.inputBuffer.getChannelData(0);
                    const pcm16 = new Int16Array(inputData.length);
                    for (let i = 0; i < inputData.length; i++) {
                        let s = Math.max(-1, Math.min(1, inputData[i]));
                        pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                    }
                    if (ws.readyState === WebSocket.OPEN) {
                        ws.send(pcm16.buffer);
                    }
                };

                source.connect(processor);
                processor.connect(audioContext.destination);

                isRecording = true;
                micBtn.classList.add('recording');
                statusBadge.textContent = '● Đang lắng nghe...';
                statusBadge.className = 'status-badge talking';
                actionHint.textContent = 'Đang thu âm giọng nói của bạn... Nhấn nút lần nữa để gửi';

                ws.send(JSON.stringify({ type: 'audio.start' }));
            } catch (err) {
                console.error(err);
                alert('Không thể truy cập Microphone: ' + err.message);
            }
        }

        function stopRecording() {
            if (!isRecording) return;
            isRecording = false;
            micBtn.classList.remove('recording');
            statusBadge.textContent = 'Đang xử lý STT & AI...';
            statusBadge.className = 'status-badge';
            actionHint.textContent = 'Nhấn mic để bắt đầu nói, thả ra khi nói xong';

            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'audio.end' }));
            }

            if (processor) processor.disconnect();
            if (audioContext) audioContext.close();
            if (micStream) micStream.getTracks().forEach(t => t.stop());
        }

        micBtn.addEventListener('click', () => {
            if (!isRecording) {
                startRecording();
            } else {
                stopRecording();
            }
        });

        window.onload = initWebSocket;
    </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)
