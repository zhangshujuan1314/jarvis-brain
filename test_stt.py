"""M1.2 E2E test: WS auth + wake_event + audio feed + STT pipeline."""
import asyncio
import json
import os
import random
import struct
import sys

try:
    import websockets
except ImportError:
    print("Install: pip install websockets")
    sys.exit(1)

TOKEN = os.environ.get("JARVIS_TOKEN", "dev-token-change-me")
URI = os.environ.get("JARVIS_URI", "ws://localhost:8000/ws")


def make_audio_frame(turn_id: int, pcm: bytes) -> bytes:
    """§5 binary frame: [0x01][turn_id LE uint32][PCM data]"""
    return b"\x01" + struct.pack("<I", turn_id) + pcm


async def test_protocol():
    print(f"Connecting to {URI}")
    async with websockets.connect(URI) as ws:
        # 1. Auth
        await ws.send(json.dumps({
            "type": "auth",
            "token": TOKEN,
            "device_id": "test-pc-stt",
            "platform": "pc",
        }))
        resp = json.loads(await ws.recv())
        assert resp["type"] == "auth_ok", f"auth failed: {resp}"
        print("[PASS] auth")

        # 2. Wake event
        turn_id = 1
        await ws.send(json.dumps({"type": "wake_event", "turn_id": turn_id}))
        resp = json.loads(await ws.recv())
        assert resp["type"] == "turn_accepted", f"wake rejected: {resp}"
        print("[PASS] wake_event -> turn_accepted")

        # 3. Send audio: 0.5s noise + 1.5s silence (triggers VAD at 0.8s silence)
        random.seed(42)
        noise_pcm = b"".join(
            random.randint(-2000, 2000).to_bytes(2, "little", signed=True)
            for _ in range(8000)
        )
        silence_pcm = b"\x00\x00" * 24000

        await ws.send(make_audio_frame(turn_id, noise_pcm))
        print("  sent 0.5s noise audio")

        await ws.send(make_audio_frame(turn_id, silence_pcm))
        print("  sent 1.5s silence")

        # 4. Collect results
        got_utterance_end = False
        got_stt_result = False
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=15.0)
                if isinstance(msg, bytes):
                    print(f"  <- binary {len(msg)} bytes")
                    continue
                data = json.loads(msg)
                print(f"  <- {data['type']}")
                if data["type"] == "utterance_end":
                    got_utterance_end = True
                    print("  [PASS] utterance_end")
                elif data["type"] == "stt_result":
                    text = data.get("text", "")
                    print(f"  [PASS] stt_result: {text!r}")
                    got_stt_result = True
                    break
                elif data["type"] == "error":
                    print(f"  [FAIL] error: {data}")
                    return False
        except asyncio.TimeoutError:
            pass

        if not got_stt_result:
            print("[FAIL] No stt_result received within 15s")
            return False

        print("\n[PASS] M1.2 E2E protocol test passed")
        return True


async def main():
    ok = await test_protocol()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
