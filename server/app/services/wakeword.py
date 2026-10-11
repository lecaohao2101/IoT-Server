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
            width = len(alias.split())
            # Minimum accepted ratio rises for a one-word name: a single short
            # token matches far too much ordinary speech at 0.82.
            needed = self.threshold if width >= 2 else max(self.threshold, 0.92)
            span = min(width, len(flat))
            for start in range(0, len(flat) - span + 1):
                if start > _MAX_LEAD_TOKENS and start + span != len(flat):
                    continue  # mid-sentence: somebody is being talked about, not to
                window = " ".join(flat[start : start + span]).strip()
                if not window:
                    continue
                score = SequenceMatcher(None, window, alias).ratio()
                if score > best_score:
                    best_score = score
                    if score >= needed:
                        best = (alias, start, start + span)

        if best is None:
            return WakeMatch(
                matched=False,
                command="",
                raw=raw,
                score=round(best_score, 3),
                near_miss=best_score >= self.threshold - 0.10,
            )

        alias, start, end = best
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
