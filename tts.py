"""
M1.4 TTS engine: mimo-v2.5-tts via OpenAI-compatible API.

Supports any OpenAI-compatible TTS endpoint (mimo, OpenAI, etc.).

Design:
- HTTP streaming for low latency
- Output format: PCM 16kHz (matches audio pipeline)
- Async generator yields PCM chunks
"""
from __future__ import annotations

import logging
import os
from typing import AsyncGenerator

import httpx

logger = logging.getLogger(__name__)


class TTSEngine:
    """OpenAI-compatible TTS via HTTP streaming."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        voice: str | None = None,
    ):
        self._api_key = api_key or os.environ.get("TTS_API_KEY", os.environ.get("LLM_API_KEY", ""))
        self._api_base = api_base or os.environ.get("TTS_API_BASE", os.environ.get("LLM_API_BASE", "https://api.xiaomimimo.com/v1"))
        self._model = model or os.environ.get("TTS_MODEL", "mimo-v2.5-tts")
        self._voice = voice or os.environ.get("TTS_VOICE", "alloy")

        if not self._api_key:
            logger.warning("TTS_API_KEY not set — TTS will fail")

        logger.info("TTS ready: model=%s, voice=%s", self._model, self._voice)

    async def speak(self, text: str) -> AsyncGenerator[bytes, None]:
        """
        Synthesize text to PCM audio (16kHz, 16-bit, mono).

        Yields raw PCM chunks for streaming to client via WS binary frames.
        """
        if not self._api_key:
            logger.error("TTS skipped: no API key")
            return

        if not text.strip():
            return

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self._model,
            "input": text,
            "voice": self._voice,
            "response_format": "pcm",
            "speed": 1.0,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream(
                    "POST",
                    f"{self._api_base}/audio/speech",
                    json=payload,
                    headers=headers,
                ) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes(chunk_size=4096):
                        if chunk:
                            yield chunk

        except httpx.HTTPStatusError as e:
            logger.error("TTS API error: %s %s", e.response.status_code, e.response.text[:200])
        except httpx.TimeoutException:
            logger.error("TTS timeout")
        except Exception as e:
            logger.error("TTS error: %s", e)
