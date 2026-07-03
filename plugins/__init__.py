"""
Jarvis Plugin System — Device & App Control

Plugins are tools that the LLM can invoke to control external devices and apps.
Each plugin is a Python module that:
  1. Defines TOOLS (Claude API tool schema)
  2. Implements execute(name, args) → result JSON

Built-in plugins:
  - smart_home: MQTT/HTTP smart home devices
  - android: Android app control via ADB/Intent
  - pc_control: Windows/Mac/Linux app control
  - media: Media playback control
  - webhook: Generic HTTP webhook triggers

User plugins:
  Place .py files in plugins/custom/ and they're auto-loaded.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).parent
CUSTOM_DIR = PLUGIN_DIR / "custom"


class PluginManager:
    """Manages plugin discovery and tool routing."""

    def __init__(self):
        self._plugins: dict[str, Any] = {}
        self._tools: list[dict[str, Any]] = []
        self._tool_to_plugin: dict[str, str] = {}

    def load_all(self):
        """Discover and load all plugins."""
        # Load built-in plugins
        for py_file in PLUGIN_DIR.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            self._load_plugin(py_file.stem)

        # Load custom plugins
        if CUSTOM_DIR.exists():
            CUSTOM_DIR.mkdir(exist_ok=True)
            for py_file in CUSTOM_DIR.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                self._load_plugin(py_file.stem, custom=True)

        logger.info("loaded %d plugins, %d tools", len(self._plugins), len(self._tools))

    def _load_plugin(self, name: str, custom: bool = False):
        """Load a single plugin module."""
        try:
            if custom:
                module = importlib.import_module(f"plugins.custom.{name}")
            else:
                module = importlib.import_module(f"plugins.{name}")

            # Plugin must export TOOLS and execute()
            if not hasattr(module, "TOOLS") or not hasattr(module, "execute"):
                logger.warning("plugin %s: missing TOOLS or execute()", name)
                return

            self._plugins[name] = module
            for tool in module.TOOLS:
                tool_name = tool["name"]
                self._tools.append(tool)
                self._tool_to_plugin[tool_name] = name
                logger.info("  registered tool: %s (from %s)", tool_name, name)

        except Exception as e:
            logger.error("failed to load plugin %s: %s", name, e)

    @property
    def tools(self) -> list[dict[str, Any]]:
        """All registered tool schemas (for Claude API)."""
        return list(self._tools)

    async def execute(self, tool_name: str, args: dict[str, Any]) -> str:
        """Route tool call to the appropriate plugin."""
        plugin_name = self._tool_to_plugin.get(tool_name)
        if not plugin_name:
            return json.dumps({"error": f"unknown tool: {tool_name}"})

        plugin = self._plugins[plugin_name]
        try:
            return await plugin.execute(tool_name, args)
        except Exception as e:
            logger.error("plugin %s execute error: %s", plugin_name, e)
            return json.dumps({"error": str(e)})

    def list_plugins(self) -> list[dict[str, Any]]:
        """List all loaded plugins and their tools."""
        result = []
        for name, module in self._plugins.items():
            tools = [t["name"] for t in module.TOOLS]
            result.append({"name": name, "tools": tools})
        return result
