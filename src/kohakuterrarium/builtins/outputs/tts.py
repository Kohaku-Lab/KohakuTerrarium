"""Text-to-speech output abstractions with buffering and interruption."""

import asyncio
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class TTSState(Enum):
    """TTS module state."""

    IDLE = "idle"
    SPEAKING = "speaking"
    BUFFERING = "buffering"
    ERROR = "error"


@dataclass
class TTSConfig:
    """Configure voice, audio, and streaming behavior for a TTS backend."""

    voice_id: str = "default"
    language: str = "en"
    speed: float = 1.0
    pitch: float = 0.0
    volume: float = 1.0
    sample_rate: int = 24000
    streaming: bool = True
    buffer_size: int = 50  # Delay synthesis until enough text or punctuation arrives.
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class AudioChunk:
    """Represent one synthesized audio chunk and its source text."""

    data: bytes
    sample_rate: int = 24000
    channels: int = 1
    is_final: bool = False
    text: str = ""


class TTSModule(OutputModule, ABC):
    """Provide TTS state, buffering, playback, and interruption orchestration."""

    def __init__(self, config: TTSConfig | None = None):
        """Initialize TTS state from optional backend configuration."""
        self.config = config or TTSConfig()
        self._state = TTSState.IDLE
        self._running = False
        self._text_buffer = ""
        self._interrupted = False

    @property
    def state(self) -> TTSState:
        """Return the current synthesis or playback state."""
        return self._state

    @property
    def is_speaking(self) -> bool:
        """Return whether audio playback is active."""
        return self._state == TTSState.SPEAKING

    async def start(self) -> None:
        """Start the TTS module."""
        if self._running:
            return

        self._running = True
        self._state = TTSState.IDLE
        await self._initialize()
        logger.info("TTS started", voice=self.config.voice_id)

    async def stop(self) -> None:
        """Stop the TTS module."""
        if not self._running:
            return

        await self.interrupt()
        self._running = False
        self._state = TTSState.IDLE
        await self._cleanup()
        logger.info("TTS stopped")

    async def speak(self, text: str) -> None:
        """Synthesize and play one complete text segment."""
        if not text.strip():
            return

        self._interrupted = False
        self._state = TTSState.SPEAKING

        try:
            async for chunk in self._synthesize(text):
                if self._interrupted:
                    break
                await self._play_audio(chunk)

            if not self._interrupted:
                logger.debug("TTS completed", text_length=len(text))
        except Exception as e:
            logger.error("TTS speak error", error=str(e))
            self._state = TTSState.ERROR
        finally:
            if not self._interrupted:
                self._state = TTSState.IDLE

    async def stream(self, text_chunk: str) -> None:
        """Buffer text and synthesize at size or sentence boundaries."""
        self._text_buffer += text_chunk
        self._state = TTSState.BUFFERING

        should_synthesize = len(
            self._text_buffer
        ) >= self.config.buffer_size or self._ends_with_sentence(self._text_buffer)

        if should_synthesize and self._text_buffer.strip():
            text = self._text_buffer
            self._text_buffer = ""
            await self.speak(text)

    async def flush(self) -> None:
        """Synthesize any text remaining in the streaming buffer."""
        if self._text_buffer.strip():
            text = self._text_buffer
            self._text_buffer = ""
            await self.speak(text)

    async def interrupt(self) -> None:
        """Stop active playback immediately and clear buffered text."""
        if self._state != TTSState.SPEAKING:
            return

        self._interrupted = True
        await self._stop_playback()
        self._text_buffer = ""
        self._state = TTSState.IDLE
        logger.debug("TTS interrupted")

    def _ends_with_sentence(self, text: str) -> bool:
        """Return whether text ends at a supported sentence boundary."""
        text = text.rstrip()
        if not text:
            return False
        return text[-1] in ".!?。！？"

    async def write(self, text: str) -> None:
        """Write text to TTS (implements OutputModule)."""
        await self.stream(text)

    async def write_stream(self, chunk: str) -> None:
        """Write streaming chunk to TTS (implements OutputModule)."""
        await self.stream(chunk)

    async def _initialize(self) -> None:
        """Initialize TTS backend. Override if needed."""

    async def _cleanup(self) -> None:
        """Cleanup TTS backend. Override if needed."""

    @abstractmethod
    async def _synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        """Yield audio chunks synthesized from text."""
        ...

    @abstractmethod
    async def _play_audio(self, chunk: AudioChunk) -> None:
        """Play one synthesized audio chunk."""
        ...

    @abstractmethod
    async def _stop_playback(self) -> None:
        """Stop current audio playback immediately."""
        ...


class DummyTTS(TTSModule):
    """Record synthesized text without producing audio."""

    def __init__(self, config: TTSConfig | None = None):
        super().__init__(config)
        self.spoken_texts: list[str] = []

    async def _synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        """Record text and yield one empty final audio chunk."""
        delay = len(text) * 0.01  # Approximate synthesis latency for tests.
        await asyncio.sleep(min(delay, 0.5))

        self.spoken_texts.append(text)
        logger.info("DummyTTS speaking", text=text[:50])

        yield AudioChunk(
            data=b"",
            is_final=True,
            text=text,
        )

    async def _play_audio(self, chunk: AudioChunk) -> None:
        """No-op for dummy."""

    async def _stop_playback(self) -> None:
        """No-op for dummy."""


class ConsoleTTS(TTSModule):
    """Simulate speech by printing characters with a configurable delay."""

    def __init__(
        self,
        config: TTSConfig | None = None,
        char_delay: float = 0.02,
    ):
        super().__init__(config)
        self.char_delay = char_delay
        self._current_text = ""

    async def _synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        """Yield chunks for each character."""
        self._current_text = text

        for char in text:
            if self._interrupted:
                break
            yield AudioChunk(
                data=char.encode(),
                is_final=False,
                text=char,
            )

        yield AudioChunk(data=b"", is_final=True, text="")

    async def _play_audio(self, chunk: AudioChunk) -> None:
        """Print character with delay."""
        if chunk.text:
            sys.stdout.write(chunk.text)
            sys.stdout.flush()
            await asyncio.sleep(self.char_delay)

        if chunk.is_final:
            sys.stdout.write("\n")
            sys.stdout.flush()

    async def _stop_playback(self) -> None:
        """Print newline on interrupt."""
        sys.stdout.write(" [interrupted]\n")
        sys.stdout.flush()
