"""
Media Plugin — Control media playback on connected devices.

Supports:
  - System volume control (Windows/Mac/Linux)
  - Media playback (play/pause/next/prev)
  - Spotify (via Spotify Connect API)
  - YouTube (via URL opening)
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TOOLS = [
    {
        "name": "media_control",
        "description": (
            "Control media playback. Actions: play, pause, next, previous, "
            "volume_up, volume_down, volume_set, mute, unmute. "
            "Works with system media player and Spotify."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "pause", "next", "previous", "volume_up",
                             "volume_down", "volume_set", "mute", "unmute"],
                },
                "value": {
                    "type": "string",
                    "description": "For volume_set: 0-100. For play: search query or URL.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "media_search",
        "description": "Search and play music/video. Returns search results or starts playback.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query, e.g. '周杰伦 稻香', 'lofi hip hop'",
                },
                "platform": {
                    "type": "string",
                    "enum": ["spotify", "youtube", "system"],
                    "description": "Platform to search on (default: auto-detect)",
                },
            },
            "required": ["query"],
        },
    },
]


# ── Platform detection ───────────────────────────────────────────

def _is_windows() -> bool:
    return sys.platform == "win32"

def _is_mac() -> bool:
    return sys.platform == "darwin"

def _is_linux() -> bool:
    return sys.platform.startswith("linux")


# ── System volume control ────────────────────────────────────────

def _get_volume() -> int:
    """Get current system volume (0-100)."""
    try:
        if _is_windows():
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            return int(volume.GetMasterVolumeLevelScalar() * 100)
        elif _is_mac():
            result = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"],
                                  capture_output=True, text=True)
            return int(result.stdout.strip())
    except Exception:
        pass
    return 50  # Default


def _set_volume(level: int):
    """Set system volume (0-100)."""
    level = max(0, min(100, level))
    try:
        if _is_windows():
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = interface.QueryInterface(IAudioEndpointVolume)
            volume.SetMasterVolumeLevelScalar(level / 100.0, None)
        elif _is_mac():
            subprocess.run(["osascript", "-e", f"set volume output volume {level}"])
        elif _is_linux():
            subprocess.run(["amixer", "set", "Master", f"{level}%"])
    except Exception as e:
        logger.error("volume control error: %s", e)


# ── Media key simulation ─────────────────────────────────────────

def _send_media_key(key: str):
    """Send media key press (play/pause/next/prev)."""
    try:
        if _is_windows():
            import ctypes
            VK_MEDIA_PLAY_PAUSE = 0xB3
            VK_MEDIA_NEXT_TRACK = 0xB0
            VK_MEDIA_PREV_TRACK = 0xB1
            key_map = {
                "play": VK_MEDIA_PLAY_PAUSE,
                "pause": VK_MEDIA_PLAY_PAUSE,
                "next": VK_MEDIA_NEXT_TRACK,
                "previous": VK_MEDIA_PREV_TRACK,
            }
            vk = key_map.get(key, VK_MEDIA_PLAY_PAUSE)
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        elif _is_mac():
            key_map = {
                "play": "play",
                "pause": "pause",
                "next": "next track",
                "previous": "previous track",
            }
            mac_key = key_map.get(key, "play")
            subprocess.run(["osascript", "-e", f'tell application "System Events" to {mac_key}'])
    except Exception as e:
        logger.error("media key error: %s", e)


# ── Spotify integration ──────────────────────────────────────────

async def _spotify_search(query: str) -> dict:
    """Search Spotify (requires SPOTIFY_TOKEN)."""
    token = os.environ.get("SPOTIFY_TOKEN", "")
    if not token:
        return {"error": "SPOTIFY_TOKEN not set"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            "https://api.spotify.com/v1/search",
            params={"q": query, "type": "track", "limit": 3},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        data = resp.json()

        tracks = []
        for item in data.get("tracks", {}).get("items", []):
            tracks.append({
                "name": item["name"],
                "artist": ", ".join(a["name"] for a in item["artists"]),
                "uri": item["uri"],
            })
        return {"tracks": tracks}


# ── Tool execution ───────────────────────────────────────────────

async def execute(name: str, args: dict[str, Any]) -> str:
    try:
        if name == "media_control":
            return await _control_media(args)
        elif name == "media_search":
            return await _search_media(args)
        return json.dumps({"error": f"unknown tool: {name}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def _control_media(args: dict) -> str:
    action = args.get("action", "")
    value = args.get("value", "")

    if action in ("play", "pause", "next", "previous"):
        _send_media_key(action)
        return json.dumps({"action": action, "status": "ok"})

    elif action == "volume_up":
        current = _get_volume()
        _set_volume(current + 10)
        return json.dumps({"action": "volume_up", "volume": _get_volume()})

    elif action == "volume_down":
        current = _get_volume()
        _set_volume(current - 10)
        return json.dumps({"action": "volume_down", "volume": _get_volume()})

    elif action == "volume_set":
        _set_volume(int(value))
        return json.dumps({"action": "volume_set", "volume": int(value)})

    elif action == "mute":
        _set_volume(0)
        return json.dumps({"action": "mute"})

    elif action == "unmute":
        _set_volume(50)
        return json.dumps({"action": "unmute", "volume": 50})

    return json.dumps({"error": f"unknown action: {action}"})


async def _search_media(args: dict) -> str:
    query = args.get("query", "")
    platform = args.get("platform", "auto")

    if platform == "spotify" or platform == "auto":
        result = await _spotify_search(query)
        if "error" not in result:
            return json.dumps(result, ensure_ascii=False)

    # Fallback: open YouTube search in browser
    import webbrowser
    url = f"https://www.youtube.com/results?search_query={query}"
    webbrowser.open(url)
    return json.dumps({"action": "opened_browser", "url": url, "query": query})
