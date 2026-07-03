"""
Jarvis Brain — WS endpoint + token auth + STT + LLM + TTS pipeline.
Protocol: §6 control frames (JSON text) + §5 audio frames (binary).
"""
import asyncio
import json
import os
import struct
import time
import logging
import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from stt import STTEngine
from llm import LLMEngine
from tts import TTSEngine

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("jarvis-brain")

JARVIS_TOKEN = os.environ.get("JARVIS_TOKEN", "dev-token-change-me")
AUTH_TIMEOUT = 5.0

app = FastAPI(title="Jarvis Brain", version="0.3.0")

stt = STTEngine()
llm = LLMEngine()
tts = TTSEngine()


# ── Turn manager ──────────────────────────────────────────────────
class TurnManager:
    """§6.3: first-come-first-served turn arbitration.

    Concurrency fix: _finalized flag prevents _finalize_turn from running twice
    when both VAD utterance_end and client audio_done arrive for the same turn.

    Cancel support: _cancel_event is checked by the pipeline to abort mid-turn.
    """

    def __init__(self):
        self.current_device: Optional[str] = None
        self.current_turn_id: Optional[int] = None
        self._finalized: bool = False
        self._cancel_event: asyncio.Event = asyncio.Event()

    def acquire(self, device_id: str, turn_id: int) -> bool:
        if self.current_device is not None and self.current_device != device_id:
            return False
        self.current_device = device_id
        self.current_turn_id = turn_id
        self._finalized = False
        self._cancel_event.clear()
        stt.start_turn()
        return True

    def mark_finalized(self) -> bool:
        """Mark turn as finalized. Returns True if this is the first call (should proceed)."""
        if self._finalized:
            return False
        self._finalized = True
        return True

    def cancel(self):
        """Signal cancellation for the current turn."""
        self._cancel_event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def release(self):
        self.current_device = None
        self.current_turn_id = None
        self._finalized = False
        self._cancel_event.clear()


turn_mgr = TurnManager()


# ── Connection manager ────────────────────────────────────────────
MAX_HISTORY = 20  # Max messages per device (balances context vs memory)


class ConnectionManager:
    def __init__(self):
        self._devices: dict[str, WebSocket] = {}
        self._history: dict[str, list[dict]] = {}  # Per-device conversation history
        self._last_turn: dict[str, tuple[str, str]] = {}  # Last (user, assistant) per device

    async def register(self, ws: WebSocket, device_id: str):
        old = self._devices.pop(device_id, None)
        if old:
            try:
                await old.close(code=4001, reason="duplicate_device")
            except Exception:
                pass
        self._devices[device_id] = ws
        # Preserve existing history if device reconnects
        if device_id not in self._history:
            self._history[device_id] = []
        logger.info("device connected: %s", device_id)

        # M4.1: Send session_sync on reconnect (last turn context)
        last = self.get_last_turn(device_id)
        if last:
            user_text, assistant_text = last
            try:
                await _send(ws, {
                    "type": "session_sync",
                    "turn_id": 0,
                    "user_text": user_text,
                    "assistant_text": assistant_text,
                })
                logger.info("session_sync sent to %s on reconnect", device_id)
            except Exception:
                pass

    def remove(self, device_id: str):
        self._devices.pop(device_id, None)
        # Keep history for a while (in case of reconnect)
        logger.info("device disconnected: %s", device_id)

    def get(self, device_id: str) -> Optional[WebSocket]:
        return self._devices.get(device_id)

    def get_history(self, device_id: str) -> list[dict]:
        return self._history.get(device_id, [])

    def add_history(self, device_id: str, user_text: str, assistant_text: str):
        """Add a turn to conversation history, capping at MAX_HISTORY."""
        if device_id not in self._history:
            self._history[device_id] = []
        h = self._history[device_id]
        h.append({"role": "user", "content": user_text})
        h.append({"role": "assistant", "content": assistant_text})
        # Trim to keep last N messages
        if len(h) > MAX_HISTORY:
            self._history[device_id] = h[-MAX_HISTORY:]
        # Store last turn for session_sync on reconnect
        self._last_turn[device_id] = (user_text, assistant_text)

    def get_last_turn(self, device_id: str) -> tuple[str, str] | None:
        """Get the last turn for session_sync on reconnect."""
        return self._last_turn.get(device_id)

    def clear_history(self, device_id: str):
        self._history.pop(device_id, None)

    @property
    def count(self) -> int:
        return len(self._devices)


manager = ConnectionManager()


# ── Routes ────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"service": "jarvis-brain", "version": "0.3.0", "devices": manager.count}

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "stt": "ready",
        "llm": "ready",
        "tts": "ready",
        "devices": manager.count,
    }

@app.post("/history/{device_id}/clear")
async def clear_history(device_id: str):
    manager.clear_history(device_id)
    return {"status": "ok", "message": f"history cleared for {device_id}"}


# ── WS handler ────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()

    # §6.1 Auth
    device_id = await _do_auth(ws)
    if device_id is None:
        return

    await manager.register(ws, device_id)

    try:
        while True:
            data = await ws.receive()
            if data["type"] == "websocket.disconnect":
                break
            if "text" in data:
                await _handle_text(ws, device_id, data["text"])
            elif "bytes" in data:
                await _handle_audio(ws, device_id, data["bytes"])
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("error for device %s", device_id)
    finally:
        if turn_mgr.current_device == device_id:
            turn_mgr.release()
        manager.remove(device_id)


async def _do_auth(ws: WebSocket) -> Optional[str]:
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT)
    except asyncio.TimeoutError:
        await _send(ws, {"type": "auth_fail", "reason": "auth_timeout"})
        await ws.close(code=4000)
        return None
    except WebSocketDisconnect:
        return None

    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await _send(ws, {"type": "auth_fail", "reason": "invalid_json"})
        await ws.close(code=4000)
        return None

    if msg.get("type") != "auth":
        await _send(ws, {"type": "auth_fail", "reason": "auth_required_first"})
        await ws.close(code=4000)
        return None

    if msg.get("token", "") != JARVIS_TOKEN:
        await _send(ws, {"type": "auth_fail", "reason": "invalid_token"})
        await ws.close(code=4000)
        return None

    did = msg.get("device_id", f"unknown-{uuid.uuid4().hex[:6]}")
    platform = msg.get("platform", "unknown")
    await _send(ws, {"type": "auth_ok", "server_time": int(time.time())})
    logger.info("auth ok: %s (%s)", did, platform)
    return did


# ── Text frame handler ────────────────────────────────────────────
async def _handle_text(ws: WebSocket, device_id: str, raw: str):
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await _send(ws, {"type": "error", "message": "invalid json"})
        return

    mtype = msg.get("type", "")

    if mtype == "wake_event":
        await _on_wake(ws, device_id, msg)
    elif mtype == "audio_done":
        await _on_audio_done(ws, device_id, msg)
    elif mtype == "cancel":
        await _on_cancel(ws, device_id, msg)
    elif mtype == "ping":
        await _send(ws, {"type": "pong"})
    else:
        await _send(ws, {"type": "echo", "original": msg})  # M1.1 compat


async def _on_wake(ws: WebSocket, device_id: str, msg: dict):
    turn_id = msg.get("turn_id", 0)

    if not turn_mgr.acquire(device_id, turn_id):
        await _send(ws, {
            "type": "turn_rejected", "turn_id": turn_id,
            "reason": "busy_other_device",
        })
        return

    await _send(ws, {"type": "turn_accepted", "turn_id": turn_id})
    logger.info("turn %d started by %s", turn_id, device_id)


async def _on_audio_done(ws: WebSocket, device_id: str, msg: dict):
    """Client signaled end of audio (15s timeout or manual). Flush STT."""
    turn_id = msg.get("turn_id", 0)
    if turn_mgr.current_device != device_id or turn_mgr.current_turn_id != turn_id:
        return

    if not turn_mgr.mark_finalized():
        return  # Already finalized by VAD utterance_end

    await _send(ws, {"type": "utterance_end", "turn_id": turn_id})
    await _finalize_turn(ws, device_id, turn_id)


async def _on_cancel(ws: WebSocket, device_id: str, msg: dict):
    turn_id = msg.get("turn_id", 0)
    if turn_mgr.current_device != device_id or turn_mgr.current_turn_id != turn_id:
        return
    turn_mgr.cancel()
    turn_mgr.release()
    await _send(ws, {"type": "state", "turn_id": turn_id, "value": "cancelled"})
    logger.info("turn %d cancelled by %s", turn_id, device_id)


# ── Audio binary frame handler (§5) ──────────────────────────────
async def _handle_audio(ws: WebSocket, device_id: str, data: bytes):
    """Parse binary frame: [0x01][turn_id:4 LE][PCM...]"""
    if len(data) < 6:
        return

    channel = data[0]
    if channel != 0x01:
        return  # Not mic audio

    frame_turn_id = struct.unpack("<I", data[1:5])[0]
    if frame_turn_id != turn_mgr.current_turn_id:
        logger.debug("discarding audio frame for stale turn %d", frame_turn_id)
        return
    if device_id != turn_mgr.current_device:
        return  # not this device's turn

    pcm = data[5:]

    # Feed STT, check for utterance end
    utterance_done = stt.feed(pcm)

    if utterance_done and turn_mgr.mark_finalized():
        await _send(ws, {
            "type": "utterance_end",
            "turn_id": turn_mgr.current_turn_id,
        })
        await _finalize_turn(ws, device_id, turn_mgr.current_turn_id)


# ── Turn finalization: STT → LLM → TTS pipeline ──────────────────
async def _finalize_turn(ws: WebSocket, device_id: str, turn_id: int):
    """§7 pipeline: STT → LLM (streaming) → TTS (streaming) → client."""
    # 1. STT result
    seg = stt.pop()
    if seg is None:
        logger.warning("no speech segment for turn %d", turn_id)
        turn_mgr.release()
        return

    await _send(ws, {
        "type": "stt_result",
        "turn_id": turn_id,
        "text": seg.text,
    })
    logger.info("turn %d: stt_result=%r (%.1fs)", turn_id, seg.text, seg.duration)

    # Skip LLM if speech was empty/inaudible
    if not seg.text.strip():
        await _send(ws, {"type": "state", "turn_id": turn_id, "value": "done"})
        turn_mgr.release()
        return

    # 2. LLM → TTS streaming pipeline
    if turn_mgr.is_cancelled:
        turn_mgr.release()
        return

    await _send(ws, {"type": "state", "turn_id": turn_id, "value": "thinking"})

    history = manager.get_history(device_id)
    tts_chunk_count = 0
    full_response = ""

    try:
        async for sentence in llm.stream(seg.text, history=history):
            if turn_mgr.is_cancelled:
                logger.info("turn %d: cancelled during LLM streaming", turn_id)
                break

            full_response += sentence

            # First sentence: notify client that TTS is starting
            if tts_chunk_count == 0:
                await _send(ws, {"type": "state", "turn_id": turn_id, "value": "speaking"})

            # Stream TTS audio for this sentence
            async for audio_chunk in tts.speak(sentence):
                if turn_mgr.is_cancelled:
                    break
                if audio_chunk:
                    # §5 binary frame: [0x02][turn_id:4 LE][PCM data]
                    frame = b"\x02" + struct.pack("<I", turn_id) + audio_chunk
                    await ws.send_bytes(frame)
                    tts_chunk_count += 1

            if turn_mgr.is_cancelled:
                break

    except Exception as e:
        logger.error("pipeline error turn %d: %s", turn_id, e)
        await _send(ws, {
            "type": "error",
            "turn_id": turn_id,
            "stage": "llm|tts",
            "message": str(e),
        })
        turn_mgr.release()
        return

    # 3. Update conversation history & send done
    if full_response and not turn_mgr.is_cancelled:
        manager.add_history(device_id, seg.text, full_response)

    # 4. Broadcast session_sync for cross-device continuity (§6.2)
    if not turn_mgr.is_cancelled:
        await _broadcast_session_sync(device_id, turn_id, seg.text, full_response)
        await _send(ws, {"type": "tts_done", "turn_id": turn_id})
        logger.info("turn %d: done (%d audio chunks)", turn_id, tts_chunk_count)
    else:
        await _send(ws, {"type": "state", "turn_id": turn_id, "value": "cancelled"})
        logger.info("turn %d: cancelled", turn_id)

    turn_mgr.release()


# ── Helpers ───────────────────────────────────────────────────────
async def _send(ws: WebSocket, msg: dict):
    await ws.send_text(json.dumps(msg, ensure_ascii=False))


async def _broadcast_session_sync(
    source_device: str, turn_id: int, user_text: str, assistant_text: str
):
    """§6.2: Broadcast session_sync to all devices except the source."""
    sync_msg = {
        "type": "session_sync",
        "turn_id": turn_id,
        "user_text": user_text,
        "assistant_text": assistant_text,
    }
    for did, ws in manager._devices.items():
        if did != source_device:
            try:
                await _send(ws, sync_msg)
            except Exception:
                pass  # Device might be disconnected


# ── Entry ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
