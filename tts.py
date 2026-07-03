"""
M1.4 TTS engine: ElevenLabs eleven_flash_v2_5 WebSocket streaming.

Design:
- WebSocket streaming for low latency (~75ms synthesis)
- Output format: pcm_16000 (16kHz PCM, matches our audio pipeline)
- No external SDK dependency — raw websockets for minimal footprint
- Async generator yields PCM chunks for streaming to client

Protocol (ElevenLabs WebSocket v1):
  → {"text": "...", "voice_settings": {...}}   (text input)
  → {"text": ""}                                (end of input)
  ← {"audio": "<base64>", ...}                  (audio chunk)
  ← {"isFinal": true}                           (done)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import AsyncGenerator

import websockets

logger = logging.getLogger(__name__)

TTS_WS_URL = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"


class TTSEngine:
    """ElevenLabs streaming TTS via WebSocket."""

    def __init__(
        self,
        api_key: str | None = None,
        voice_id: str | None = None,
    ):
        self._api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self._voice_id = voice_id or os.environ.get(
            "ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"
        )

        if not self._api_key:
            logger.warning("ELEVENLABS_API_KEY not set — TTS will fail")

        logger.info("TTS ready: voice=%s", self._voice_id)

    async def speak(self, text: str) -> AsyncGenerator[bytes, None]:
        """
        Synthesize text to PCM audio (16kHz, 16-bit, mono).

        Yields raw PCM chunks for streaming to client via WS binary frames.

        Args:
            text: Text to synthesize (should be a complete sentence)

        Yields:
            PCM audio bytes (16kHz, 16-bit, mono)
        """
        if not self._api_key:
            logger.error("TTS skipped: no API key")
            return

        if not text.strip():
            return

        url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{self._voice_id}/stream-input"
            f"?model_id=eleven_flash_v2_5&output_format=pcm_16000"
        )
        headers = {"xi-api-key": self._api_key}

        try:
            async with websockets.connect(url, additional_headers=headers) as ws:
                # Send BOS (beginning of stream) with voice settings
                await ws.send(json.dumps({
                    "text": " ",
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                    },
                    "xi-api-key": self._api_key,
                }))

                # Send the actual text
                await ws.send(json.dumps({"text": text}))

                # Send EOS (end of stream)
                await ws.send(json.dumps({"text": ""}))

                # Receive and yield audio chunks as they arrive (true streaming)
                # 8s timeout per message (§9: TTS first packet 8s timeout)
                deadline = asyncio.get_running_loop().time() + 8.0
                while True:
                    try:
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            logger.error("TTS timeout: no response within 8s")
                            return
                        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        logger.error("TTS timeout waiting for next message")
                        return

                    if isinstance(raw, bytes):
                        yield raw
                        continue

                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if "audio" in msg and msg["audio"]:
                        yield base64.b64decode(msg["audio"])

                    if msg.get("error"):
                        logger.error("TTS API error: %s", msg["error"])
                        return

                    if msg.get("isFinal"):
                        return

                    # Reset deadline after first successful message
                    deadline = asyncio.get_running_loop().time() + 8.0

        except websockets.exceptions.ConnectionClosed as e:
            logger.error("TTS WebSocket closed: %s", e)
        except Exception as e:
            logger.error("TTS error: %s", e)
