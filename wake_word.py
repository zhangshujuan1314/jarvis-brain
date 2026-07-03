"""
M2 Wake word detection for PC using Porcupine (Picovoice).

Local, offline, low-power wake word detection.
Listens for "贾维斯" (Jarvis) keyword.

Setup:
  1. Get AccessKey: https://console.picovoice.ai/
  2. Train wake word "贾维斯" at Picovoice Console
  3. Download .ppn file for your platform (Windows/Linux/macOS)
  4. Set environment: PORCUPINE_ACCESS_KEY=your-key
  5. Place .ppn file as: models/jarvis.ppn

Usage:
  from wake_word import WakeWordDetector

  detector = WakeWordDetector(on_wake=lambda: print("Wake!"))
  detector.start()
  # ... detector runs in background ...
  detector.stop()

CLI test:
  python wake_word.py
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent / "models"
DEFAULT_KEYWORD = MODEL_DIR / "jarvis.ppn"
SENSITIVITY = 0.7


class WakeWordDetector:
    """Porcupine wake word detector for PC.

    Uses pvporcupine Python package for local, offline detection.
    Falls back gracefully if Porcupine is not available.
    """

    def __init__(
        self,
        on_wake: Callable[[], None],
        access_key: str | None = None,
        keyword_path: Path | None = None,
        sensitivity: float = SENSITIVITY,
    ):
        self._on_wake = on_wake
        self._access_key = access_key or os.environ.get("PORCUPINE_ACCESS_KEY", "")
        self._keyword_path = keyword_path or DEFAULT_KEYWORD
        self._sensitivity = sensitivity
        self._porcupine = None
        self._pa = None
        self._audio_stream = None
        self._running = False

    @property
    def is_configured(self) -> bool:
        """Check if Porcupine is properly configured."""
        if not self._access_key:
            return False
        if not self._keyword_path.exists():
            return False
        try:
            import pvporcupine
            return True
        except ImportError:
            return False

    def start(self) -> bool:
        """Start listening for wake word. Returns True if started successfully."""
        if self._running:
            return True

        if not self.is_configured:
            logger.warning(
                "Wake word not configured. Need: PORCUPINE_ACCESS_KEY + %s",
                self._keyword_path,
            )
            return False

        try:
            import pvporcupine
            import pyaudio

            self._porcupine = pvporcupine.create(
                access_key=self._access_key,
                keyword_paths=[str(self._keyword_path)],
                sensitivities=[self._sensitivity],
            )

            self._pa = pyaudio.PyAudio()
            self._audio_stream = self._pa.open(
                rate=self._porcupine.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=self._porcupine.frame_length,
            )

            self._running = True

            # Start detection thread
            import threading
            self._thread = threading.Thread(target=self._listen_loop, daemon=True)
            self._thread.start()

            logger.info(
                "Wake word listening started (keyword=%s, sensitivity=%.1f)",
                self._keyword_path.stem,
                self._sensitivity,
            )
            return True

        except ImportError as e:
            logger.error("Missing package: %s (pip install pvporcupine pyaudio)", e)
            return False
        except Exception as e:
            logger.error("Wake word init failed: %s", e)
            self._cleanup()
            return False

    def stop(self):
        """Stop listening."""
        self._running = False
        if hasattr(self, '_thread'):
            self._thread.join(timeout=3)
        self._cleanup()
        logger.info("Wake word stopped")

    def _listen_loop(self):
        """Background thread: read audio frames and check for wake word."""
        try:
            while self._running:
                pcm = self._audio_stream.read(
                    self._porcupine.frame_length,
                    exception_on_overflow=False,
                )
                keyword_index = self._porcupine.process(pcm)
                if keyword_index >= 0 and self._running:
                    logger.info("Wake word detected! (index=%d)", keyword_index)
                    self._on_wake()
        except Exception as e:
            if self._running:
                logger.error("Wake word listen error: %s", e)

    def _cleanup(self):
        """Release Porcupine and audio resources."""
        if self._audio_stream:
            try:
                self._audio_stream.close()
            except Exception:
                pass
            self._audio_stream = None
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        if self._porcupine:
            try:
                self._porcupine.delete()
            except Exception:
                pass
            self._porcupine = None


# ── CLI test ──────────────────────────────────────────────────────
def _test():
    """Test wake word detection from command line."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    detector = WakeWordDetector(
        on_wake=lambda: print("\n🔔 贾维斯！我在。\n"),
    )

    if not detector.is_configured:
        print("Wake word not configured!")
        print()
        print("Setup steps:")
        print("  1. Get AccessKey: https://console.picovoice.ai/")
        print("  2. Train '贾维斯' at Picovoice Console")
        print("  3. Download .ppn → models/jarvis.ppn")
        print("  4. Set: export PORCUPINE_ACCESS_KEY=your-key")
        print("  5. Install: pip install pvporcupine pyaudio")
        sys.exit(1)

    print("Listening for '贾维斯'... (Ctrl+C to stop)")
    detector.start()

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopping...")
        detector.stop()


if __name__ == "__main__":
    _test()
