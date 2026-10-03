#!/usr/bin/env python
"""Kiểm tra từng provider AI bằng một lệnh gọi thật, rồi nói rõ phải sửa gì.

    python tools/check_providers.py            # kiểm tra theo .env
    python tools/check_providers.py --all      # ép thử cả ba dù .env đang để mock

Công cụ này tồn tại vì lỗi xác thực của Google rất khó đọc: cùng một thông báo
"permission denied" có thể là chưa bật API, là key bị giới hạn, hoặc là nhầm kiểu
credentials. Mỗi phép thử dưới đây ánh xạ lỗi sang đúng việc cần làm.

Không bao giờ in ra giá trị bí mật.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.base import AudioEncoding, LlmMessage  # noqa: E402
from app.ai.registry import apply_google_credentials  # noqa: E402
from app.ai.schemas import response_json_schema  # noqa: E402
from app.config import Settings  # noqa: E402

OK, FAIL, SKIP = "  [OK]  ", "  [LỖI] ", "  [BỎ]  "


def diagnose(message: str) -> list[str]:
    """Dịch lỗi của Google sang việc cần làm."""
    m = message.lower()
    if "api_key_service_blocked" in m:
        return [
            "Key bị chặn với API này. Vào Google Cloud Console:",
            "  1. APIs & Services > Library > bật 'Generative Language API'",
            "  2. APIs & Services > Credentials > mở key > API restrictions:",
            "     chọn 'Don't restrict key', hoặc thêm 'Generative Language API'",
            "  Cách nhanh hơn: tạo key mới tại https://aistudio.google.com/apikey",
        ]
    if "api keys are not supported" in m or "credentials_missing" in m:
        return [
            "API này không nhận API key, phải dùng service account:",
            "  1. Console > IAM & Admin > Service Accounts > Create",
            "  2. Cấp quyền gọi Speech-to-Text và Text-to-Speech",
            "  3. Keys > Add key > JSON > lưu vào server/credentials/",
            "  4. .env:  GOOGLE_APPLICATION_CREDENTIALS=credentials/<tên-file>.json",
        ]
    if "default credentials were not found" in m or "could not automatically determine" in m:
        return [
            "Chưa có credentials nào. Đặt GOOGLE_APPLICATION_CREDENTIALS trong .env",
            "trỏ tới file JSON của service account (đường dẫn tương đối tính từ server/).",
        ]
    if "has not been used" in m or "is disabled" in m or "service_disabled" in m:
        return [
            "API chưa được bật trên project này.",
            "  Console > APIs & Services > Library > tìm API > Enable",
        ]
    if "permission" in m and "denied" in m:
        return [
            "Service account thiếu quyền. Cấp thêm role cho phép gọi API này,",
            "hoặc kiểm tra GOOGLE_PROJECT_ID có khớp project của service account không.",
        ]
    if "invalid_argument" in m and "voice" in m:
        return ["Tên giọng đọc không hợp lệ. Kiểm tra TTS_VOICE trong .env."]
    return []


def report(ok: bool, label: str, detail: str, error: str = "") -> bool:
    print(f"{OK if ok else FAIL}{label:<22} {detail}")
    if not ok and error:
        print(f"         {error[:220]}")
        for line in diagnose(error):
            print(f"         -> {line}")
    return ok


async def check_llm(settings: Settings) -> bool:
    from app.ai.llm.gemini import GeminiLanguageModel

    if not settings.gemini_api_key:
        return report(False, "LLM (Gemini)", "chưa đặt GEMINI_API_KEY")
    model = GeminiLanguageModel(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.gemini_model,
        timeout_s=settings.llm_timeout_s,
    )
    try:
        parts = [
            delta
            async for delta in model.stream(
                system='Trả về JSON: {"speech": "...", "commands": []}',
                messages=[LlmMessage(role="user", content="Nói một câu chào thật ngắn.")],
                json_schema=response_json_schema(),
            )
        ]
    except Exception as exc:  # noqa: BLE001
        return report(False, "LLM (Gemini)", settings.gemini_model, str(exc))
    text = "".join(parts).strip()
    return report(True, "LLM (Gemini)", f"{settings.gemini_model} -> {text[:70]}")


async def check_tts(settings: Settings) -> bool:
    from app.ai.tts.google import GoogleSynthesizer

    synth = GoogleSynthesizer(
        voice=settings.tts_voice,
        language=settings.stt_language,
        speaking_rate=settings.tts_speaking_rate,
        pitch=settings.tts_pitch,
    )
    try:
        audio = await synth.synthesize(
            "Xin chào, đây là thử nghiệm giọng nói.",
            encoding=AudioEncoding.PCM16,
            sample_rate=settings.audio_sample_rate,
        )
    except Exception as exc:  # noqa: BLE001
        return report(False, "TTS (Cloud TTS)", settings.tts_voice, str(exc))

    out = Path("tts_check.wav")
    from app.core.audio import AudioFormat, pcm_to_wav

    out.write_bytes(pcm_to_wav(audio.audio, AudioFormat(sample_rate=audio.sample_rate)))
    seconds = len(audio.audio) / 2 / audio.sample_rate
    return report(True, "TTS (Cloud TTS)", f"{settings.tts_voice} -> {out} ({seconds:.1f}s)")


async def check_stt(settings: Settings) -> bool:
    """Nhận dạng chính giọng vừa tổng hợp ở bước TTS -- khép kín, không cần file mẫu."""
    from app.ai.stt.google_v2 import GoogleRecognizer
    from app.core.audio import iter_frames, wav_to_pcm

    sample = Path("tts_check.wav")
    if not sample.is_file():
        return report(False, "STT (Speech v2)", "cần tts_check.wav từ bước TTS ở trên")

    pcm, fmt = wav_to_pcm(sample.read_bytes())
    recognizer = GoogleRecognizer(
        project_id=settings.google_project_id or "",
        location=settings.google_location,
        language=settings.stt_language,
        model=settings.stt_model,
    )
    stream = recognizer.open_stream(sample_rate=fmt.sample_rate)

    async def feed() -> None:
        for frame in iter_frames(pcm, fmt.bytes_for_ms(100)):
            await stream.push(frame)
            await asyncio.sleep(0.01)
        await stream.end_of_audio()

    try:
        pump = asyncio.create_task(feed())
        heard = ""
        async for result in stream.results():
            if result.text:
                heard = result.text
            if result.is_final:
                break
        await pump
    except Exception as exc:  # noqa: BLE001
        return report(False, "STT (Speech v2)", settings.stt_language, str(exc))
    finally:
        await stream.aclose()

    if not heard:
        return report(False, "STT (Speech v2)", "kết nối được nhưng không ra chữ nào")
    return report(True, "STT (Speech v2)", f"{settings.stt_language} -> nghe thấy: {heard!r}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="thử cả ba dù .env đang để mock")
    args = parser.parse_args()

    settings = Settings()
    apply_google_credentials(settings)

    print("Cấu hình hiện tại")
    print(f"  STT={settings.stt_provider}  TTS={settings.tts_provider}  LLM={settings.llm_provider}")
    print(f"  GOOGLE_PROJECT_ID              {settings.google_project_id or 'CHƯA ĐẶT'}")
    cred = settings.google_application_credentials
    print(f"  GOOGLE_APPLICATION_CREDENTIALS {settings.resolve(cred) if cred else 'CHƯA ĐẶT'}")
    key = settings.gemini_api_key
    print(f"  GEMINI_API_KEY                 {'đã đặt' if key else 'CHƯA ĐẶT'}")
    print()

    results = []
    for name, wanted, runner in (
        ("LLM", settings.llm_provider, check_llm),
        ("TTS", settings.tts_provider, check_tts),
        ("STT", settings.stt_provider, check_stt),
    ):
        if wanted != "google" and not args.all:
            print(f"{SKIP}{name:<22} đang để mock (dùng --all để vẫn thử)")
            continue
        results.append(await runner(settings))

    print()
    if not results:
        print("Chưa thử gì. Chạy lại với --all, hoặc đặt *_PROVIDER=google trong .env.")
        return 0
    passed = sum(results)
    print(f"Kết quả: {passed}/{len(results)} provider hoạt động")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
