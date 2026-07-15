"""Provide console-backed TTS examples with streaming and immediate output."""

import asyncio
import sys
from typing import AsyncIterator

from kohakuterrarium.builtins.outputs.tts import AudioChunk, TTSConfig, TTSModule


class StreamingTTS(TTSModule):
    """Simulate streaming speech by printing text with paced typing."""

    def __init__(
        self,
        config: TTSConfig | None = None,
        char_delay: float = 0.02,
        word_delay: float = 0.05,
    ):
        super().__init__(config)
        self.char_delay = char_delay
        self.word_delay = word_delay

    async def _initialize(self) -> None:
        """Announce that the console playback sink is ready."""
        print("\n[StreamingTTS] Ready for output")
        print("=" * 50)

    async def _cleanup(self) -> None:
        """Announce that console playback has stopped."""
        print("\n[StreamingTTS] Stopped")

    async def _synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        """Yield word-sized chunks so playback can preserve natural pacing."""
        words = text.split()

        for i, word in enumerate(words):
            if self._interrupted:
                break

            # Emit spaces separately so playback can distinguish word delays.
            if i > 0:
                yield AudioChunk(data=b" ", text=" ")

            yield AudioChunk(data=word.encode(), text=word)

        # The terminal chunk lets the playback sink close the utterance.
        yield AudioChunk(data=b"", is_final=True, text="")

    async def _play_audio(self, chunk: AudioChunk) -> None:
        """Render one synthesized chunk with interruptible typing delays."""
        if chunk.is_final:
            # A final chunk terminates the current console line.
            sys.stdout.write("\n")
            sys.stdout.flush()
            return

        text = chunk.text
        if not text:
            return

        # Per-character waits make interruption visible during long words.
        for char in text:
            if self._interrupted:
                break
            sys.stdout.write(char)
            sys.stdout.flush()
            await asyncio.sleep(self.char_delay)

        # Whitespace chunks must not introduce an extra inter-word delay.
        if text and not text.isspace():
            await asyncio.sleep(self.word_delay)

    async def _stop_playback(self) -> None:
        """Mark interrupted playback without leaving a partial line open."""
        sys.stdout.write(" [...]\n")
        sys.stdout.flush()


class SimpleTTS(TTSModule):
    """Render synthesized text immediately without streaming delays."""

    async def _synthesize(self, text: str) -> AsyncIterator[AudioChunk]:
        yield AudioChunk(data=text.encode(), text=text, is_final=True)

    async def _play_audio(self, chunk: AudioChunk) -> None:
        if chunk.text:
            print(f"[AI]: {chunk.text}")

    async def _stop_playback(self) -> None:
        print("[interrupted]")
