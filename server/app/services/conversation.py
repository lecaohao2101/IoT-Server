"""Conversation manager: bounded, expiring short-term memory per session."""

from __future__ import annotations

import logging
from typing import Any

from app.domain.conversation import ConversationContext, PendingConfirmation, Role, Turn
from app.storage.backend import KeyValueStore

log = logging.getLogger(__name__)

_KEY = "conv:{session_id}"


class ConversationManager:
    def __init__(self, store: KeyValueStore, ttl_s: int = 1800, max_turns: int = 12) -> None:
        self._store = store
        self._ttl = ttl_s
        self._max_turns = max_turns

    async def get(self, session_id: str) -> ConversationContext:
        raw = await self._store.get(_KEY.format(session_id=session_id))
        if not raw:
            return ConversationContext(session_id=session_id)
        try:
            return ConversationContext.model_validate_json(raw)
        except Exception:  # noqa: BLE001 - never let stale memory break a turn
            log.warning("discarding unreadable conversation", extra={"session_id": session_id})
            return ConversationContext(session_id=session_id)

    async def save(self, ctx: ConversationContext) -> None:
        if len(ctx.turns) > self._max_turns:
            ctx.turns = ctx.turns[-self._max_turns :]
        await self._store.set(
            _KEY.format(session_id=ctx.session_id), ctx.model_dump_json(), ttl_s=self._ttl
        )

    async def set_room(self, session_id: str, room: str | None) -> ConversationContext:
        ctx = await self.get(session_id)
        if room and ctx.room != room:
            ctx.room = room
            await self.save(ctx)
        return ctx

    async def append_user(self, session_id: str, text: str) -> ConversationContext:
        ctx = await self.get(session_id)
        ctx.turns.append(Turn(role=Role.USER, text=text))
        await self.save(ctx)
        return ctx

    async def append_assistant(
        self,
        session_id: str,
        text: str,
        *,
        commands: list[dict[str, Any]] | None = None,
        latency_ms: dict[str, float] | None = None,
    ) -> ConversationContext:
        ctx = await self.get(session_id)
        ctx.turns.append(
            Turn(
                role=Role.ASSISTANT,
                text=text,
                commands=commands or [],
                latency_ms=latency_ms or {},
            )
        )
        await self.save(ctx)
        return ctx

    async def set_pending(self, session_id: str, pending: PendingConfirmation | None) -> None:
        ctx = await self.get(session_id)
        ctx.pending = pending
        await self.save(ctx)

    async def clear(self, session_id: str) -> None:
        await self._store.delete(_KEY.format(session_id=session_id))

    async def last_assistant_commands(self, session_id: str) -> list[dict[str, Any]]:
        """Backing data for "hoàn tác" / "undo that"."""
        ctx = await self.get(session_id)
        for turn in reversed(ctx.turns):
            if turn.role is Role.ASSISTANT and turn.commands:
                return turn.commands
        return []
