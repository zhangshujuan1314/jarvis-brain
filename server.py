"""
Jarvis Brain — WS endpoint + token auth + STT + LLM + TTS pipeline.
Protocol: §6 control frames (JSON text) + §5 audio frames (binary).
"""
import asyncio
import json
import os
import struct
import sys
import time
import logging
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import validate_all, print_config_summary
from structured_logging import setup_logging
from plugins import PluginManager

# Lazy imports — these may fail if deps are missing
STTEngine = None
LLMEngine = None
TTSEngine = None

try:
    from stt import STTEngine
except ImportError as e:
    print(f"STT module not available: {e}")

try:
    from llm import LLMEngine
except ImportError as e:
    print(f"LLM module not available: {e}")

try:
    from tts import TTSEngine
except ImportError as e:
    print(f"TTS module not available: {e}")

load_dotenv()
setup_logging()

logger = logging.getLogger("jarvis-brain")

JARVIS_TOKEN = os.environ.get("JARVIS_TOKEN", "dev-token-change-me")
AUTH_TIMEOUT = 5.0
WS_PING_INTERVAL = 30.0  # Send WS ping every 30s to keep connection alive
RATE_LIMIT_TURNS = 10    # Max turns per device per minute
RATE_LIMIT_WINDOW = 60.0 # Rate limit window in seconds

app = FastAPI(title="Jarvis Brain", version="0.4.0")

# Startup validation (non-fatal — warn but continue)
validate_all()
print_config_summary()

# Initialize components (graceful degradation)
stt = None
if STTEngine:
    try:
        stt = STTEngine()
    except Exception as e:
        logger.warning("STT not available: %s", e)
else:
    logger.warning("STT module not imported")

tts = None
if TTSEngine:
    try:
        tts = TTSEngine()
    except Exception as e:
        logger.warning("TTS not available: %s", e)
else:
    logger.warning("TTS module not imported")

# Initialize plugin system (non-fatal)
plugins = PluginManager()
try:
    plugins.load_all()
except Exception as e:
    logger.warning("plugin loading error: %s", e)

# LLM with plugin tools
llm = None
if LLMEngine:
    try:
        llm = LLMEngine(extra_tools=plugins.tools)
        llm.set_executor(plugins.execute)
    except Exception as e:
        logger.warning("LLM not available: %s", e)
else:
    logger.warning("LLM module not imported")

logger.info("plugins loaded: %s", [p["name"] for p in plugins.list_plugins()])


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
        if stt: stt.start_turn()
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
MAX_HISTORY = 20  # Max messages in shared session history
MAX_SYNC_TURNS = 3  # Max recent turns to send on device connect


class ConnectionManager:
    """Manages connected devices and shared session state.

    M3 design: All devices share a SINGLE conversation history.
    This enables cross-device continuity — device B can continue
    a conversation started on device A.

    On connect:
      - New device gets the last MAX_SYNC_TURNS turns via session_sync
      - Reconnecting device also gets recent context

    On turn completion:
      - Shared history updated
      - session_sync broadcast to ALL other devices
    """

    def __init__(self):
        self._devices: dict[str, WebSocket] = {}
        self._device_info: dict[str, dict] = {}  # platform, connected_at, etc.
        # M3: SHARED session history (not per-device)
        self._history: list[dict] = []
        self._last_user_text: str = ""
        self._last_assistant_text: str = ""
        # Rate limiting
        self._turn_times: dict[str, list[float]] = {}

    async def register(self, ws: WebSocket, device_id: str, platform: str = "unknown"):
        old = self._devices.pop(device_id, None)
        if old:
            try:
                await old.close(code=4001, reason="duplicate_device")
            except Exception:
                pass
        self._devices[device_id] = ws
        self._device_info[device_id] = {
            "platform": platform,
            "connected_at": time.time(),
        }
        logger.info("device connected: %s (%s)", device_id, platform)

        # M3: Send recent session history to newly connected device
        await self._sync_history_to_device(ws, device_id)

    async def _sync_history_to_device(self, ws: WebSocket, device_id: str):
        """Send recent conversation history to a device (on connect/reconnect)."""
        if not self._history:
            return

        # Send the last N turns
        recent = self._history[-(MAX_SYNC_TURNS * 2):]  # *2 because user+assistant per turn
        for i in range(0, len(recent) - 1, 2):
            user_msg = recent[i]
            asst_msg = recent[i + 1] if i + 1 < len(recent) else None
            try:
                await _send(ws, {
                    "type": "session_sync",
                    "turn_id": 0,
                    "user_text": user_msg.get("content", ""),
                    "assistant_text": asst_msg.get("content", "") if asst_msg else "",
                })
            except Exception:
                break

        logger.info("synced %d history entries to %s", len(recent), device_id)

    def remove(self, device_id: str):
        self._devices.pop(device_id, None)
        self._device_info.pop(device_id, None)
        logger.info("device disconnected: %s", device_id)

    def get(self, device_id: str) -> Optional[WebSocket]:
        return self._devices.get(device_id)

    def get_history(self) -> list[dict]:
        """Get shared conversation history (for LLM context)."""
        return list(self._history)

    def add_history(self, user_text: str, assistant_text: str):
        """Add a turn to SHARED conversation history."""
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": assistant_text})
        # Cap history
        if len(self._history) > MAX_HISTORY:
            self._history = self._history[-MAX_HISTORY:]
        # Store last turn for quick access
        self._last_user_text = user_text
        self._last_assistant_text = assistant_text

    def get_last_turn(self) -> tuple[str, str] | None:
        """Get the last (user_text, assistant_text) for session_sync."""
        if self._last_user_text:
            return (self._last_user_text, self._last_assistant_text)
        return None

    def get_device_list(self) -> list[dict]:
        """Get info about all connected devices."""
        result = []
        for did, info in self._device_info.items():
            result.append({
                "device_id": did,
                "platform": info.get("platform", "unknown"),
                "connected_at": info.get("connected_at", 0),
            })
        return result

    def clear_history(self):
        """Clear shared session history."""
        self._history.clear()
        self._last_user_text = ""
        self._last_assistant_text = ""

    def check_rate_limit(self, device_id: str) -> bool:
        """Returns True if the device is within rate limits."""
        now = time.time()
        if device_id not in self._turn_times:
            self._turn_times[device_id] = []
        self._turn_times[device_id] = [
            t for t in self._turn_times[device_id] if now - t < RATE_LIMIT_WINDOW
        ]
        if len(self._turn_times[device_id]) >= RATE_LIMIT_TURNS:
            return False
        self._turn_times[device_id].append(now)
        return True

    @property
    def count(self) -> int:
        return len(self._devices)


manager = ConnectionManager()


# ── Routes ────────────────────────────────────────────────────────
@app.get("/api")
async def api_info():
    return {"service": "jarvis-brain", "version": "0.4.0", "devices": manager.count}

@app.get("/")
async def root():
    """Serve web client if available, otherwise redirect to API."""
    static_index = Path(__file__).parent / "static" / "index.html"
    if static_index.exists():
        return FileResponse(static_index)
    return {"service": "jarvis-brain", "version": "0.4.0", "devices": manager.count}

# Mount static files
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "stt": "ready" if stt else "unavailable",
        "llm": "ready" if llm and llm._client else "unavailable",
        "tts": "ready" if tts and hasattr(tts, '_api_key') and tts._api_key else "unavailable",
        "plugins": len(plugins.tools),
        "devices": manager.count,
    }

@app.post("/history/{device_id}/clear")
async def clear_history(device_id: str):
    manager.clear_history()
    return {"status": "ok", "message": "session history cleared"}

@app.get("/devices")
async def list_devices():
    """List all connected devices (M3 monitoring)."""
    return {
        "devices": manager.get_device_list(),
        "count": manager.count,
    }

@app.get("/plugins")
async def list_plugins():
    """List all loaded plugins and their tools."""
    return {
        "plugins": plugins.list_plugins(),
        "total_tools": len(plugins.tools),
    }


# ── WS handler ────────────────────────────────────────────────────
@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()

    # §6.1 Auth
    auth_result = await _do_auth(ws)
    if auth_result is None:
        return

    device_id, platform = auth_result
    await manager.register(ws, device_id, platform)

    # Keepalive ping task
    async def _keepalive():
        while True:
            await asyncio.sleep(WS_PING_INTERVAL)
            try:
                await ws.send_text(json.dumps({"type": "ping"}))
            except Exception:
                break

    ping_task = asyncio.create_task(_keepalive())

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
        ping_task.cancel()
        if turn_mgr.current_device == device_id:
            turn_mgr.release()
        manager.remove(device_id)


async def _do_auth(ws: WebSocket) -> Optional[tuple[str, str]]:
    """Authenticate WebSocket connection. Returns (device_id, platform) or None."""
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
    return (did, platform)


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

    if not manager.check_rate_limit(device_id):
        await _send(ws, {
            "type": "turn_rejected", "turn_id": turn_id,
            "reason": "rate_limit_exceeded",
        })
        logger.warning("rate limit exceeded for %s", device_id)
        return

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
    if not stt:
        return  # STT not available

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
    if not stt:
        await _send(ws, {"type": "error", "turn_id": turn_id, "stage": "stt", "message": "STT not available"})
        turn_mgr.release()
        return

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

    if not llm:
        await _send(ws, {"type": "error", "turn_id": turn_id, "stage": "llm", "message": "LLM not available"})
        turn_mgr.release()
        return

    await _send(ws, {"type": "state", "turn_id": turn_id, "value": "thinking"})

    history = manager.get_history()
    tts_chunk_count = 0
    full_response = ""

    t_llm_start = time.time()
    t_tts_start = None
    tts_errors = 0

    try:
        async for sentence in llm.stream(seg.text, history=history):
            if turn_mgr.is_cancelled:
                logger.info("turn %d: cancelled during LLM streaming", turn_id)
                break

            full_response += sentence

            # First sentence: notify client + record LLM→TTS transition
            if tts_chunk_count == 0:
                t_tts_start = time.time()
                llm_ms = (t_tts_start - t_llm_start) * 1000
                await _send(ws, {"type": "state", "turn_id": turn_id, "value": "speaking"})
                logger.info("turn %d: LLM first sentence in %.0fms", turn_id, llm_ms)

            # Stream TTS audio for this sentence (per-sentence error recovery)
            try:
                if not tts:
                    logger.warning("TTS not available, skipping audio for: %s", sentence[:30])
                    continue
                async for audio_chunk in tts.speak(sentence):
                    if turn_mgr.is_cancelled:
                        break
                    if audio_chunk:
                        frame = b"\x02" + struct.pack("<I", turn_id) + audio_chunk
                        await ws.send_bytes(frame)
                        tts_chunk_count += 1
            except Exception as e:
                tts_errors += 1
                logger.warning("turn %d: TTS error for sentence %r: %s", turn_id, sentence, e)
                # Continue with next sentence instead of failing the whole turn

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

    # Pipeline timing summary
    t_end = time.time()
    total_ms = (t_end - t_llm_start) * 1000
    tts_ms = (t_end - t_tts_start) * 1000 if t_tts_start else 0
    logger.info(
        "turn %d: pipeline complete — total=%.0fms llm→tts=%.0fms tts=%.0fms chunks=%d tts_errors=%d",
        turn_id, total_ms, (t_tts_start - t_llm_start) * 1000 if t_tts_start else 0,
        tts_ms, tts_chunk_count, tts_errors,
    )

    # 3. Update conversation history & send done
    if full_response and not turn_mgr.is_cancelled:
        manager.add_history(seg.text, full_response)

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
    # Iterate over a snapshot — _devices may change during await
    for did, ws in list(manager._devices.items()):
        if did != source_device:
            try:
                await _send(ws, sync_msg)
            except Exception:
                pass  # Device might be disconnected


# ── Lifecycle hooks ───────────────────────────────────────────────
@app.on_event("shutdown")
async def shutdown():
    """Close all WebSocket connections on server shutdown."""
    logger.info("shutting down, closing %d connections...", manager.count)
    for did, ws in list(manager._devices.items()):
        try:
            await ws.close(code=1001, reason="server_shutdown")
        except Exception:
            pass
    logger.info("shutdown complete")


# ── Entry ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
