"""The assistant only answers to its own name.

The microphone board listens continuously -- there is no button -- so every
sentence spoken anywhere near it reaches the recogniser. Without this gate each
one of them becomes a command, and a conversation between two people in the
living room turns the lights on and off.

Matching runs on accent-stripped text compared with a ratio, not with ``==``.
Vietnamese STT spells the same two words differently every few utterances:
"Anh Thạc Sĩ", "anh thạc sỹ", "anh thạch sĩ" and "anh thác sĩ" are one name to a
person and four strings to the recogniser. An exact match accepted roughly half
of the real summons; a sliding-window ratio accepts all four and still refuses
ordinary sentences, which share no window with the name at all.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.config import DEFAULT_ASSISTANT_NAME
from app.core.utils import clamp, strip_accents

#: Dropped from the front of an alias to build a shorter one, so "thạc sĩ, bật
#: đèn" works as well as the full "anh thạc sĩ, bật đèn".
_HONORIFICS = frozenset({"anh", "chi", "ong", "ba", "co", "chu", "bac", "em", "thay", "cau"})

#: Vocatives and particles that cling to the name. "Anh Thạc Sĩ ơi, bật đèn"
#: must leave "bật đèn" behind, not "ơi, bật đèn".
_FILLERS = frozenset({"oi", "a", "ah", "e", "nay", "ne", "nhe", "nha", "di", "dc"})

_TOKEN_RE = re.compile(r"\S+")
_PUNCT = " \t\r\n,.!?;:…\"'()-–—"
_WS_RE = re.compile(r"\s+")

#: How far into the utterance the name may start. Two tokens of slack covers
#: "ê, anh thạc sĩ ơi" and "làm ơn anh thạc sĩ" without opening the middle of
#: the sentence, where "luận văn thạc sĩ" would otherwise wake the apartment in
#: the middle of a conversation about somebody's thesis. The only other accepted
#: position is the very end -- "bật đèn giúp mình nhé, Anh Thạc Sĩ" -- which is
#: the vocative, and which a trailing word like "rồi" rules out.
_MAX_LEAD_TOKENS = 2

#: Phép so theo âm phải vượt ngưỡng thường cộng thêm chừng này.
#:
#: Nó gộp nhiều cách viết về một chuỗi nên hai câu khác hẳn nhau vẫn có thể gần
#: nhau. Đo trên tập câu đời thường: ở cùng ngưỡng 0.82, "anh thấy sao" (0.833),
#: "anh tài xế đang ở đâu rồi" (0.833) và "anh ta xin nghỉ rồi" (0.857) đều đánh
#: thức nhầm; cộng 0.06 thì cả ba bị chặn mà "anh taxi" (0.923) vẫn lọt.
_PHONETIC_MARGIN = 0.06

#: Tiếng ĐẦU của cửa sổ phải giống tiếng đầu của tên ít nhất chừng này.
#:
#: Không có neo này thì một cửa sổ lệch pha vẫn ăn điểm cao nhờ phần đuôi: "anh
#: ấy là thạc sĩ kinh tế" cho cửa sổ "la thac si", giống "anh thac si" tới 0.857
#: vì 8 trên 11 ký tự cuối trùng nhau. Nhưng người gọi thì bắt đầu bằng tiếng đầu
#: của cái tên, nên một lần khớp thật luôn thẳng hàng ngay từ tiếng đó.
_ANCHOR_MIN = 0.55


@dataclass(frozen=True, slots=True)
class WakeMatch:
    """What the detector heard.

    ``command`` is the utterance with the name removed -- that, and never the raw
    transcript, is what reaches the model: "Anh Thạc Sĩ ơi, bật đèn" should be
    reasoned about as "bật đèn".
    """

    matched: bool
    command: str
    raw: str = ""
    alias: str = ""
    score: float = 0.0
    index: int = -1
    #: Close enough to be worth a log line when it did not match. A string of
    #: these in the log is what tells an operator the threshold is too high.
    near_miss: bool = False

    @property
    def summon_only(self) -> bool:
        """The name with nothing after it -- "Anh Thạc Sĩ ơi?"."""
        return self.matched and not self.command


def _clean(token: str) -> str:
    return strip_accents(token).strip(_PUNCT)


#: Những cặp phụ âm mà tiếng Việt nói ra gần như không phân biệt, và bộ nhận dạng
#: đổi qua đổi lại giữa chúng trong cùng một buổi. Áp cho CẢ tên lẫn câu nghe
#: được, nên nó chỉ gộp các cách viết của cùng một âm chứ không nới lỏng gì thêm.
#:
#: Thứ tự quan trọng: tổ hợp ba chữ trước tổ hợp hai chữ, hai chữ trước một chữ.
#: Đảo thứ tự thì "ngh" bị "ng" ăn mất phần đầu.
_PHONETIC_RULES: tuple[tuple[str, str], ...] = (
    ("ngh", "n"),
    ("nh", "n"),
    ("ng", "n"),
    ("ch", "c"),
    ("tr", "c"),   # ch/tr: phần lớn người nói miền Nam phát âm như nhau
    ("th", "t"),
    ("ph", "f"),
    ("kh", "k"),
    ("gh", "g"),
    ("gi", "z"),
    ("qu", "k"),
    ("d", "z"),    # d/gi/r đều là một âm với giọng Bắc
    ("r", "z"),
    ("x", "s"),    # s/x không phân biệt với phần lớn người nói
    ("k", "c"),
    ("q", "c"),
    ("y", "i"),
)


def phonetic_key(text: str) -> str:
    """Rút câu về bộ âm, để cách viết khác nhau của cùng một âm trùng nhau.

    Đây là thứ cho phép nhận ra "Anh taxi" là "Anh Thạc Sĩ". Bộ nhận dạng trả về
    đúng âm nó nghe thấy, chỉ là chọn cách viết khác và gộp hai tiếng thành một:
    ``anh thac si`` -> ``an tac si`` và ``anh taxi`` -> ``an tasi``, hai chuỗi
    gần như trùng nhau, trong khi ở dạng chữ gốc chúng chỉ giống 0.64.

    Dấu cách bị bỏ: chỗ ngắt từ của bộ nhận dạng là thứ kém tin cậy nhất trong
    cả câu, nên không để nó tham gia vào phép so.
    """
    key = strip_accents(text)
    for src, dst in _PHONETIC_RULES:
        key = key.replace(src, dst)
    # Gộp chữ lặp: "ss" và "s" là một âm, và việc gộp ở trên hay tạo ra chữ đôi.
    out: list[str] = []
    for ch in key:
        if ch.isspace():
            continue
        if out and out[-1] == ch:
            continue
        out.append(ch)
    return "".join(out)


def build_aliases(name: str, extra: Iterable[str] = ()) -> tuple[str, ...]:
    """Accent-stripped forms the name may arrive as, longest first.

    Longest first matters: with "anh thac si" tried before "thac si", a full
    summon consumes the honorific too instead of leaving "anh" at the head of
    the command.
    """
    candidates: list[str] = [name, *extra]
    aliases: list[str] = []
    for candidate in candidates:
        flat = _WS_RE.sub(" ", strip_accents(candidate or "").strip(_PUNCT)).strip()
        if len(flat) < 3:
            continue
        aliases.append(flat)
        parts = flat.split()
        # "anh thạc sĩ" is also called "thạc sĩ"; one bare token is too short to
        # be a safe wake word, so the honorific is only dropped when two remain.
        if len(parts) >= 3 and parts[0] in _HONORIFICS:
            aliases.append(" ".join(parts[1:]))
    deduped = list(dict.fromkeys(aliases))
    return tuple(sorted(deduped, key=lambda a: (-len(a.split()), -len(a))))


class WakeWordDetector:
    """Decides whether an utterance was addressed to the apartment."""

    def __init__(
        self,
        *,
        name: str = DEFAULT_ASSISTANT_NAME,
        aliases: Iterable[str] = (),
        threshold: float = 0.82,
        enabled: bool = True,
    ) -> None:
        self.name = (name or "").strip() or DEFAULT_ASSISTANT_NAME
        self.enabled = enabled
        self.threshold = clamp(threshold, 0.5, 1.0)
        self._aliases = build_aliases(self.name, aliases)
        # Những alias sinh ra bằng cách bỏ kính ngữ ("anh thạc sĩ" -> "thạc sĩ").
        # Phần còn lại thường là một từ có nghĩa trong tiếng Việt, nên chúng bị
        # giới hạn chặt hơn: chỉ nhận ở đầu câu, không nhận ở vị trí cuối câu.
        full = {_WS_RE.sub(" ", strip_accents(n or "").strip(_PUNCT)).strip()
                for n in (self.name, *aliases)}
        self._short_aliases = frozenset(a for a in self._aliases if a not in full)

    @property
    def aliases(self) -> tuple[str, ...]:
        return self._aliases

    def detect(self, text: str) -> WakeMatch:
        raw = (text or "").strip()
        if not raw:
            return WakeMatch(matched=False, command="", raw=raw)
        if not self.enabled or not self._aliases:
            # Gate switched off: everything heard is meant for us, unchanged.
            return WakeMatch(matched=True, command=raw, raw=raw, alias="", score=1.0, index=0)

        spans = [m.span() for m in _TOKEN_RE.finditer(raw)]
        flat = [_clean(raw[s:e]) for s, e in spans]
        if not any(flat):
            return WakeMatch(matched=False, command="", raw=raw)

        best_score = 0.0
        best: tuple[str, int, int] | None = None
        for alias in self._aliases:
            tokens = len(alias.split())
            alias_phon = phonetic_key(alias)
            alias_first = alias.split()[0]
            # Alias rút gọn ("thạc sĩ") là một từ có thật trong tiếng Việt. Nó chỉ
            # được coi là lời gọi khi đứng ĐẦU câu: "thạc sĩ, bật đèn" là gọi,
            # còn "...em học cao học thạc sĩ" là một tấm bằng.
            allow_tail = alias not in self._short_aliases

            # Thử cả độ rộng hẹp hơn một từ. Bộ nhận dạng gộp tiếng rất tuỳ hứng:
            # "Thạc Sĩ" (2 tiếng) ra thành "taxi" (1 tiếng) là nguyên văn một lần
            # gọi thật. Cố định đúng số từ của tên là tự loại mình khỏi những lần
            # như vậy.
            for width in sorted({tokens - 1, tokens}):
                if width < 1 or width > len(flat):
                    continue
                # Cửa sổ một từ phải giống hơn hẳn: một tiếng ngắn trùng với quá
                # nhiều lời nói thường ở mức 0.82.
                needed = self.threshold if width >= 2 else max(self.threshold, 0.92)
                # Phép so theo âm gộp nhiều cách viết về một chuỗi, tức là một
                # không gian lỏng hơn hẳn. Đo thật trên câu đời thường: ở cùng
                # ngưỡng với phép so chữ, "anh thấy sao" và "anh tài xế đang ở
                # đâu rồi" đều lọt. Phần chênh này là chỗ chặn chúng lại.
                needed_phon = min(1.0, needed + _PHONETIC_MARGIN)

                for start in range(0, len(flat) - width + 1):
                    at_tail = (start + width == len(flat)) and allow_tail
                    if start > _MAX_LEAD_TOKENS and not at_tail:
                        continue  # giữa câu: đang nói *về* ai đó, không phải nói *với*
                    parts = flat[start : start + width]
                    window = " ".join(parts).strip()
                    if not window:
                        continue

                    # Neo ở tiếng đầu: chặn cửa sổ lệch pha ăn điểm nhờ phần
                    # đuôi. Cửa sổ một từ không có gì để neo, và nó đã phải vượt
                    # một ngưỡng cao hơn hẳn rồi.
                    if width >= 2 and not self._anchored(parts[0], alias_first):
                        continue

                    plain = SequenceMatcher(None, window, alias).ratio()
                    # So theo âm: th/t, s/x, ch/tr... nghe như nhau trong tiếng
                    # Việt và bộ nhận dạng đổi qua đổi lại giữa chúng.
                    phon = SequenceMatcher(None, phonetic_key(window), alias_phon).ratio()

                    score = max(plain, phon)
                    if score > best_score:
                        best_score = score
                    if not (plain >= needed or phon >= needed_phon):
                        continue
                    # Giữ lần khớp TỐT NHẤT, không phải lần đầu tiên, và khi điểm
                    # ngang nhau thì lấy cửa sổ rộng hơn. "Anh Thạc Sĩ ơi" khớp
                    # được cả ở 2 từ lẫn 3 từ; lấy bản 2 từ là bỏ chữ "Sĩ" lại
                    # trong câu lệnh gửi cho mô hình.
                    if best is None or (score, width) > (best[0], best[1]):
                        best = (score, width, alias, start, start + width)

        if best is None:
            return WakeMatch(
                matched=False,
                command="",
                raw=raw,
                score=round(best_score, 3),
                near_miss=best_score >= self.threshold - 0.10,
            )

        _, _, alias, start, end = best
        command = self._strip_name(raw, spans, start, end)
        return WakeMatch(
            matched=True,
            command=command,
            raw=raw,
            alias=alias,
            score=round(best_score, 3),
            index=start,
        )

    @staticmethod
    def _anchored(token: str, alias_first: str) -> bool:
        """Tiếng đầu của cửa sổ có thẳng hàng với tiếng đầu của tên không."""
        if not token:
            return False
        if SequenceMatcher(None, token, alias_first).ratio() >= _ANCHOR_MIN:
            return True
        return (
            SequenceMatcher(None, phonetic_key(token), phonetic_key(alias_first)).ratio()
            >= _ANCHOR_MIN
        )

    @staticmethod
    def _strip_name(raw: str, spans: list[tuple[int, int]], start: int, end: int) -> str:
        """Remove the matched tokens and whatever clings to them.

        The name is not always at the front: "bật đèn giúp mình, Anh Thạc Sĩ" is
        the same request, so both sides of the match are kept and rejoined.
        """
        head = raw[: spans[start][0]]
        tail = raw[spans[end - 1][1] :] if end <= len(spans) else ""
        rest = f"{head.strip(_PUNCT)} {tail.strip(_PUNCT)}".strip()

        tokens = rest.split()
        while tokens and _clean(tokens[0]) in _FILLERS:
            tokens.pop(0)
        return _WS_RE.sub(" ", " ".join(tokens)).strip(_PUNCT).strip()


__all__ = ["DEFAULT_ASSISTANT_NAME", "WakeMatch", "WakeWordDetector", "build_aliases"]
