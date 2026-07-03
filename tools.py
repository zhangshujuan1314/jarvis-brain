"""
Tool definitions and execution for Jarvis Brain.
v1: weather query (read-only) to validate the pipeline.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Tool schema for Claude API (OpenAI-compatible tool format)
TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_weather",
        "description": "Get current weather for a city. Returns temperature and conditions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name, e.g. 'Beijing', 'Shanghai', 'New York'",
                },
            },
            "required": ["city"],
        },
    },
]


async def execute(name: str, args: dict[str, Any]) -> str:
    """Execute a tool by name, return result as string."""
    if name == "get_weather":
        return await _get_weather(args.get("city", ""))
    logger.warning("unknown tool: %s", name)
    return json.dumps({"error": f"unknown tool: {name}"})


async def _get_weather(city: str) -> str:
    """Fetch weather from wttr.in (free, no API key)."""
    if not city:
        return json.dumps({"error": "city is required"})

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://wttr.in/{city}?format=j1",
                headers={"Accept-Language": "zh"},
            )
            resp.raise_for_status()
            data = resp.json()

            current = data.get("current_condition", [{}])[0]
            temp_c = current.get("temp_C", "N/A")
            desc_list = current.get("lang_zh", current.get("weatherDesc", [{}]))
            desc = desc_list[0].get("value", "N/A") if desc_list else "N/A"
            humidity = current.get("humidity", "N/A")

            result = {
                "city": city,
                "temperature": f"{temp_c}°C",
                "description": desc,
                "humidity": f"{humidity}%",
            }
            logger.info("weather: %s -> %s", city, result)
            return json.dumps(result, ensure_ascii=False)

    except httpx.HTTPError as e:
        logger.error("weather API error: %s", e)
        return json.dumps({"error": f"failed to get weather for {city}"})
