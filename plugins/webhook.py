"""
Webhook Plugin — Trigger arbitrary HTTP APIs and webhooks.

Use cases:
  - IFTTT webhooks
  - Home automation platforms (not Home Assistant)
  - Custom APIs
  - Notification services (Bark, ServerChan, etc.)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TOOLS = [
    {
        "name": "webhook_trigger",
        "description": (
            "Trigger a webhook or call an HTTP API. "
            "Use for: IFTTT, Bark notifications, ServerChan, custom APIs. "
            "Webhook names are configured in webhooks.json."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Webhook name from webhooks.json, e.g. 'notify_phone', 'ifttt_morning'",
                },
                "data": {
                    "type": "object",
                    "description": "Additional data to send with the webhook",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "send_notification",
        "description": "Send a push notification to the user's phone. Uses Bark (iOS) or ServerChan (WeChat).",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Notification title",
                },
                "body": {
                    "type": "string",
                    "description": "Notification body text",
                },
            },
            "required": ["title", "body"],
        },
    },
]

# ── Webhook registry ─────────────────────────────────────────────

WEBHOOKS_FILE = os.path.join(os.path.dirname(__file__), "webhooks.json")


def _load_webhooks() -> dict[str, dict]:
    if not os.path.exists(WEBHOOKS_FILE):
        return {}
    try:
        with open(WEBHOOKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ── Notification backends ────────────────────────────────────────

async def _bark_notify(title: str, body: str) -> dict:
    """Send via Bark (iOS push notification)."""
    bark_url = os.environ.get("BARK_URL", "")
    if not bark_url:
        return {"error": "BARK_URL not set"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{bark_url}/{title}/{body}")
        return {"status": "ok", "platform": "bark"}


async def _serverchan_notify(title: str, body: str) -> dict:
    """Send via ServerChan (WeChat push notification)."""
    key = os.environ.get("SERVERCHAN_KEY", "")
    if not key:
        return {"error": "SERVERCHAN_KEY not set"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title, "desp": body},
        )
        return {"status": "ok", "platform": "serverchan"}


# ── Tool execution ───────────────────────────────────────────────

async def execute(name: str, args: dict[str, Any]) -> str:
    try:
        if name == "webhook_trigger":
            return await _trigger_webhook(args)
        elif name == "send_notification":
            return await _send_notification(args)
        return json.dumps({"error": f"unknown tool: {name}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


async def _trigger_webhook(args: dict) -> str:
    webhook_name = args.get("name", "")
    data = args.get("data", {})

    webhooks = _load_webhooks()
    webhook = webhooks.get(webhook_name)
    if not webhook:
        return json.dumps({"error": f"webhook not found: {webhook_name}"})

    url = webhook.get("url", "")
    method = webhook.get("method", "POST").upper()
    headers = webhook.get("headers", {})
    body = {**webhook.get("body", {}), **data}

    async with httpx.AsyncClient(timeout=15.0) as client:
        if method == "GET":
            resp = await client.get(url, params=body, headers=headers)
        else:
            resp = await client.post(url, json=body, headers=headers)

        return json.dumps({
            "webhook": webhook_name,
            "status_code": resp.status_code,
            "response": resp.text[:500],
        })


async def _send_notification(args: dict) -> str:
    title = args.get("title", "Jarvis")
    body = args.get("body", "")

    # Try Bark first, then ServerChan
    result = await _bark_notify(title, body)
    if "error" not in result:
        return json.dumps(result)

    result = await _serverchan_notify(title, body)
    if "error" not in result:
        return json.dumps(result)

    return json.dumps({"error": "no notification service configured (set BARK_URL or SERVERCHAN_KEY)"})
