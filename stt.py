"""
M1.2 STT engine: sherpa-onnx Paraformer CN-small + Silero VAD endpointing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import sherpa_onnx

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent / "models"
SAMPLE_RATE = 16000


@dataclass
class SpeechSegment:
    text: str
    duration: float  # seconds


class STTEngine:
    """Offline ASR with integrated VAD. Per-turn: feed audio chunks,
    VAD detects utterance end → pop() returns recognized text."""

    def __init__(
        self,
        model_dir: Path = MODEL_DIR,
        vad_silence_s: float = 0.8,
        vad_threshold: float = 0.5,
        max_speech_s: float = 15.0,
        min_speech_s: float = 0.3,
    ):
        self._asr_dir = model_dir / "csukuangfj_sherpa-onnx-paraformer-zh-small-2024-03-09"
        self._vad_path = model_dir / "istupakov_silero-vad-onnx" / "silero_vad.onnx"
        self._vad_silence_s = vad_silence_s
        self._vad_threshold = vad_threshold
        self._max_speech_s = max_speech_s
        self._min_speech_s = min_speech_s

        if not self._asr_dir.exists():
            raise FileNotFoundError(f"ASR model not found at {self._asr_dir}")
        if not self._vad_path.exists():
            raise FileNotFoundError(f"VAD model not found at {self._vad_path}")

        # ASR — created once, reused
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
            paraformer=str(self._asr_dir / "model.int8.onnx"),
            tokens=str(self._asr_dir / "tokens.txt"),
            num_threads=2,
            sample_rate=SAMPLE_RATE,
        )

        # VAD — recreated per turn via _new_vad()
        self._vad: Optional[sherpa_onnx.VoiceActivityDetector] = None

        logger.info(
            "STT ready: Paraformer + SileroVAD (silence=%.1fs)", vad_silence_s
        )

    # ── Public API ───────────────────────────────────────────────

    def start_turn(self):
        """Begin a new turn — reset VAD state."""
        if self._vad is None:
            self._vad = self._new_vad()
        else:
            self._vad.reset()

    def feed(self, pcm_bytes: bytes) -> bool:
        """
        Feed PCM16 audio chunk (§5: 16kHz, 16bit, mono).
        Returns True if utterance end detected (VAD has completed segment).
        """
        if self._vad is None:
            self.start_turn()
        samples = _pcm_to_f32(pcm_bytes)
        if len(samples) == 0:
            return False
        self._vad.accept_waveform(samples)
        return not self._vad.empty()

    def has_utterance(self) -> bool:
        return self._vad is not None and not self._vad.empty()

    def pop(self) -> Optional[SpeechSegment]:
        """Get next completed speech segment with recognized text, or None.

        If VAD has multiple segments (user paused mid-sentence), they are
        concatenated into one segment to avoid losing speech.
        """
        if self._vad is None or self._vad.empty():
            return None

        # Collect all queued segments — interleave with short silence to preserve pauses
        all_samples = []
        total_duration = 0.0
        silence_gap = np.zeros(int(SAMPLE_RATE * 0.3), dtype=np.float32)  # 300ms gap
        while not self._vad.empty():
            seg = self._vad.front()
            if all_samples:
                all_samples.extend(silence_gap)  # Preserve pause between segments
            all_samples.extend(seg.samples)
            total_duration += len(seg.samples) / SAMPLE_RATE
            self._vad.pop()

        if not all_samples:
            return None

        # Recognize concatenated audio
        text = self._transcribe(np.array(all_samples, dtype=np.float32))
        logger.info("STT: %r (%.1fs, %d segments)", text, total_duration, len(all_samples))
        return SpeechSegment(text=text, duration=total_duration)

    # ── Internals ────────────────────────────────────────────────

    def _new_vad(self):
        silero = sherpa_onnx.SileroVadModelConfig(
            model=str(self._vad_path),
            threshold=self._vad_threshold,
            min_silence_duration=self._vad_silence_s,
            min_speech_duration=self._min_speech_s,
            max_speech_duration=self._max_speech_s,
        )
        cfg = sherpa_onnx.VadModelConfig(silero_vad=silero)
        return sherpa_onnx.VoiceActivityDetector(
            cfg, buffer_size_in_seconds=self._max_speech_s + 5
        )

    def _transcribe(self, samples: np.ndarray) -> str:
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate=SAMPLE_RATE, samples=samples)
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()


def _pcm_to_f32(pcm: bytes) -> np.ndarray:
    """PCM 16-bit LE mono → float32 [-1, 1]."""
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


# ── Self-test ────────────────────────────────────────────────────
def _test():
    import time

    engine = STTEngine()
    engine.start_turn()

    sr = SAMPLE_RATE
    # 1s tone (simulate speech) + 2s silence
    tone = (np.sin(2 * np.pi * 440 * np.arange(sr) / sr) * 0.5).astype(np.float32)
    silence = np.zeros(sr, dtype=np.float32)

    engine.feed(_f32_to_pcm(tone))
    print(f"After 1s tone: has_utterance={engine.has_utterance()}")

    engine.feed(_f32_to_pcm(silence))
    engine.feed(_f32_to_pcm(silence))
    print(f"After 2s silence: has_utterance={engine.has_utterance()}")

    seg = engine.pop()
    if seg:
        print(f"Segment: {seg.duration:.1f}s, text={seg.text!r}")
    else:
        print("No segment — need more silence (try increasing silence feed)")

    print("Self-test done.")


def _f32_to_pcm(samples: np.ndarray) -> bytes:
    return (samples * 32767).astype(np.int16).tobytes()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _test()
