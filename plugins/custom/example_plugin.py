"""
Example custom plugin — template for your own integrations.

Copy this file and modify to add your own tools.
"""
from __future__ import annotations

import json
from typing import Any

# Tool schemas (Claude API format)
TOOLS = [
    {
        "name": "my_custom_tool",
        "description": "Description of what this tool does.",
        "input_schema": {
            "type": "object",
            "properties": {
                "param1": {
                    "type": "string",
                    "description": "Description of param1",
                },
            },
            "required": ["param1"],
        },
    },
]


async def execute(name: str, args: dict[str, Any]) -> str:
    """Handle tool calls."""
    if name == "my_custom_tool":
        param1 = args.get("param1", "")
        # Your logic here
        return json.dumps({"result": f"processed: {param1}"})
    return json.dumps({"error": f"unknown tool: {name}"})
