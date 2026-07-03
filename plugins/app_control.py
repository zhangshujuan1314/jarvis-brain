"""
App Control Plugin — Launch and control apps on PC and Android.

PC: Uses OS commands to open apps and URLs.
Android: Uses ADB (Android Debug Bridge) to send intents.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import webbrowser
from typing import Any

logger = logging.getLogger(__name__)

TOOLS = [
    {
        "name": "open_app",
        "description": (
            "Open an application or URL on the current device. "
            "Examples: '微信', 'Chrome', 'https://bilibili.com', '计算器'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "App name or URL to open",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "send_intent",
        "description": (
            "Send an Android Intent to control apps on a connected Android device. "
            "Requires ADB connection. Examples: open WeChat, make a call, send SMS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Intent action, e.g. 'android.intent.action.VIEW'",
                },
                "package": {
                    "type": "string",
                    "description": "Target package name, e.g. 'com.tencent.mm' for WeChat",
                },
                "uri": {
                    "type": "string",
                    "description": "Intent URI, e.g. 'tel:10086', 'smsto:10086'",
                },
                "extras": {
                    "type": "object",
                    "description": "Intent extras as key-value pairs",
                },
            },
        },
    },
]

# ── Common app mappings ──────────────────────────────────────────

PC_APPS = {
    "微信": "WeChat" if sys.platform == "win32" else "WeChat",
    "wechat": "WeChat",
    "chrome": "chrome",
    "浏览器": "chrome",
    "记事本": "notepad" if sys.platform == "win32" else "TextEdit",
    "计算器": "calc" if sys.platform == "win32" else "Calculator",
    "终端": "cmd" if sys.platform == "win32" else "Terminal",
    "文件管理器": "explorer" if sys.platform == "win32" else "Finder",
    "spotify": "spotify",
    "网易云音乐": "cloudmusic",
    "qq音乐": "QQMusic",
    "vscode": "code",
    "idea": "idea",
}

ANDROID_PACKAGES = {
    "微信": "com.tencent.mm",
    "wechat": "com.tencent.mm",
    "qq": "com.tencent.mobileqq",
    "支付宝": "com.eg.android.AlipayGphone",
    "淘宝": "com.taobao.taobao",
    "抖音": "com.ss.android.ugc.aweme",
    "b站": "tv.danmaku.bili",
    "bilibili": "tv.danmaku.bili",
    "高德地图": "com.autonavi.minimap",
    "百度地图": "com.baidu.BaiduMap",
    "qq音乐": "com.tencent.qqmusic",
    "网易云音乐": "com.netease.cloudmusic",
    "电话": "com.android.dialer",
    "短信": "com.android.mms",
    "相机": "com.android.camera",
    "设置": "com.android.settings",
}


# ── Tool execution ───────────────────────────────────────────────

async def execute(name: str, args: dict[str, Any]) -> str:
    try:
        if name == "open_app":
            return _open_app(args)
        elif name == "send_intent":
            return _send_intent(args)
        return json.dumps({"error": f"unknown tool: {name}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _open_app(args: dict) -> str:
    app_name = args.get("name", "")

    # Check if it's a URL
    if app_name.startswith(("http://", "https://", "www.")):
        webbrowser.open(app_name)
        return json.dumps({"action": "opened_url", "url": app_name})

    # Look up in PC_APPS
    exe = PC_APPS.get(app_name.lower(), app_name)

    try:
        if sys.platform == "win32":
            subprocess.Popen(["start", exe], shell=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", exe])
        else:
            subprocess.Popen([exe])
        return json.dumps({"action": "opened_app", "app": app_name})
    except FileNotFoundError:
        return json.dumps({"error": f"app not found: {app_name}"})


def _send_intent(args: dict) -> str:
    action = args.get("action", "")
    package = args.get("package", "")
    uri = args.get("uri", "")
    extras = args.get("extras", {})

    # Resolve package name from friendly name
    if package in ANDROID_PACKAGES:
        package = ANDROID_PACKAGES[package]

    # Build ADB command
    cmd = ["adb", "shell", "am", "start"]

    if action:
        cmd.extend(["-a", action])
    if package:
        cmd.extend(["-n", f"{package}/.main"])
    if uri:
        cmd.extend(["-d", uri])

    for key, value in extras.items():
        cmd.extend(["--es", key, str(value)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return json.dumps({"action": "intent_sent", "command": " ".join(cmd)})
        else:
            return json.dumps({"error": f"ADB error: {result.stderr}"})
    except FileNotFoundError:
        return json.dumps({"error": "ADB not found. Install Android SDK platform-tools."})
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "ADB timeout — is the device connected?"})
