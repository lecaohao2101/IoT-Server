"""Composition root.

Every dependency is constructed once, here, and handed to whoever needs it. The
application has no global singletons beyond ``Settings``; routes reach their
collaborators through ``request.app.state.container``, which makes the whole graph
replaceable in a test with three lines.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.ai.base import LanguageModel, SpeechRecognizer, SpeechSynthesizer
from app.ai.registry import (
    apply_google_credentials,
    build_language_model,
    build_recognizer,
    build_synthesizer,
)
from app.config import Settings
from app.core.eventbus import EventBus
from app.domain.home import HomeConfig, load_home_config
from app.mqtt.bridge import DeviceBridge
from app.mqtt.client import MqttTransport, create_transport
from app.safety.rules import SafetyPolicy, load_safety_policy
from app.safety.validator import CommandValidator
from app.services.conversation import ConversationManager
from app.services.device_state import DeviceStateManager
from app.services.orchestrator import Orchestrator
from app.storage.backend import KeyValueStore, create_store

log = logging.getLogger(__name__)


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    home: HomeConfig
    policy: SafetyPolicy
    store: KeyValueStore
    bus: EventBus
    state: DeviceStateManager
    conversations: ConversationManager
    validator: CommandValidator
    transport: MqttTransport
    bridge: DeviceBridge
    stt: SpeechRecognizer
    tts: SpeechSynthesizer
    llm: LanguageModel
    orchestrator: Orchestrator
    started_at: float

    # ------------------------------------------------------------- lifecycle
    @classmethod
    async def create(cls, settings: Settings) -> AppContainer:
        home = load_home_config(
            settings.resolve(settings.home_config_path), base_topic=settings.mqtt_base_topic
        )
        policy = load_safety_policy(settings.resolve(settings.safety_config_path))
        log.info(
            "apartment loaded",
            extra={
                "home": home.name,
                "rooms": len(home.rooms),
                "devices": len(home.devices),
                "scenes": len(home.scenes),
            },
        )

        if not settings.auth_enabled and settings.app_env != "dev":
            log.warning(
                "AUTHENTICATION IS DISABLED -- anyone who can reach this server can "
                "control the apartment. Set API_KEY before exposing it to a network.",
                extra={"env": settings.app_env},
            )

        store = await create_store(settings)
        bus = EventBus()
        state = DeviceStateManager(store, home, bus)
        await state.seed_defaults()

        conversations = ConversationManager(
            store, ttl_s=settings.conversation_ttl_s, max_turns=settings.conversation_max_turns
        )
        validator = CommandValidator(home, policy)

        transport = create_transport(settings)
        bridge = DeviceBridge(transport, home, state, bus, qos=settings.mqtt_qos)
        await bridge.start()

        # Must precede every provider: the Google SDKs read credentials from
        # the process environment, not from Settings.
        apply_google_credentials(settings)
        stt = build_recognizer(settings)
        tts = build_synthesizer(settings)
        llm = build_language_model(settings, home)
        log.info(
            "ai providers ready",
            extra={"stt": stt.name, "tts": tts.name, "llm": llm.name},
        )

        orchestrator = Orchestrator(
            home=home,
            policy=policy,
            llm=llm,
            tts=tts,
            validator=validator,
            state=state,
            conversations=conversations,
            bridge=bridge,
            bus=bus,
            history_turns=settings.conversation_max_turns,
            tts_chunk_bytes=settings.tts_chunk_bytes,
            sample_rate=settings.audio_sample_rate,
        )

        return cls(
            settings=settings,
            home=home,
            policy=policy,
            store=store,
            bus=bus,
            state=state,
            conversations=conversations,
            validator=validator,
            transport=transport,
            bridge=bridge,
            stt=stt,
            tts=tts,
            llm=llm,
            orchestrator=orchestrator,
            started_at=time.monotonic(),
        )

    async def aclose(self) -> None:
        for name, closer in (
            ("bridge", self.bridge.stop()),
            ("stt", self.stt.aclose()),
            ("tts", self.tts.aclose()),
            ("llm", self.llm.aclose()),
            ("store", self.store.close()),
        ):
            try:
                await closer
            except Exception:  # noqa: BLE001 - shutdown is best-effort
                log.warning("error while closing %s", name, exc_info=True)

    # ---------------------------------------------------------------- probes
    @property
    def uptime_s(self) -> float:
        return time.monotonic() - self.started_at

    async def health(self) -> dict[str, Any]:
        store_ok = await self.store.ping()
        transport_stats = self.transport.stats()
        ready = store_ok and (transport_stats.get("connected") or not self.settings.mqtt_enabled)
        return {
            "status": "ok" if ready else "degraded",
            "uptime_s": round(self.uptime_s, 1),
            "env": self.settings.app_env,
            "store": {"kind": self.store.kind, "reachable": store_ok},
            "transport": transport_stats,
            "providers": {"stt": self.stt.name, "tts": self.tts.name, "llm": self.llm.name},
            "home": {
                "name": self.home.name,
                "devices": len(self.home.devices),
                "rooms": len(self.home.rooms),
            },
            "subscribers": self.bus.subscriber_count,
        }
