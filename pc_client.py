"""
M1.5 + M4.1 PC Client — Keyboard-triggered voice interaction with Jarvis Brain.

Features:
  - M1.5: State machine (IDLE → RECORDING → WAITING → PLAYING)
  - M4.1: Exponential backoff reconnection + session sync on reconnect
  - M4.2: Energy-based silence filtering (skip empty audio)

Flow:
  1. Connect to brain via WebSocket, authenticate
  2. Press Enter to start recording
  3. Audio streams to brain in real-time (silence filtered out)
  4. Press Enter again to stop (or 15s auto-stop)
  5. Receive STT result, LLM response, TTS audio
  6. Play TTS audio through speakers
  7. Repeat from step 2

Usage:
  python pc_client.py                    # localhost
  python pc_client.py wss://your-vps/ws  # remote
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import struct
import sys
import threading
import time

try:
    import websockets
except ImportError:
    print("Install: pip install websockets")
    sys.exit(1)

try:
    import sounddevice as sd
except ImportError:
    print("Install: pip install sounddevice")
    sys.exit(1)

import numpy as np

# Audio config (§5): 16kHz, 16-bit, mono, 100ms chunks
SAMPLE_RATE = 16000
CHUNK_MS = 100
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000  # 1600 samples
CHANNELS = 1
MAX_RECORD_S = 15

# M4.2: Energy threshold for silence detection (RMS of float32 samples)
# Typical speech: 0.01–0.1; silence: <0.005
ENERGY_THRESHOLD = 0.005

TOKEN = os.environ.get("JARVIS_TOKEN", "dev-token-change-me")
URI = os.environ.get("JARVIS_URI", "ws://localhost:8000/ws")

# M4.1: Reconnection config
RECONNECT_INITIAL_S = 1.0    # Initial backoff
RECONNECT_MAX_S = 30.0       # Max backoff
RECONNECT_FACTOR = 2.0       # Exponential factor

# State machine
IDLE = "idle"
RECORDING = "recording"
WAITING = "waiting"
PLAYING = "playing"

# ANSI colors
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_CYAN = "\033[96m"
C_RED = "\033[91m"
C_DIM = "\033[2m"
C_RESET = "\033[0m"


class PCClient:
    def __init__(self, uri: str, use_wake_word: bool = False):
        self.uri = uri
        self.use_wake_word = use_wake_word
        self.state = IDLE
        self.turn_id = 0
        self.ws = None
        self._audio_buf = bytearray()
        self._play_thread = None
        self._silence_count = 0  # M4.2: consecutive silence chunks
        self._wake_detector = None

    async def run(self):
        """M4.1: Main loop with exponential backoff reconnection."""
        backoff = RECONNECT_INITIAL_S

        while True:
            try:
                await self._connect()
                # If _connect returns normally, user exited
                return
            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.InvalidStatusCode,
                ConnectionRefusedError,
                OSError,
            ) as e:
                self.state = IDLE
                self._audio_buf.clear()
                if self._wake_detector:
                    self._wake_detector.stop()
                    self._wake_detector = None
                print(f"\n{C_RED}连接断开: {e}{C_RESET}")
                print(f"{C_YELLOW}⏱ {backoff:.0f}s 后重连...{C_RESET}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * RECONNECT_FACTOR, RECONNECT_MAX_S)
            except (KeyboardInterrupt, EOFError):
                print(f"\n{C_DIM}Bye!{C_RESET}")
                return

    async def _connect(self):
        """Single connection attempt. Raises on disconnect."""
        print(f"{C_CYAN}Connecting to {self.uri}...{C_RESET}")
        async with websockets.connect(self.uri) as ws:
            self.ws = ws

            # Authenticate
            await ws.send(json.dumps({
                "type": "auth",
                "token": TOKEN,
                "device_id": "pc-client-01",
                "platform": "pc",
            }))
            resp = json.loads(await ws.recv())
            if resp["type"] == "auth_fail":
                print(f"{C_RED}✗ Auth failed: {resp}{C_RESET}")
                raise ConnectionRefusedError("auth failed")

            print(f"{C_GREEN}✓ Connected & authenticated{C_RESET}")
            backoff = RECONNECT_INITIAL_S  # Reset on successful connect

            # Start wake word detector if enabled
            if self.use_wake_word:
                self._init_wake_word(ws)

            # Start receiver task
            recv_task = asyncio.create_task(self._receiver(ws))

            # Main input loop
            try:
                await self._input_loop(ws)
            except (KeyboardInterrupt, EOFError):
                print(f"\n{C_DIM}Bye!{C_RESET}")
                recv_task.cancel()
                return
            finally:
                recv_task.cancel()

    def _init_wake_word(self, ws):
        """Initialize Porcupine wake word detector."""
        try:
            from wake_word import WakeWordDetector
            loop = asyncio.get_running_loop()

            def on_wake():
                if self.state == IDLE:
                    asyncio.run_coroutine_threadsafe(self._start_recording(ws), loop)

            self._wake_detector = WakeWordDetector(on_wake=on_wake)
            if self._wake_detector.start():
                print(f"{C_GREEN}✓ 唤醒词已启用 — 说「贾维斯」激活{C_RESET}")
            else:
                print(f"{C_YELLOW}⚠ 唤醒词未配置 — 使用 Enter 键触发{C_RESET}")
                self._wake_detector = None
        except ImportError:
            print(f"{C_YELLOW}⚠ pvporcupine 未安装 — 使用 Enter 键触发{C_RESET}")
        except Exception as e:
            print(f"{C_YELLOW}⚠ 唤醒词初始化失败: {e}{C_RESET}")

    async def _input_loop(self, ws):
        wake_hint = "说「贾维斯」或" if self._wake_detector else ""
        while True:
            if self.state == IDLE:
                print(f"\n{C_YELLOW}{wake_hint}[Enter] 开始录音 | Ctrl+C 退出{C_RESET}")
                await asyncio.get_running_loop().run_in_executor(None, input)
                if self.state == IDLE:  # Might have changed via wake word
                    await self._start_recording(ws)
            elif self.state == RECORDING:
                print(f"{C_YELLOW}[Enter] 停止录音{C_RESET}")
                await asyncio.get_running_loop().run_in_executor(None, input)
                await self._stop_recording(ws)
            elif self.state in (WAITING, PLAYING):
                print(f"{C_YELLOW}[Enter] 取消当前回复{C_RESET}")
                await asyncio.get_running_loop().run_in_executor(None, input)
                await self._cancel_turn(ws)

    async def _start_recording(self, ws):
        self.turn_id += 1
        self.state = RECORDING
        self._silence_count = 0
        print(f"{C_GREEN}● 录音中... (再说按 Enter 停止，最长 {MAX_RECORD_S}s){C_RESET}")

        # Send wake event
        await ws.send(json.dumps({"type": "wake_event", "turn_id": self.turn_id}))

        # Start audio capture in background
        asyncio.ensure_future(self._capture_audio(ws))

    async def _capture_audio(self, ws):
        """Capture microphone audio and stream to server. M4.2: filter silence."""
        loop = asyncio.get_running_loop()
        start = time.time()
        sent_chunks = 0

        def audio_callback(indata, frames, time_info, status):
            nonlocal sent_chunks
            if self.state != RECORDING:
                return

            # M4.2: Energy-based silence detection
            rms = np.sqrt(np.mean(indata ** 2))
            if rms < ENERGY_THRESHOLD:
                self._silence_count += 1
                return  # Skip silence
            self._silence_count = 0

            pcm = (indata * 32767).astype(np.int16).tobytes()
            frame = b"\x01" + struct.pack("<I", self.turn_id) + pcm
            asyncio.run_coroutine_threadsafe(ws.send(frame), loop)
            sent_chunks += 1

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=CHUNK_SAMPLES,
                callback=audio_callback,
            ):
                while self.state == RECORDING:
                    await asyncio.sleep(0.1)
                    if time.time() - start >= MAX_RECORD_S:
                        print(f"{C_YELLOW}⏱ 录音达到 {MAX_RECORD_S}s 上限{C_RESET}")
                        await self._stop_recording(ws)
                        return

        except Exception as e:
            print(f"{C_RED}录音错误: {e}{C_RESET}")
            self.state = IDLE

    async def _stop_recording(self, ws):
        if self.state != RECORDING:
            return
        self.state = WAITING
        print(f"{C_CYAN}⏳ 等待回复...{C_RESET}")
        await ws.send(json.dumps({"type": "audio_done", "turn_id": self.turn_id}))

    async def _cancel_turn(self, ws):
        """Cancel the current turn (during WAITING or PLAYING)."""
        print(f"{C_YELLOW}⊘ 已取消{C_RESET}")
        await ws.send(json.dumps({"type": "cancel", "turn_id": self.turn_id}))
        self.state = IDLE
        self._audio_buf.clear()

    async def _receiver(self, ws):
        """Receive and handle server messages."""
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    self._handle_binary(raw)
                    continue

                msg = json.loads(raw)
                mtype = msg.get("type", "")
                turn = msg.get("turn_id", 0)

                # Ignore messages for stale turns
                if turn and turn != self.turn_id:
                    continue

                if mtype == "turn_accepted":
                    pass  # Expected after wake_event
                elif mtype == "turn_rejected":
                    print(f"{C_RED}✗ 请求被拒绝: {msg.get('reason')}{C_RESET}")
                    self.state = IDLE
                elif mtype == "utterance_end":
                    if self.state == RECORDING:
                        await self._stop_recording(ws)
                elif mtype == "stt_result":
                    print(f"{C_DIM}识别: {msg.get('text', '')}{C_RESET}")
                elif mtype == "state":
                    val = msg.get("value", "")
                    if val == "thinking":
                        print(f"{C_CYAN}🧠 思考中...{C_RESET}")
                    elif val == "speaking":
                        print(f"{C_GREEN}🔊 播放中...{C_RESET}")
                        self.state = PLAYING
                    elif val == "cancelled":
                        self.state = IDLE
                elif mtype == "tts_done":
                    # Flush any remaining buffered audio
                    self._flush_audio()
                    # Wait for playback to finish
                    if self._play_thread and self._play_thread.is_alive():
                        self._play_thread.join()
                    self.state = IDLE
                    print(f"{C_DIM}✓ 回复完毕{C_RESET}")
                elif mtype == "error":
                    print(f"{C_RED}✗ 错误 [{msg.get('stage')}]: {msg.get('message')}{C_RESET}")
                    self.state = IDLE
                elif mtype == "session_sync":
                    # M4.1: Show synced context from other device or reconnect
                    user = msg.get("user_text", "")
                    assistant = msg.get("assistant_text", "")
                    if user:
                        print(f"{C_DIM}[sync] 用户: {user}{C_RESET}")
                    if assistant:
                        print(f"{C_DIM}[sync] 助手: {assistant}{C_RESET}")

        except websockets.exceptions.ConnectionClosed:
            print(f"{C_RED}连接断开{C_RESET}")

    def _handle_binary(self, data: bytes):
        """Handle binary audio frame: [0x02][turn_id:4 LE][PCM data]"""
        if len(data) < 6:  # 1 channel + 4 turn_id + at least 1 byte PCM
            return
        channel = data[0]
        if channel != 0x02:
            return  # Not TTS audio

        turn_id = struct.unpack("<I", data[1:5])[0]
        if turn_id != self.turn_id:
            return

        pcm = data[5:]
        self._audio_buf.extend(pcm)

        # Start playback if not already playing and we have enough buffer (~0.3s)
        if self.state == PLAYING and len(self._audio_buf) > int(SAMPLE_RATE * 0.3 * 2):  # 16-bit = 2 bytes/sample
            self._flush_audio()

    def _flush_audio(self):
        """Play buffered audio."""
        if not self._audio_buf:
            return
        pcm = bytes(self._audio_buf)
        self._audio_buf.clear()
        self._play_thread = threading.Thread(target=self._play_pcm, args=(pcm,))
        self._play_thread.start()

    def _play_pcm(self, pcm: bytes):
        """Play PCM audio through speakers."""
        try:
            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            sd.play(samples, SAMPLE_RATE)
            sd.wait()
        except Exception as e:
            print(f"{C_RED}播放错误: {e}{C_RESET}")


async def main():
    args = sys.argv[1:]
    use_wake = "--wake" in args
    uri = [a for a in args if not a.startswith("--")]
    uri = uri[0] if uri else URI
    client = PCClient(uri, use_wake_word=use_wake)
    await client.run()


if __name__ == "__main__":
    asyncio.run(main())
