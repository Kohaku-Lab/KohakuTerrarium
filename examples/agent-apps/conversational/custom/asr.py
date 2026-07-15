"""Define reusable speech-to-text input modules and result types.

Provides abstract interface for speech-to-text with support for:
- Local Whisper (whisper.cpp, faster-whisper)
- Whisper API (OpenAI)
- NVIDIA Nemotron/Riva

Usage:
    asr = WhisperASR(model="base", device="cuda")
    await asr.start()
    async for event in asr.listen():
        print(f"User said: {event.content}")

    await asr.stop()
"""

import asyncio
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator

from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.modules.input.base import InputModule
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class ASRState(Enum):
    """ASR module state."""

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    ERROR = "error"


@dataclass
class ASRConfig:
    """Configure audio capture, VAD, and transcription behavior.

    Attributes:
        language: Target language code (e.g., "en", "ja", "auto")
        sample_rate: Audio sample rate in Hz
        vad_enabled: Enable voice activity detection
        vad_threshold: VAD sensitivity (0.0-1.0)
        min_speech_duration: Minimum speech duration in seconds
        max_speech_duration: Maximum speech duration before forced processing
        silence_duration: Silence duration to end utterance
    """

    language: str = "auto"
    sample_rate: int = 16000
    vad_enabled: bool = True
    vad_threshold: float = 0.5
    min_speech_duration: float = 0.25
    max_speech_duration: float = 30.0
    silence_duration: float = 0.8
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class ASRResult:
    """Represent one final or interim transcription result.

    Attributes:
        text: Transcribed text
        language: Detected language
        confidence: Confidence score (0.0-1.0)
        is_final: Whether this is final or interim result
        duration: Audio duration in seconds
        metadata: Additional result data
    """

    text: str
    language: str = "unknown"
    confidence: float = 1.0
    is_final: bool = True
    duration: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_event(self) -> TriggerEvent:
        """Convert the transcription into a user-input event."""
        return TriggerEvent(
            type=EventType.USER_INPUT,
            content=self.text,
            context={
                "source": "asr",
                "language": self.language,
                "confidence": self.confidence,
                "duration": self.duration,
                **self.metadata,
            },
        )


class ASRModule(InputModule, ABC):
    """Manage ASR lifecycle and expose transcriptions as input events.

    Subclasses must implement:
    - _start_listening(): Begin audio capture
    - _stop_listening(): Stop audio capture
    - _transcribe(): Get next transcription

    The base class handles:
    - State management
    - Event creation
    - Error handling
    """

    def __init__(self, config: ASRConfig | None = None):
        """Initialize the module with explicit or default ASR settings."""
        self.config = config or ASRConfig()
        self._state = ASRState.IDLE
        self._running = False

    @property
    def state(self) -> ASRState:
        """Return the current ASR lifecycle state."""
        return self._state

    @property
    def is_listening(self) -> bool:
        """Return whether the module is actively listening."""
        return self._state == ASRState.LISTENING

    async def start(self) -> None:
        """Start the ASR module."""
        if self._running:
            logger.warning("ASR already running")
            return

        self._running = True
        self._state = ASRState.LISTENING
        await self._start_listening()
        logger.info("ASR started", language=self.config.language)

    async def stop(self) -> None:
        """Stop the ASR module."""
        if not self._running:
            return

        self._running = False
        self._state = ASRState.IDLE
        await self._stop_listening()
        logger.info("ASR stopped")

    async def get_input(self) -> TriggerEvent | None:
        """Return the next non-empty transcription as an input event."""
        if not self._running:
            await self.start()

        try:
            result = await self._transcribe()
            if result and result.text.strip():
                logger.debug(
                    "ASR transcription",
                    text=result.text[:50],
                    confidence=result.confidence,
                )
                return result.to_event()
        except Exception as e:
            logger.error("ASR transcription error", error=str(e))
            self._state = ASRState.ERROR
            await asyncio.sleep(0.5)
            self._state = ASRState.LISTENING

        return None

    async def listen(self) -> AsyncIterator[TriggerEvent]:
        """Yield transcription events while the module is running."""
        if not self._running:
            await self.start()

        while self._running:
            event = await self.get_input()
            if event:
                yield event

    @abstractmethod
    async def _start_listening(self) -> None:
        """Start the subclass-specific audio capture source."""
        ...

    @abstractmethod
    async def _stop_listening(self) -> None:
        """Stop the subclass-specific audio capture source."""
        ...

    @abstractmethod
    async def _transcribe(self) -> ASRResult | None:
        """Wait for speech and return its transcription, if any."""
        ...


class DummyASR(ASRModule):
    """Provide deterministic or stdin-backed transcriptions for testing."""

    def __init__(
        self,
        config: ASRConfig | None = None,
        responses: list[str] | None = None,
        use_stdin: bool = False,
    ):
        super().__init__(config)
        self.responses = responses or []
        self.use_stdin = use_stdin
        self._response_idx = 0

    async def _start_listening(self) -> None:
        logger.debug("DummyASR started")

    async def _stop_listening(self) -> None:
        logger.debug("DummyASR stopped")

    async def _transcribe(self) -> ASRResult | None:
        if self.use_stdin:
            # stdin is blocking, so keep it off the event-loop thread.
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(None, sys.stdin.readline)
            text = text.strip()
            if text:
                return ASRResult(text=text, language="en")
            return None

        if self._response_idx < len(self.responses):
            text = self.responses[self._response_idx]
            self._response_idx += 1
            await asyncio.sleep(0.1)  # Preserve asynchronous timing in tests.
            return ASRResult(text=text, language="en")

        # Avoid a busy loop after all deterministic responses are consumed.
        await asyncio.sleep(1.0)
        return None
