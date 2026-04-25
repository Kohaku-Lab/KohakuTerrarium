"""
Whisper-based ASR module using openai-whisper + sounddevice + silero-vad.

Provides real-time speech-to-text with:
- Continuous microphone recording via sounddevice
- Voice Activity Detection via Silero VAD
- Transcription via OpenAI Whisper (local)

Requires:
    pip install openai-whisper sounddevice numpy torch

Also requires FFmpeg installed on system:
    Windows: choco install ffmpeg
    Linux: apt install ffmpeg
    macOS: brew install ffmpeg
"""

import asyncio
import threading
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Any

import numpy as np

try:
    import sounddevice as sd

    HAS_SOUNDDEVICE = True
except ImportError:
    sd = None  # type: ignore[assignment]
    HAS_SOUNDDEVICE = False

try:
    import torch

    HAS_TORCH = True
except ImportError:
    torch = None  # type: ignore[assignment]
    HAS_TORCH = False

try:
    import whisper

    HAS_WHISPER = True
except ImportError:
    whisper = None  # type: ignore[assignment]
    HAS_WHISPER = False

from asr import ASRConfig, ASRModule, ASRResult
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class WhisperConfig(ASRConfig):
    """
    Configuration for Whisper ASR.

    Attributes:
        model: Whisper model size ('tiny', 'base', 'small', 'medium', 'large')
        device: 'cuda' or 'cpu'
        language: Language code or 'auto' for auto-detection
        vad_threshold: Silero VAD threshold (0.0-1.0)
        speech_pad_ms: Padding around speech in milliseconds
        min_speech_ms: Minimum speech duration to process
        min_silence_ms: Silence duration to end utterance
        max_speech_s: Maximum speech duration before forced processing
    """

    model: str = "large-v3"
    device: str = "cuda"
    language: str = "auto"
    vad_threshold: float = 0.5
    speech_pad_ms: int = 300
    min_speech_ms: int = 250
    min_silence_ms: int = 500
    max_speech_s: float = 30.0


class WhisperASR(ASRModule):
    """
    Real-time Whisper ASR using openai-whisper + sounddevice + silero-vad.

    Uses continuous microphone recording with VAD-based segmentation.
    Audio is only sent to Whisper when speech is detected.

    Usage:
        asr = WhisperASR(WhisperConfig(model="base", device="cuda"))
        await asr.start()
        async for event in asr.listen():
            print(f"User said: {event.content}")
    """

    def __init__(self, config: WhisperConfig | None = None):
        """Initialize Whisper ASR."""
        super().__init__(config or WhisperConfig())
        self.whisper_config: WhisperConfig = self.config  # type: ignore

        self._whisper_model = None
        self._vad_model = None
        self._vad_utils = None

        self._transcription_queue: Queue[ASRResult] = Queue()
        self._recording_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Audio settings
        self._sample_rate = 16000
        self._chunk_size = 512  # ~32ms at 16kHz

    async def _start_listening(self) -> None:
        """Start continuous audio recording with VAD."""
        # Load models
        await self._load_models()

        logger.info(
            "Starting Whisper ASR",
            model=self.whisper_config.model,
            device=self.whisper_config.device,
        )

        # Start recording thread
        self._stop_event.clear()
        self._recording_thread = threading.Thread(
            target=self._recording_loop,
            daemon=True,
        )
        self._recording_thread.start()

    async def _load_models(self) -> None:
        """Load Whisper and VAD models."""
        if not HAS_TORCH:
            raise ImportError("torch not installed. Install with: pip install torch")

        # Load Whisper model
        if not HAS_WHISPER:
            raise ImportError(
                "openai-whisper not installed. Install with: pip install openai-whisper"
            )

        try:
            device = self.whisper_config.device
            if device == "cuda" and not torch.cuda.is_available():
                logger.warning("CUDA not available, falling back to CPU")
                device = "cpu"

            logger.info("Loading Whisper model...", model=self.whisper_config.model)
            self._whisper_model = whisper.load_model(
                self.whisper_config.model,
                device=device,
            )
            logger.info("Whisper model loaded")

        except Exception as e:
            raise RuntimeError(f"Failed to load Whisper model: {e}") from e

        # Load Silero VAD
        try:
            model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                onnx=False,
            )
            self._vad_model = model
            self._vad_utils = utils
            logger.info("Silero VAD loaded")

        except Exception as e:
            logger.warning(
                "Failed to load Silero VAD, using energy-based VAD", error=str(e)
            )
            self._vad_model = None

    def _recording_loop(self) -> None:
        """Main recording loop (runs in separate thread)."""
        if not HAS_SOUNDDEVICE:
            logger.error(
                "sounddevice not installed. Install with: pip install sounddevice"
            )
            return

        # Audio buffer for accumulating speech
        audio_buffer: list[np.ndarray] = []
        is_speaking = False
        silence_chunks = 0
        speech_chunks = 0

        # VAD parameters
        min_silence_chunks = int(
            self.whisper_config.min_silence_ms
            / (self._chunk_size / self._sample_rate * 1000)
        )
        min_speech_chunks = int(
            self.whisper_config.min_speech_ms
            / (self._chunk_size / self._sample_rate * 1000)
        )
        max_speech_chunks = int(
            self.whisper_config.max_speech_s * self._sample_rate / self._chunk_size
        )

        logger.info("Whisper ASR ready, listening for speech...")

        try:
            with sd.InputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype=np.float32,
                blocksize=self._chunk_size,
            ) as stream:
                while not self._stop_event.is_set():
                    # Read audio chunk
                    audio_chunk, overflowed = stream.read(self._chunk_size)
                    if overflowed:
                        logger.warning("Audio buffer overflow")

                    audio_chunk = audio_chunk.flatten()

                    # Check for speech using VAD
                    has_speech = self._detect_speech(audio_chunk)

                    if has_speech:
                        silence_chunks = 0
                        speech_chunks += 1
                        audio_buffer.append(audio_chunk)

                        if not is_speaking and speech_chunks >= min_speech_chunks:
                            is_speaking = True
                            logger.debug("Speech started")

                    else:
                        if is_speaking:
                            silence_chunks += 1
                            audio_buffer.append(audio_chunk)

                            # Check if speech ended
                            if silence_chunks >= min_silence_chunks:
                                # Process the audio
                                self._process_audio(audio_buffer)
                                audio_buffer = []
                                is_speaking = False
                                speech_chunks = 0
                                silence_chunks = 0
                                logger.debug("Speech ended")

                    # Force process if too long
                    if len(audio_buffer) >= max_speech_chunks:
                        logger.debug("Max speech duration reached, processing...")
                        self._process_audio(audio_buffer)
                        audio_buffer = []
                        is_speaking = False
                        speech_chunks = 0
                        silence_chunks = 0

        except Exception as e:
            logger.error("Recording error", error=str(e))

    def _detect_speech(self, audio_chunk: np.ndarray) -> bool:
        """Detect if audio chunk contains speech."""
        if self._vad_model is not None:
            # Use Silero VAD
            try:
                audio_tensor = torch.from_numpy(audio_chunk)
                speech_prob = self._vad_model(audio_tensor, self._sample_rate).item()
                return speech_prob >= self.whisper_config.vad_threshold
            except Exception as e:
                logger.debug("Silero VAD inference failed", error=str(e), exc_info=True)

        # Fallback: energy-based detection
        energy = np.sqrt(np.mean(audio_chunk**2))
        return energy > 0.01  # Simple threshold

    def _process_audio(self, audio_buffer: list[np.ndarray]) -> None:
        """Process accumulated audio through Whisper."""
        if not audio_buffer or self._whisper_model is None:
            return

        # Concatenate audio
        audio = np.concatenate(audio_buffer)

        # Pad/trim to 30 seconds (Whisper's expected input)
        audio = whisper.pad_or_trim(audio)

        # Transcribe
        try:
            # Get language
            language = (
                None
                if self.whisper_config.language == "auto"
                else self.whisper_config.language
            )

            result = self._whisper_model.transcribe(
                audio,
                language=language,
                fp16=(self.whisper_config.device == "cuda"),
            )

            text = result["text"].strip()

            if text:
                # Log ASR result
                logger.info("ASR transcription", text=text)

                asr_result = ASRResult(
                    text=text,
                    language=result.get("language", "unknown"),
                    confidence=1.0,
                    is_final=True,
                    duration=len(audio) / self._sample_rate,
                )
                self._transcription_queue.put(asr_result)
                logger.debug("Transcription complete", text=text[:50])

        except Exception as e:
            logger.error("Transcription error", error=str(e))

    async def _stop_listening(self) -> None:
        """Stop audio recording."""
        self._stop_event.set()

        if self._recording_thread and self._recording_thread.is_alive():
            self._recording_thread.join(timeout=2.0)
            self._recording_thread = None

        # Clear models to free memory
        self._whisper_model = None
        self._vad_model = None

        logger.info("Whisper ASR stopped")

    async def _transcribe(self) -> ASRResult | None:
        """Get next transcription from queue."""
        while self._running:
            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._transcription_queue.get(timeout=0.1),
                )
                return result
            except Empty:
                continue

        return None


# Factory function for config-based creation
def create_whisper_config(options: dict[str, Any]) -> WhisperConfig:
    """Create WhisperConfig from config options."""
    return WhisperConfig(
        language=options.get("language", "auto"),
        sample_rate=options.get("sample_rate", 16000),
        vad_enabled=options.get("vad_enabled", True),
        vad_threshold=options.get("vad_threshold", 0.5),
        min_speech_duration=options.get("min_speech_duration", 0.25),
        max_speech_duration=options.get("max_speech_duration", 30.0),
        silence_duration=options.get("silence_duration", 0.8),
        model=options.get("model", "large-v3"),
        device=options.get("device", "cuda"),
        speech_pad_ms=options.get("speech_pad_ms", 300),
        min_speech_ms=options.get("min_speech_ms", 250),
        min_silence_ms=options.get("min_silence_ms", 500),
        max_speech_s=options.get("max_speech_s", 30.0),
    )
