"""The wake word and the gate that stands in front of every utterance.

These are the specification for "the apartment only listens when it is called".
The detector cases are all real transcripts of the same two words: Vietnamese STT
spells "Anh Thạc Sĩ" a different way roughly every other utterance, and a gate
that only accepts one spelling is a gate that ignores its owner.
"""

from __future__ import annotations

import math
import struct

import pytest

from app.config import Settings
from app.services.speaker import SpeakerManager, enrollment_phrases
from app.services.voice_gate import GateOutcome, VoiceGate
from app.services.wakeword import WakeWordDetector, build_aliases

NAME = "Anh Thạc Sĩ"


@pytest.fixture
def detector() -> WakeWordDetector:
    return WakeWordDetector(name=NAME)


# --------------------------------------------------------------- the detector
@pytest.mark.parametrize(
    "said",
    [
        "Anh Thạc Sĩ, bật đèn phòng khách",
        "anh thạc sĩ bật đèn phòng khách",
        "Anh Thạc Sỹ, bật đèn phòng khách",  # "sĩ" / "sỹ": both spellings exist
        "Anh Thạch Sĩ ơi, bật đèn phòng khách",  # STT hears a trailing "ch"
        "anh thác sĩ, bật đèn phòng khách",  # wrong tone on the first syllable
        "ANH THẠC SĨ: bật đèn phòng khách",
        "Thạc Sĩ ơi, bật đèn phòng khách",  # the honorific is optional
    ],
)
def test_every_way_the_recogniser_spells_the_name_still_wakes_it(detector, said: str):
    match = detector.detect(said)
    assert match.matched, f"{said!r} did not wake the assistant (score {match.score})"
    assert match.command == "bật đèn phòng khách", "the name must not reach the model"


@pytest.mark.parametrize(
    "said",
    [
        "bật đèn phòng khách",
        "tắt hết đèn đi",
        "hôm nay trời nóng quá nhỉ",
        "mai mình phải nộp luận văn thạc sĩ rồi",
    ],
)
def test_an_ordinary_sentence_does_not_wake_it(detector, said: str):
    # The last one is the interesting case: it contains the degree, not the
    # name. Two tokens of context either side are what keep them apart.
    assert detector.detect(said).matched is False


# --------------------------------------------------- nghe nham, nhung van goi
@pytest.mark.parametrize(
    "said",
    [
        # Nguyên văn một lần gọi thật trên điện thoại: bộ nhận dạng gộp "Thạc Sĩ"
        # thành một tiếng "taxi". Ở dạng chữ hai chuỗi chỉ giống 0.64, nên trước
        # khi có lớp so theo âm thì căn hộ im lặng và người dùng không hiểu vì sao.
        "Anh taxi ơi, mở cửa",
        "anh tắc xi ơi, mở cửa",
        "Anh tác si ơi, mở cửa",
        "Anh Tắc Sỹ, mở cửa",
    ],
)
def test_the_recogniser_mangling_the_name_still_wakes_it(detector, said: str):
    match = detector.detect(said)
    assert match.matched, f"{said!r} bị bỏ qua (điểm {match.score})"
    assert match.command == "mở cửa"


@pytest.mark.parametrize(
    "said",
    [
        # Những câu này từng đánh thức nhầm khi lớp so theo âm còn nới tay. Mỗi
        # câu là một kiểu nới khác nhau, nên giữ cả bốn.
        "anh thấy sao",                 # th/t + vần gần giống
        "anh tài xế đang ở đâu rồi",    # cùng bộ phụ âm t...x
        "anh ta xin nghỉ rồi",          # "ta xin" nghe như "thạc sĩ"
        "anh ấy là thạc sĩ kinh tế",    # cửa sổ lệch pha "la thac si"
        "em đang học cao học thạc sĩ",  # "thạc sĩ" là tấm bằng, lại ở cuối câu
        "gọi taxi giúp anh với",
        "xe taxi đỗ ngoài cổng",
    ],
)
def test_everyday_speech_does_not_wake_it(detector, said: str):
    """Lớp so theo âm phải mở đủ rộng cho lời gọi, không rộng hơn.

    Đây là ranh giới hẹp: nới thêm một chút là căn hộ tự bật đèn giữa lúc hai
    người đang nói chuyện với nhau.
    """
    assert detector.detect(said).matched is False


def test_the_phonetic_key_folds_sounds_vietnamese_does_not_distinguish():
    from app.services.wakeword import phonetic_key

    # Cùng một âm, hai cách viết: đây chính là thứ bắc cầu "taxi" sang "thạc sĩ".
    assert phonetic_key("thạc sĩ") == phonetic_key("tắc xi")
    assert phonetic_key("anh") == phonetic_key("ăn")
    # Nhưng vần khác nhau thì vẫn phải khác nhau, nếu không mọi câu đều giống nhau.
    assert phonetic_key("thạc sĩ") != phonetic_key("thấy sao")


def test_a_window_must_line_up_on_the_first_syllable(detector):
    """Khớp phải thẳng hàng từ tiếng đầu, không trượt vào giữa câu.

    "la thac si" giống "anh thac si" tới 0.857 chỉ vì 8 trên 11 ký tự cuối trùng
    nhau -- nhưng người gọi thì bắt đầu bằng tiếng đầu của cái tên.
    """
    assert detector.detect("anh ấy là thạc sĩ kinh tế").matched is False
    assert detector.detect("anh thạc sĩ bật đèn").matched is True


def test_the_name_can_come_at_the_end(detector):
    match = detector.detect("bật đèn phòng ngủ giúp mình nhé, Anh Thạc Sĩ")
    assert match.matched
    assert match.command == "bật đèn phòng ngủ giúp mình nhé"


def test_vocatives_clinging_to_the_name_are_stripped(detector):
    assert detector.detect("Anh Thạc Sĩ ơi, bật đèn").command == "bật đèn"
    assert detector.detect("Anh Thạc Sĩ à, bật đèn").command == "bật đèn"
    assert detector.detect("Anh Thạc Sĩ này, bật đèn").command == "bật đèn"


def test_the_name_on_its_own_is_a_summons_not_a_command(detector):
    match = detector.detect("Anh Thạc Sĩ ơi?")
    assert match.matched
    assert match.command == ""
    assert match.summon_only


def test_a_disabled_detector_passes_everything_through():
    off = WakeWordDetector(name=NAME, enabled=False)
    match = off.detect("bật đèn phòng khách")
    assert match.matched
    assert match.command == "bật đèn phòng khách"


def test_the_name_is_configurable():
    detector = WakeWordDetector(name="Chị Trợ Lý")
    assert detector.detect("Chị Trợ Lý, mở rèm").command == "mở rèm"
    assert detector.detect("Anh Thạc Sĩ, mở rèm").matched is False


def test_extra_aliases_are_accepted():
    detector = WakeWordDetector(name=NAME, aliases=["ê bot", "trợ lý nhà"])
    assert detector.detect("trợ lý nhà, tắt đèn").command == "tắt đèn"


def test_a_single_short_word_is_not_a_usable_wake_word():
    """Guard rail: one token at the default threshold fires on ordinary speech."""
    assert build_aliases("ừ") == ()


def test_the_honorific_is_only_dropped_when_two_words_remain():
    # "anh thạc sĩ" -> also "thạc sĩ"; "anh Nam" must not degrade to "Nam".
    assert "thac si" in build_aliases(NAME)
    assert "nam" not in build_aliases("Anh Nam")


# ------------------------------------------------------------------- the gate
class _Clock:
    """A clock a test can move, so the follow-up window is testable in one ms."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _gate(settings: Settings, speakers: SpeakerManager | None = None, clock=None) -> VoiceGate:
    return VoiceGate(
        detector=WakeWordDetector(
            name=settings.assistant_name,
            aliases=settings.wake_word_aliases,
            threshold=settings.wake_word_threshold,
            enabled=settings.wake_word_enabled,
        ),
        speakers=speakers or SpeakerManager(enabled=False),
        settings=settings,
        clock=clock or (lambda: 0.0),
    )


def test_an_unaddressed_utterance_is_ignored_in_silence(settings: Settings):
    result = _gate(settings).evaluate(session_id="s1", text="bật đèn phòng khách")
    assert result.outcome is GateOutcome.IGNORE
    assert result.code == "wake_word_required"
    # Nothing is said back: answering every overheard sentence with "you did not
    # say my name" is worse than the problem the wake word solves.
    assert result.speech == ""


def test_the_name_lets_the_command_through_without_it(settings: Settings):
    result = _gate(settings).evaluate(session_id="s1", text="Anh Thạc Sĩ, bật đèn phòng khách")
    assert result.outcome is GateOutcome.ALLOW
    assert result.text == "bật đèn phòng khách"


def test_a_follow_up_inside_the_window_needs_no_name(settings: Settings):
    clock = _Clock()
    gate = _gate(settings.model_copy(update={"wake_follow_up_s": 30.0}), clock=clock)

    assert gate.evaluate(session_id="s1", text="Anh Thạc Sĩ, bật đèn").allowed
    clock.now += 5.0
    assert gate.evaluate(session_id="s1", text="tắt đèn đi").allowed, "the conversation closed"

    # ...and a different session never inherits it.
    assert gate.evaluate(session_id="s2", text="tắt đèn đi").outcome is GateOutcome.IGNORE


def test_the_window_closes_and_the_name_is_required_again(settings: Settings):
    clock = _Clock()
    gate = _gate(settings.model_copy(update={"wake_follow_up_s": 30.0}), clock=clock)

    assert gate.evaluate(session_id="s1", text="Anh Thạc Sĩ, bật đèn").allowed
    clock.now += 31.0
    assert gate.evaluate(session_id="s1", text="tắt đèn đi").outcome is GateOutcome.IGNORE


def test_a_zero_window_makes_every_turn_say_the_name(settings: Settings):
    gate = _gate(settings.model_copy(update={"wake_follow_up_s": 0.0}))
    assert gate.evaluate(session_id="s1", text="Anh Thạc Sĩ, bật đèn").allowed
    assert gate.evaluate(session_id="s1", text="tắt đèn").outcome is GateOutcome.IGNORE


def test_answering_a_pending_confirmation_never_needs_the_name(settings: Settings):
    """The assistant asked a question a second ago; "vâng" is an answer to it.

    Making the user re-summon in order to confirm is how a door ends up left
    unlocked -- the confirmation flow is a safety invariant, not a convenience.
    """
    gate = _gate(settings.model_copy(update={"wake_follow_up_s": 0.0}))
    result = gate.evaluate(session_id="s1", text="vâng", has_pending=True)
    assert result.outcome is GateOutcome.ALLOW
    assert result.text == "vâng"


def test_the_name_alone_is_answered_and_opens_the_conversation(settings: Settings):
    gate = _gate(settings)
    summon = gate.evaluate(session_id="s1", text="Anh Thạc Sĩ ơi")
    assert summon.outcome is GateOutcome.SUMMON
    assert settings.assistant_name in summon.speech
    assert gate.is_open("s1"), "a summon must leave the conversation open"


def test_the_gate_is_transparent_when_the_wake_word_is_off(settings: Settings):
    gate = _gate(settings.model_copy(update={"wake_word_enabled": False}))
    result = gate.evaluate(session_id="s1", text="bật đèn phòng khách")
    assert result.outcome is GateOutcome.ALLOW
    assert result.text == "bật đèn phòng khách"


# ---------------------------------------------------- gate + voice biometrics
def _voice(f0: float, duration_s: float = 1.0) -> bytes:
    """A synthetic voice: a pitch and its harmonics, enveloped."""
    rate = 16000
    total = int(duration_s * rate)
    out = []
    for i in range(total):
        t = i / rate
        value = sum(
            amp * math.sin(2.0 * math.pi * f0 * h * t)
            for h, amp in enumerate([1.0, 0.8, 0.6, 0.4], 1)
        )
        env = 0.5 * (1.0 - math.cos(2.0 * math.pi * i / (total - 1)))
        out.append(int(max(-1.0, min(1.0, value * env * 0.4)) * 32767.0))
    return struct.pack(f"<{len(out)}h", *out)


def test_an_unenrolled_voice_is_refused_out_loud(settings: Settings, tmp_path):
    speakers = SpeakerManager(profiles_path=tmp_path / "s.json", enabled=True, threshold=0.75)
    speakers.enroll(speaker_id="owner", name="Chủ nhà", pcm_bytes=_voice(130.0))

    gate = _gate(settings, speakers)
    result = gate.evaluate(
        session_id="s1", text="Anh Thạc Sĩ, mở khoá cửa", audio=_voice(320.0)
    )
    assert result.outcome is GateOutcome.DENY_SPEAKER
    assert result.code == "unauthorized_speaker"
    # Spoken, not silent: the person turned away is standing in the room.
    assert "không nhận diện được giọng nói" in result.speech


def test_an_enrolled_voice_passes_the_same_gate(settings: Settings, tmp_path):
    speakers = SpeakerManager(profiles_path=tmp_path / "s.json", enabled=True, threshold=0.75)
    speakers.enroll(speaker_id="owner", name="Chủ nhà", pcm_bytes=_voice(130.0))

    result = _gate(settings, speakers).evaluate(
        session_id="s1", text="Anh Thạc Sĩ, bật đèn", audio=_voice(131.0)
    )
    assert result.outcome is GateOutcome.ALLOW
    assert result.verification is not None and result.verification.speaker_name == "Chủ nhà"


def test_the_wake_word_is_checked_before_the_fingerprint(settings: Settings, tmp_path):
    """Order matters: the cheap text check must not be gated behind an FFT."""
    speakers = SpeakerManager(profiles_path=tmp_path / "s.json", enabled=True, threshold=0.75)
    speakers.enroll(speaker_id="owner", name="Chủ nhà", pcm_bytes=_voice(130.0))

    result = _gate(settings, speakers).evaluate(
        session_id="s1", text="bật đèn", audio=_voice(320.0)
    )
    assert result.outcome is GateOutcome.IGNORE
    assert result.verification is None, "an unaddressed utterance was fingerprinted anyway"


def test_an_apartment_with_nobody_enrolled_can_be_locked_down(settings: Settings, tmp_path):
    locked = settings.model_copy(update={"voice_enrollment_required": True})
    speakers = SpeakerManager(profiles_path=tmp_path / "s.json", enabled=True)

    result = _gate(locked, speakers).evaluate(session_id="s1", text="Anh Thạc Sĩ, bật đèn")
    assert result.outcome is GateOutcome.DENY_ENROLLMENT
    assert "đăng ký giọng nói" in result.speech


def test_without_that_setting_an_empty_apartment_still_answers(settings: Settings, tmp_path):
    """The default: no profiles means no biometrics, so the demo runs unconfigured."""
    speakers = SpeakerManager(profiles_path=tmp_path / "s.json", enabled=True)
    assert _gate(settings, speakers).evaluate(session_id="s1", text="Anh Thạc Sĩ, bật đèn").allowed


def test_a_typed_turn_has_no_audio_and_is_not_refused_for_it(settings: Settings, tmp_path):
    speakers = SpeakerManager(profiles_path=tmp_path / "s.json", enabled=True, threshold=0.75)
    speakers.enroll(speaker_id="owner", name="Chủ nhà", pcm_bytes=_voice(130.0))

    result = _gate(settings, speakers).evaluate(
        session_id="s1", text="Anh Thạc Sĩ, bật đèn", audio=b""
    )
    assert result.outcome is GateOutcome.ALLOW


# ------------------------------------------------------------ enrollment text
def test_the_phrases_to_read_carry_the_assistant_name():
    phrases = enrollment_phrases("Anh Thạc Sĩ", 3)
    assert len(phrases) == 3
    assert all("Anh Thạc Sĩ" in p for p in phrases)
    assert len(set(phrases)) == 3, "three identical sentences teach the extractor nothing"


def test_a_renamed_assistant_renames_the_phrases():
    assert all("Chị Trợ Lý" in p for p in enrollment_phrases("Chị Trợ Lý", 2))
