#!/usr/bin/env python
"""Stand-in for the ESP32 (or the mobile app) on /ws/voice.

Three modes:

    # speak a sentence -- with STT_PROVIDER=mock the text is the transcript
    python tools/simulate_client.py --say "bật đèn phòng khách"

    # stream a real recording (any mono WAV; it is resampled to 16 kHz)
    python tools/simulate_client.py --wav recording.wav

    # send a control frame without pretending to be a microphone
    python tools/simulate_client.py --text "mở rèm phòng ngủ"

Received speech is written to ``reply.wav`` so you can listen to what the
apartment said back.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets  # noqa: E402

from app.core.audio import (  # noqa: E402
    AudioFormat,
    iter_frames,
    pcm_to_wav,
    resample,
    to_mono,
    wav_to_pcm,
)

FRAME_MS = 20


def build_url(args: argparse.Namespace) -> str:
    query = []
    if args.token:
        query.append(f"token={args.token}")
    if args.device_id:
        query.append(f"device_id={args.device_id}")
    suffix = ("?" + "&".join(query)) if query else ""
    return f"{args.url.rstrip('/')}/ws/voice{suffix}"


async def send_audio(ws, pcm: bytes, rate: int, realtime: bool) -> None:
    fmt = AudioFormat(sample_rate=rate)
    frame_bytes = fmt.bytes_for_ms(FRAME_MS)
    for frame in iter_frames(pcm, frame_bytes):
        await ws.send(frame)
        if realtime:
            await asyncio.sleep(FRAME_MS / 1000.0)
    await ws.send(json.dumps({"type": "audio.end"}))


async def receive_loop(ws, out_path: Path, rate: int) -> None:
    audio = bytearray()
    partial = ""
    async for message in ws:
        if isinstance(message, bytes):
            audio.extend(message)
            continue

        frame = json.loads(message)
        kind = frame.get("type")
        if kind == "stt.partial":
            partial = frame["text"]
            print(f"\r  …{partial}", end="", flush=True)
        elif kind == "stt.final":
            print(f"\r  bạn: {frame['text']}" + " " * 20)
        elif kind == "assistant.delta":
            print(frame["text"], end="", flush=True)
        elif kind == "tts.start":
            print(f"\n  [audio {frame['encoding']} @ {frame['sample_rate']} Hz]")
        elif kind == "assistant.final":
            print(f"\n  trợ lý: {frame['text']}")
            plan = frame.get("plan") or {}
            for command in plan.get("accepted", []):
                print(f"    -> {command['device_id']}.{command['capability']} = {command['value']}")
            for rejection in plan.get("rejected", []):
                print(f"    x  {rejection['device_id']}: {rejection['message']}")
            print(f"    latency: {frame.get('latency_ms')}")
            break
        elif kind in {"error", "notice"}:
            print(f"\n  [{kind}] {frame.get('code')}: {frame.get('message')}")
            if kind == "error":
                break
        elif kind == "session.ready":
            print(f"  phiên: {frame['session_id']}")

    if audio:
        # Blocking write is fine here: a CLI with nothing else to do.
        out_path.write_bytes(pcm_to_wav(bytes(audio), AudioFormat(sample_rate=rate)))  # noqa: ASYNC240
        print(f"  đã lưu âm thanh trả lời: {out_path} ({len(audio) / 2 / rate:.1f}s)")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="ws://localhost:8000")
    parser.add_argument("--token", default=None, help="API key or device token")
    parser.add_argument("--device-id", default=None)
    parser.add_argument("--room", default="living_room")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--reply-encoding", default="pcm16", choices=["pcm16", "mp3", "wav", "none"])
    parser.add_argument("--out", default="reply.wav", type=Path)
    parser.add_argument("--realtime", action="store_true", help="pace frames at 1x speed")

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--say", help="send this text as if it were microphone audio")
    source.add_argument("--wav", type=Path, help="stream a WAV file")
    source.add_argument("--text", help="send a text control frame (skips recognition)")

    args = parser.parse_args()
    rate = 16000

    async with websockets.connect(build_url(args), max_size=4 * 1024 * 1024) as ws:
        hello = {
            "type": "hello",
            "room": args.room,
            "sample_rate": rate,
            "reply_encoding": args.reply_encoding,
        }
        if args.session_id:
            hello["session_id"] = args.session_id
        await ws.send(json.dumps(hello))

        receiver = asyncio.create_task(receive_loop(ws, args.out, rate))

        if args.text:
            await ws.send(json.dumps({"type": "text", "text": args.text}))
        elif args.say:
            # The mock recogniser transcribes UTF-8 payloads verbatim.
            await ws.send(args.say.encode("utf-8"))
            await ws.send(json.dumps({"type": "audio.end"}))
        else:
            payload, fmt = wav_to_pcm(args.wav.read_bytes())
            if fmt.channels > 1:
                payload = to_mono(payload, fmt.channels)
            payload = resample(payload, fmt.sample_rate, rate)
            print(f"  gửi {len(payload) / 2 / rate:.1f}s âm thanh từ {args.wav}")
            await send_audio(ws, payload, rate, args.realtime)

        await asyncio.wait_for(receiver, timeout=60)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
