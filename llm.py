"""
M1.3 LLM engine: OpenAI-compatible API streaming + tool calling + sentence splitting.

Supports: mimo-v2.5, Claude, GPT, DeepSeek, or any OpenAI-compatible API.

Design:
- TRUE streaming: yield sentences as they arrive
- Tool calling: execute tools silently, only yield final answer
- Sentence splitter: split on 。！？.!?\n
- Force concise spoken style via system prompt
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import AsyncGenerator

import httpx

from tools import TOOLS, execute

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Jarvis, a voice assistant. Respond in spoken Chinese (Mandarin). "
    "Keep replies concise: 1-3 short sentences suitable for speech synthesis. "
    "No markdown, no bullet points, no special formatting — plain spoken language only. "
    "When asked about weather or similar info, use available tools."
)

# Sentence boundary regex (capturing group keeps delimiters)
_SENT_RE = re.compile(r"([。！？.!?\n]+)")

# Minimum characters per yielded chunk
_MIN_CHUNK = 4


class LLMEngine:
    """OpenAI-compatible API with streaming + tool calling."""

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        max_tokens: int = 512,
        extra_tools: list[dict] | None = None,
    ):
        self._api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self._api_base = api_base or os.environ.get("LLM_API_BASE", "https://api.xiaomimimo.com/v1")
        self._model = model or os.environ.get("LLM_MODEL", "mimo-v2.5")
        self._max_tokens = max_tokens

        if not self._api_key:
            logger.warning("LLM_API_KEY not set — LLM calls will fail")

        # Merge built-in tools with plugin tools
        self._tools = list(TOOLS) + list(extra_tools or [])
        # Tool executor — can be overridden by plugin manager
        self._executor = execute

        logger.info("LLM ready: model=%s, base=%s, tools=%d", self._model, self._api_base, len(self._tools))

    def set_executor(self, executor_fn):
        """Set a custom tool executor (e.g., plugin manager)."""
        self._executor = executor_fn

    async def stream(
        self,
        user_text: str,
        history: list[dict] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Stream LLM response, yielding complete sentences.
        """
        if not self._api_key:
            yield "抱歉，语言模型未配置。"
            return

        messages = list(history or [])
        messages.append({"role": "user", "content": user_text})

        try:
            for _round in range(5):  # Max 5 tool-calling rounds
                tool_calls = []
                text_buf = ""
                sentence_buf = ""

                # Build request
                payload = {
                    "model": self._model,
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
                    "max_tokens": self._max_tokens,
                    "stream": True,
                }
                if self._tools:
                    payload["tools"] = [{"type": "function", "function": t} for t in self._tools]
                    payload["tool_choice"] = "auto"

                headers = {
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                }

                # Stream the response
                current_tool_call = None

                async with httpx.AsyncClient(timeout=60.0) as client:
                    async with client.stream(
                        "POST",
                        f"{self._api_base}/chat/completions",
                        json=payload,
                        headers=headers,
                    ) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data.strip() == "[DONE]":
                                break

                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue

                            delta = chunk.get("choices", [{}])[0].get("delta", {})

                            # Handle text content
                            if "content" in delta and delta["content"]:
                                text_buf += delta["content"]
                                sentence_buf += delta["content"]
                                complete, sentence_buf = _drain_sentences(sentence_buf)
                                for s in complete:
                                    if len(s.strip()) >= _MIN_CHUNK:
                                        yield s.strip()

                            # Handle tool calls
                            if "tool_calls" in delta:
                                for tc in delta["tool_calls"]:
                                    idx = tc.get("index", 0)
                                    while len(tool_calls) <= idx:
                                        tool_calls.append({"id": "", "name": "", "arguments": ""})
                                    if "id" in tc:
                                        tool_calls[idx]["id"] = tc["id"]
                                    if "function" in tc:
                                        if "name" in tc["function"]:
                                            tool_calls[idx]["name"] = tc["function"]["name"]
                                        if "arguments" in tc["function"]:
                                            tool_calls[idx]["arguments"] += tc["function"]["arguments"]

                # After stream completes for this round
                if not tool_calls:
                    # No tools — flush remaining text and return
                    if sentence_buf.strip():
                        yield sentence_buf.strip()
                    elif not text_buf.strip():
                        yield "我不太确定该怎么回答。"
                    return

                # Tools were invoked — execute and loop
                assistant_content = ""
                if text_buf.strip():
                    assistant_content = text_buf

                # Parse tool arguments
                for tc in tool_calls:
                    try:
                        tc["arguments"] = json.loads(tc["arguments"])
                    except (json.JSONDecodeError, TypeError):
                        tc["arguments"] = {}

                # Build assistant message
                assistant_msg = {"role": "assistant", "content": assistant_content or None}
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls
                ]
                messages.append(assistant_msg)

                # Execute tools
                tool_results = []
                for tc in tool_calls:
                    logger.info("executing tool: %s(%s)", tc["name"], tc["arguments"])
                    result = await self._executor(tc["name"], tc["arguments"])
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })
                messages.extend(tool_results)

            # Exceeded max rounds
            yield "处理超时，请重试。"

        except Exception as e:
            logger.error("LLM error: %s", e)
            yield "抱歉，处理出错了。"


def _drain_sentences(buf: str) -> tuple[list[str], str]:
    """Split buffer on sentence boundaries. Returns (complete_sentences, leftover)."""
    parts = _SENT_RE.split(buf)
    sentences = []
    current = ""
    for part in parts:
        current += part
        if _SENT_RE.fullmatch(part):
            sentences.append(current)
            current = ""
    return sentences, current
