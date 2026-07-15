"""Custom ASR input helpers for the conversational agent example.

For demo purposes, ``ConsoleASR`` reads from stdin. ``WhisperInput`` wraps the
local Whisper ASR example in this folder for opt-in audio setups.
"""

import asyncio
import sys
from typing import Any

from asr import ASRConfig, ASRModule, ASRResult
from whisper_asr import WhisperASR, create_whisper_config


class WhisperInput(WhisperASR):
    """Config-friendly wrapper around the example Whisper ASR input."""

    def __init__(self, **options: Any):
        super().__init__(create_whisper_config(options))


class ConsoleASR(ASRModule):
    """Simulate speech recognition by reading text from the console."""

    def __init__(self, config: ASRConfig | None = None):
        super().__init__(config)
        self._input_queue: asyncio.Queue[str] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None

    async def _start_listening(self) -> None:
        """Start the background console reader."""
        self._reader_task = asyncio.create_task(self._read_console())

    async def _stop_listening(self) -> None:
        """Cancel and await the background console reader."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

    async def _read_console(self) -> None:
        """Read console lines and enqueue non-empty input."""
        loop = asyncio.get_event_loop()

        print("\n[ConsoleASR] Ready for input (type and press Enter):")
        print("-" * 50)

        while self._running:
            try:
                # stdin is blocking, so read it outside the event-loop thread.
                line = await loop.run_in_executor(None, sys.stdin.readline)
                line = line.strip()

                if line:
                    await self._input_queue.put(line)

            except Exception:
                break

    async def _transcribe(self) -> ASRResult | None:
        """Return the next queued line as a deterministic ASR result."""
        try:
            text = await asyncio.wait_for(
                self._input_queue.get(),
                timeout=0.5,
            )
            return ASRResult(
                text=text,
                language="en",
                confidence=1.0,
                is_final=True,
            )
        except asyncio.TimeoutError:
            return None
