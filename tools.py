"""
M4.3 Tool definitions and execution for Jarvis Brain.

Tools:
  - get_weather: Current weather for a city (wttr.in, free)
  - calculate: Math expression evaluation (safe, no API)
  - get_datetime: Current date/time (no API)
  - search_web: Simple web search (DuckDuckGo Instant Answer, free)

Design:
  - All tools are read-only (v1 safety)
  - Write tools (reminders, etc.) require voice confirmation (§10)
  - Each tool returns JSON string for LLM consumption
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Tool schemas for Claude API ──────────────────────────────────

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_weather",
        "description": "Get current weather for a city. Returns temperature, conditions, and humidity.",
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
    {
        "name": "calculate",
        "description": "Evaluate a math expression. Supports +, -, *, /, **, sqrt, sin, cos, tan, log, pi, e. Example: '2**10', 'sqrt(144)', 'sin(pi/2)'",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Math expression to evaluate",
                },
            },
            "required": ["expression"],
        },
    },
    {
        "name": "get_datetime",
        "description": "Get current date and time. Returns date, time, day of week, timezone.",
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone_offset": {
                    "type": "integer",
                    "description": "UTC offset in hours, e.g. 8 for China (UTC+8). Default: 8",
                },
            },
        },
    },
    {
        "name": "search_web",
        "description": "Search the web for information. Returns a brief answer. Use for factual questions, definitions, current events.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
            },
            "required": ["query"],
        },
    },
]


# ── Safe math evaluation ─────────────────────────────────────────

# Only allow safe math operations
_SAFE_MATH_NAMES = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "log2": math.log2,
    "exp": math.exp, "pow": pow,
    "pi": math.pi, "e": math.e,
    "ceil": math.ceil, "floor": math.floor,
}

_EXPR_RE = re.compile(r"^[0-9+\-*/().%\s,a-z_]+$")


# ── Tool execution ───────────────────────────────────────────────

async def execute(name: str, args: dict[str, Any]) -> str:
    """Execute a tool by name, return result as JSON string."""
    try:
        if name == "get_weather":
            return await _get_weather(args.get("city", ""))
        elif name == "calculate":
            return _calculate(args.get("expression", ""))
        elif name == "get_datetime":
            return _get_datetime(args.get("timezone_offset", 8))
        elif name == "search_web":
            return await _search_web(args.get("query", ""))
        else:
            logger.warning("unknown tool: %s", name)
            return json.dumps({"error": f"unknown tool: {name}"})
    except Exception as e:
        logger.error("tool %s error: %s", name, e)
        return json.dumps({"error": str(e)})


# ── Tool implementations ─────────────────────────────────────────

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


def _calculate(expression: str) -> str:
    """Safely evaluate a math expression."""
    if not expression:
        return json.dumps({"error": "expression is required"})

    # Security: only allow safe characters
    expr = expression.strip().lower()
    if not _EXPR_RE.match(expr):
        return json.dumps({"error": "invalid expression: contains disallowed characters"})

    # Security: no dunder access, no imports
    if "__" in expr or "import" in expr or "exec" in expr or "eval" in expr:
        return json.dumps({"error": "expression not allowed"})

    try:
        result = eval(expr, {"__builtins__": {}}, _SAFE_MATH_NAMES)
        logger.info("calc: %s = %s", expression, result)
        return json.dumps({"expression": expression, "result": result}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"calculation error: {e}"})


def _get_datetime(tz_offset: int = 8) -> str:
    """Get current date/time for a timezone."""
    tz = timezone(timedelta(hours=tz_offset))
    now = datetime.now(tz)

    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

    result = {
        "date": now.strftime("%Y年%m月%d日"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": weekdays[now.weekday()],
        "timezone": f"UTC{tz_offset:+d}",
    }
    return json.dumps(result, ensure_ascii=False)


async def _search_web(query: str) -> str:
    """Search via DuckDuckGo Instant Answer API (free, no API key)."""
    if not query:
        return json.dumps({"error": "query is required"})

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                headers={"User-Agent": "JarvisBrain/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()

            # Try Abstract first, then Answer, then RelatedTopics
            abstract = data.get("AbstractText", "")
            answer = data.get("Answer", "")
            related = data.get("RelatedTopics", [])

            if answer:
                result = {"query": query, "answer": answer}
            elif abstract:
                result = {"query": query, "abstract": abstract, "source": data.get("AbstractSource", "")}
            elif related:
                # Get first 3 related topics
                topics = []
                for t in related[:3]:
                    if "Text" in t:
                        topics.append(t["Text"])
                result = {"query": query, "related": topics}
            else:
                result = {"query": query, "answer": "未找到相关信息"}

            logger.info("search: %s -> %s", query, result.get("answer", result.get("abstract", ""))[:100])
            return json.dumps(result, ensure_ascii=False)

    except httpx.HTTPError as e:
        logger.error("search API error: %s", e)
        return json.dumps({"error": f"search failed for: {query}"})
