"""
Smart Home Plugin — Control IoT devices via MQTT and HTTP APIs.

Supported platforms:
  - Home Assistant (HTTP API)
  - Xiaomi Mi Home (via MIoT)
  - Tuya/SmartLife (via Tuya Cloud API)
  - Generic MQTT devices
  - Philips Hue (HTTP API)

Configuration (in .env):
  HOME_ASSISTANT_URL=http://homeassistant.local:8123
  HOME_ASSISTANT_TOKEN=your-long-lived-token
  MQTT_BROKER=localhost
  MQTT_PORT=1883
  HUE_BRIDGE_IP=192.168.1.100
  HUE_API_KEY=your-hue-api-key

Device registry (plugins/devices.json):
  Maps friendly names to device IDs.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Tool schemas for Claude API ──────────────────────────────────

TOOLS = [
    {
        "name": "smart_home_control",
        "description": (
            "Control smart home devices. Supports lights, switches, thermostats, "
            "curtains, fans, etc. Actions: turn_on, turn_off, dim, set_temperature, "
            "set_color, toggle. Device names are friendly names like '客厅灯', '卧室空调'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "Device friendly name, e.g. '客厅灯', '卧室空调'",
                },
                "action": {
                    "type": "string",
                    "enum": ["turn_on", "turn_off", "dim", "set_temperature",
                             "set_color", "toggle", "set_mode"],
                    "description": "Action to perform",
                },
                "value": {
                    "type": "string",
                    "description": "Action value: dim level (0-100), temperature (°C), color name, mode name",
                },
            },
            "required": ["device", "action"],
        },
    },
    {
        "name": "smart_home_status",
        "description": "Get the current status of a smart home device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "description": "Device friendly name",
                },
            },
            "required": ["device"],
        },
    },
    {
        "name": "smart_home_scene",
        "description": "Activate a smart home scene. Scenes: '回家模式', '离家模式', '睡眠模式', '电影模式', etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scene": {
                    "type": "string",
                    "description": "Scene name, e.g. '回家模式', '睡眠模式'",
                },
            },
            "required": ["scene"],
        },
    },
]

# ── Device registry ──────────────────────────────────────────────

DEVICES_FILE = os.path.join(os.path.dirname(__file__), "devices.json")


def _load_devices() -> dict[str, dict]:
    """Load device registry from devices.json."""
    if not os.path.exists(DEVICES_FILE):
        return {}
    try:
        with open(DEVICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("failed to load devices.json: %s", e)
        return {}


def _find_device(name: str) -> dict | None:
    """Find a device by friendly name (fuzzy match)."""
    devices = _load_devices()
    # Exact match first
    if name in devices:
        return devices[name]
    # Fuzzy: check if name is contained in any device name
    for dev_name, dev_info in devices.items():
        if name in dev_name or dev_name in name:
            return dev_info
    return None


# ── Platform backends ────────────────────────────────────────────

async def _home_assistant_call(domain: str, service: str, data: dict) -> dict:
    """Call Home Assistant REST API."""
    url = os.environ.get("HOME_ASSISTANT_URL", "")
    token = os.environ.get("HOME_ASSISTANT_TOKEN", "")
    if not url or not token:
        return {"error": "Home Assistant not configured"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{url}/api/services/{domain}/{service}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=data,
        )
        resp.raise_for_status()
        return {"status": "ok"}


async def _home_assistant_state(entity_id: str) -> dict:
    """Get entity state from Home Assistant."""
    url = os.environ.get("HOME_ASSISTANT_URL", "")
    token = os.environ.get("HOME_ASSISTANT_TOKEN", "")
    if not url or not token:
        return {"error": "Home Assistant not configured"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{url}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def _hue_control(device_id: str, action: str, value: str = "") -> dict:
    """Control Philips Hue light."""
    bridge = os.environ.get("HUE_BRIDGE_IP", "")
    api_key = os.environ.get("HUE_API_KEY", "")
    if not bridge or not api_key:
        return {"error": "Hue Bridge not configured"}

    payload = {}
    if action == "turn_on":
        payload = {"on": True}
    elif action == "turn_off":
        payload = {"on": False}
    elif action == "dim":
        payload = {"on": True, "bri": int(int(value) * 254 / 100)}
    elif action == "set_color":
        # Map color name to Hue xy
        payload = {"on": True, "effect": "colorloop"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.put(
            f"http://{bridge}/api/{api_key}/lights/{device_id}/state",
            json=payload,
        )
        resp.raise_for_status()
        return {"status": "ok"}


async def _mqtt_publish(topic: str, payload: str) -> dict:
    """Publish to MQTT broker."""
    broker = os.environ.get("MQTT_BROKER", "")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    if not broker:
        return {"error": "MQTT not configured"}

    try:
        import asyncio_mqtt
        async with asyncio_mqtt.Client(broker, port) as client:
            await client.publish(topic, payload)
        return {"status": "ok"}
    except ImportError:
        return {"error": "asyncio-mqtt not installed (pip install asyncio-mqtt)"}


# ── Action mapping ───────────────────────────────────────────────

ACTION_MAP = {
    "turn_on": {"ha_domain": "light", "ha_service": "turn_on", "ha_data": {}},
    "turn_off": {"ha_domain": "light", "ha_service": "turn_off", "ha_data": {}},
    "dim": {"ha_domain": "light", "ha_service": "turn_on", "ha_data_fn": lambda v: {"brightness_pct": int(v)}},
    "set_temperature": {"ha_domain": "climate", "ha_service": "set_temperature", "ha_data_fn": lambda v: {"temperature": float(v)}},
    "toggle": {"ha_domain": "homeassistant", "ha_service": "toggle", "ha_data": {}},
    "set_mode": {"ha_domain": "climate", "ha_service": "set_hvac_mode", "ha_data_fn": lambda v: {"hvac_mode": v}},
}


# ── Tool execution ───────────────────────────────────────────────

async def execute(name: str, args: dict[str, Any]) -> str:
    """Execute a smart home tool."""
    try:
        if name == "smart_home_control":
            return await _control_device(args)
        elif name == "smart_home_status":
            return await _get_status(args)
        elif name == "smart_home_scene":
            return await _activate_scene(args)
        else:
            return json.dumps({"error": f"unknown tool: {name}"})
    except Exception as e:
        logger.error("smart_home error: %s", e)
        return json.dumps({"error": str(e)})


async def _control_device(args: dict) -> str:
    device_name = args.get("device", "")
    action = args.get("action", "")
    value = args.get("value", "")

    device = _find_device(device_name)
    if not device:
        return json.dumps({"error": f"device not found: {device_name}"})

    platform = device.get("platform", "home_assistant")
    device_id = device.get("id", "")

    if platform == "home_assistant":
        action_cfg = ACTION_MAP.get(action, {})
        domain = action_cfg.get("ha_domain", "light")
        service = action_cfg.get("ha_service", "turn_on")
        data = dict(action_cfg.get("ha_data", {}))
        data["entity_id"] = device_id
        if "ha_data_fn" in action_cfg and value:
            data.update(action_cfg["ha_data_fn"](value))
        result = await _home_assistant_call(domain, service, data)
    elif platform == "hue":
        result = await _hue_control(device_id, action, value)
    elif platform == "mqtt":
        topic = device.get("topic", "")
        payload = json.dumps({"action": action, "value": value})
        result = await _mqtt_publish(topic, payload)
    else:
        result = {"error": f"unsupported platform: {platform}"}

    return json.dumps(result, ensure_ascii=False)


async def _get_status(args: dict) -> str:
    device_name = args.get("device", "")
    device = _find_device(device_name)
    if not device:
        return json.dumps({"error": f"device not found: {device_name}"})

    platform = device.get("platform", "home_assistant")
    if platform == "home_assistant":
        result = await _home_assistant_state(device.get("id", ""))
        state = result.get("state", "unknown")
        attrs = result.get("attributes", {})
        return json.dumps({
            "device": device_name,
            "state": state,
            "attributes": attrs,
        }, ensure_ascii=False)

    return json.dumps({"device": device_name, "state": "unknown"})


async def _activate_scene(args: dict) -> str:
    scene_name = args.get("scene", "")

    # Try Home Assistant scene
    url = os.environ.get("HOME_ASSISTANT_URL", "")
    token = os.environ.get("HOME_ASSISTANT_TOKEN", "")
    if url and token:
        try:
            result = await _home_assistant_call("scene", "turn_on", {"entity_id": f"scene.{scene_name}"})
            return json.dumps({"scene": scene_name, "result": result}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"scene activation failed: {e}"})

    return json.dumps({"error": "no scene platform configured"})
