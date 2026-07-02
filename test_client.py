"""M1.1 verification client — connect, auth, echo loop."""
import asyncio
import json
import os
import sys

# Use websockets library for the client side
try:
    import websockets
except ImportError:
    print("Install: pip install websockets")
    sys.exit(1)

TOKEN = os.environ.get("JARVIS_TOKEN", "dev-token-change-me")
URI = os.environ.get("JARVIS_URI", "ws://localhost:8000/ws")


async def test():
    print(f"Connecting to {URI} ...")
    async with websockets.connect(URI) as ws:
        # 1. Auth
        print("1. Sending auth frame...")
        await ws.send(json.dumps({
            "type": "auth",
            "token": TOKEN,
            "device_id": "test-pc-01",
            "platform": "pc",
        }))
        resp = json.loads(await ws.recv())
        print(f"   <- {resp}")
        assert resp["type"] == "auth_ok", f"Expected auth_ok, got {resp}"
        print("   [PASS] auth_ok")

        # 2. Text echo
        print("2. Sending text frame...")
        await ws.send(json.dumps({"type": "test", "msg": "hello jarvis"}))
        echo = json.loads(await ws.recv())
        print(f"   <- {echo}")
        assert echo["type"] == "echo", f"Expected echo, got {echo}"
        assert echo["original"]["msg"] == "hello jarvis"
        print("   [PASS] text echo")

        # 3. Binary echo
        print("3. Sending binary frame...")
        payload = b"\x01\x2a\x00\x00\x00" + b"test-pcm-data"
        await ws.send(payload)
        binary_back = await ws.recv()
        print(f"   <- {len(binary_back)} bytes")
        assert isinstance(binary_back, bytes)
        assert payload in binary_back or binary_back == payload
        print("   [PASS] binary echo")

        # 4. Ping/pong
        print("4. Sending ping...")
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await ws.recv())
        print(f"   <- {pong}")
        assert pong["type"] == "pong"
        print("   [PASS] pong")

    print("\n[PASS] All M1.1 tests passed.")


async def test_auth_fail():
    """Verify that bad token gets rejected."""
    print(f"\n--- Testing auth_fail ---")
    async with websockets.connect(URI) as ws:
        await ws.send(json.dumps({
            "type": "auth",
            "token": "wrong-token",
            "device_id": "bad-01",
            "platform": "pc",
        }))
        resp = json.loads(await ws.recv())
        print(f"   <- {resp}")
        assert resp["type"] == "auth_fail"
        print("   [PASS] bad token rejected (expected)")


async def main():
    await test_auth_fail()
    await test()


if __name__ == "__main__":
    asyncio.run(main())
