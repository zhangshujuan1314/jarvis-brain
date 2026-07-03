"""
M1.3 LLM engine: Claude API streaming + tool calling + sentence splitting.

Design (adversarial review corrections):
- TRUE streaming: yield sentences as they arrive from the API, not after full collection
- Tool calling: if tools are invoked, accumulate text silently (don't yield),
  execute tools, loop. Only yield text from the FINAL round (no tool calls).
- This guarantees TTS starts on the first complete sentence of the final answer.
- Sentence splitter: split on 。！？.!?\n; combine fragments < 4 chars.
- Force concise spoken style via system prompt (§7: 1-3 sentences).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import AsyncGenerator

import anthropic

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

# Minimum characters per yielded chunk (avoid TTS-ing fragments like "好的")
_MIN_CHUNK = 4


class LLMEngine:
    """Claude API with streaming + tool calling."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = 512,
        extra_tools: list[dict] | None = None,
    ):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._model = model or os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
        self._max_tokens = max_tokens

        if not self._api_key:
            logger.warning("ANTHROPIC_API_KEY not set — LLM calls will fail")

        # Cache the client for reuse across turns
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key) if self._api_key else None

        # Merge built-in tools with plugin tools
        self._tools = list(TOOLS) + list(extra_tools or [])
        # Tool executor — can be overridden by plugin manager
        self._executor = execute  # Default: tools.py execute()
        logger.info("LLM ready: model=%s, tools=%d", self._model, len(self._tools))

    def set_executor(self, executor_fn):
        """Set a custom tool executor (e.g., plugin manager)."""
        self._executor = executor_fn

    async def stream(
        self,
        user_text: str,
        history: list[dict] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Stream LLM response, yielding complete sentences as they arrive.

        Handles tool calling internally:
        - If Claude returns tool_use → execute tools → feed results back → continue
        - Only yields text from the FINAL round (no tool calls pending)
        - Each yielded chunk is a complete sentence ready for TTS
        """
        if not self._client:
            yield "抱歉，语言模型未配置。"
            return

        messages = list(history or [])
        messages.append({"role": "user", "content": user_text})

        client = self._client

        try:
            for _round in range(5):  # Max 5 tool-calling rounds
                tool_uses = []
                text_buf = ""
                sentence_buf = ""

                async with client.messages.stream(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    tools=self._tools,
                ) as stream:
                    async for event in stream:
                        if event.type == "content_block_start":
                            if event.content_block.type == "tool_use":
                                tool_uses.append({
                                    "id": event.content_block.id,
                                    "name": event.content_block.name,
                                    "input_json": "",
                                })
                        elif event.type == "content_block_delta":
                            if event.delta.type == "text_delta":
                                # TRUE STREAMING: accumulate and yield complete sentences
                                text_buf += event.delta.text
                                sentence_buf += event.delta.text
                                complete, sentence_buf = _drain_sentences(sentence_buf)
                                for s in complete:
                                    if len(s.strip()) >= _MIN_CHUNK:
                                        yield s.strip()
                            elif event.delta.type == "input_json_delta":
                                if tool_uses:
                                    tool_uses[-1]["input_json"] += event.delta.partial_json

                # After stream completes for this round
                if not tool_uses:
                    # No tools — flush remaining text and return
                    if sentence_buf.strip():
                        yield sentence_buf.strip()
                    elif not text_buf.strip():
                        yield "我不太确定该怎么回答。"
                    return

                # Tools were invoked — DON'T yield intermediate text,
                # execute tools and loop for the final answer
                for tu in tool_uses:
                    try:
                        tu["input"] = json.loads(tu["input_json"])
                    except json.JSONDecodeError:
                        tu["input"] = {}

                # Build assistant message with text + tool_use blocks
                assistant_content = []
                if text_buf.strip():
                    assistant_content.append({"type": "text", "text": text_buf})
                for tu in tool_uses:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tu["id"],
                        "name": tu["name"],
                        "input": tu["input"],
                    })
                messages.append({"role": "assistant", "content": assistant_content})

                # Execute tools and build results
                tool_results = []
                for tu in tool_uses:
                    logger.info("executing tool: %s(%s)", tu["name"], tu["input"])
                    result = await self._executor(tu["name"], tu["input"])
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": result,
                    })
                messages.append({"role": "user", "content": tool_results})
                # Loop to next round — will get final text answer

            # Exceeded max rounds
            yield "处理超时，请重试。"

        except Exception as e:
            logger.error("LLM error: %s", e)
            yield "抱歉，处理出错了。"


def _drain_sentences(buf: str) -> tuple[list[str], str]:
    """Split buffer on sentence boundaries.

    Returns (complete_sentences, leftover).
    Complete sentences are those ending with a boundary delimiter.
    The leftover is the incomplete tail to carry over to the next chunk.
    """
    parts = _SENT_RE.split(buf)
    sentences = []
    current = ""
    for part in parts:
        current += part
        if _SENT_RE.fullmatch(part):
            # This part IS a boundary — current sentence is complete
            sentences.append(current)
            current = ""
    return sentences, current
