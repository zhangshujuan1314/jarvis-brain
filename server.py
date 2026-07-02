"""
Jarvis Brain — WS endpoint + token auth + STT pipeline.
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

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("jarvis-brain")

JARVIS_TOKEN = os.environ.get("JARVIS_TOKEN", "dev-token-change-me")
AUTH_TIMEOUT = 5.0

app = FastAPI(title="Jarvis Brain", version="0.2.0")

stt = STTEngine()


# ── Turn manager ──────────────────────────────────────────────────
class TurnManager:
    """§6.3: first-come-first-served turn arbitration."""

    def __init__(self):
        self.current_device: Optional[str] = None
        self.current_turn_id: Optional[int] = None

    def acquire(self, device_id: str, turn_id: int) -> bool:
        if self.current_device is not None and self.current_device != device_id:
            return False
        self.current_device = device_id
        self.current_turn_id = turn_id
        stt.start_turn()
        return True

    def release(self):
        self.current_device = None
        self.current_turn_id = None


turn_mgr = TurnManager()


# ── Connection manager ────────────────────────────────────────────
class ConnectionManager:
    def __init__(self):
        self._devices: dict[str, WebSocket] = {}

    async def register(self, ws: WebSocket, device_id: str):
        old = self._devices.pop(device_id, None)
        if old:
            try:
                await old.close(code=4001, reason="duplicate_device")
            except Exception:
                pass
        self._devices[device_id] = ws
        logger.info("device connected: %s", device_id)

    def remove(self, device_id: str):
        self._devices.pop(device_id, None)
        logger.info("device disconnected: %s", device_id)

    def get(self, device_id: str) -> Optional[WebSocket]:
        return self._devices.get(device_id)

    @property
    def count(self) -> int:
        return len(self._devices)


manager = ConnectionManager()


# ── Routes ────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {"service": "jarvis-brain", "version": "0.2.0", "devices": manager.count}

@app.get("/health")
async def health():
    return {"status": "ok", "stt": "ready"}


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

    await _send(ws, {"type": "utterance_end", "turn_id": turn_id})
    await _finalize_turn(ws, device_id, turn_id)


async def _on_cancel(ws: WebSocket, device_id: str, msg: dict):
    turn_id = msg.get("turn_id", 0)
    if turn_mgr.current_device != device_id or turn_mgr.current_turn_id != turn_id:
        return
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

    if utterance_done:
        await _send(ws, {
            "type": "utterance_end",
            "turn_id": turn_mgr.current_turn_id,
        })
        await _finalize_turn(ws, device_id, turn_mgr.current_turn_id)


async def _finalize_turn(ws: WebSocket, device_id: str, turn_id: int):
    """Run ASR on completed utterance, send result, release turn."""
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

    # For M1.2, echo the text back as TTS placeholder
    # (real TTS in M1.4)
    await _send(ws, {
        "type": "state",
        "turn_id": turn_id,
        "value": "done",
    })

    turn_mgr.release()


# ── Helpers ───────────────────────────────────────────────────────
async def _send(ws: WebSocket, msg: dict):
    await ws.send_text(json.dumps(msg, ensure_ascii=False))


# ── Entry ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
