"""Context layering and token trimming for the cloud agent."""

from __future__ import annotations

import copy
from dataclasses import dataclass

from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage

from .context import RunContext
from .errors import AppError
from .tools.serialize import IMAGE_PLACEHOLDER

_IMAGE_TOKENS = {"low": 85, "high": 1100}
_CHARS_PER_TOKEN = 4

MEMORY_BUDGET_RATIO = 0.20
SHORT_TERM_MEMORY_RATIO = 0.10
LONG_TERM_FACTS_RATIO = 0.04
EPISODIC_MEMORY_RATIO = 0.03
SEMANTIC_KNOWLEDGE_RATIO = 0.03


@dataclass(frozen=True)
class ContextBudget:
    total: int
    memory_total: int
    short_term_memory: int
    long_term_facts: int
    episodic_memory: int
    semantic_knowledge: int
    non_memory: int


@dataclass(frozen=True)
class ContextLayers:
    """Seven context layers ordered by policy priority."""

    policy: str
    tool: str = ""
    retrieval: str = ""
    memory: str = ""
    user: str = ""
    task: str = ""
    system: str = ""

    def render(self, budget: ContextBudget | None = None, *, trim_memory: bool = True) -> str:
        retrieval = self.retrieval
        memory = self.memory
        task = self.task
        if budget and trim_memory:
            retrieval = _truncate_text_to_tokens(retrieval, budget.semantic_knowledge)
            memory = _truncate_text_to_tokens(memory, budget.long_term_facts + budget.episodic_memory)
            task = _truncate_text_to_tokens(task, budget.short_term_memory)
        parts = [
            ("Layer 1 - Policy Context", self.policy),
            ("Layer 2 - Tool Context", self.tool),
            ("Layer 3 - Retrieval Context", retrieval),
            ("Layer 4 - Memory Context", memory),
            ("Layer 5 - User Context", self.user),
            ("Layer 6 - Task Context", task),
            ("Layer 7 - System Context", self.system),
        ]
        return "\n\n".join(
            f"## {title}\n{content.strip()}" for title, content in parts if content and content.strip()
        )


def allocate_context_budget(total_tokens: int) -> ContextBudget:
    """Allocate hard memory caps; total memory is never above 20%."""
    memory_total = int(total_tokens * MEMORY_BUDGET_RATIO)
    short_term = int(total_tokens * SHORT_TERM_MEMORY_RATIO)
    long_term = int(total_tokens * LONG_TERM_FACTS_RATIO)
    episodic = int(total_tokens * EPISODIC_MEMORY_RATIO)
    semantic = int(total_tokens * SEMANTIC_KNOWLEDGE_RATIO)
    return ContextBudget(
        total=total_tokens,
        memory_total=memory_total,
        short_term_memory=short_term,
        long_term_facts=long_term,
        episodic_memory=episodic,
        semantic_knowledge=semantic,
        non_memory=total_tokens - memory_total,
    )


def count_tokens(messages: list[BaseMessage]) -> int:
    total = 0
    for msg in messages:
        if isinstance(msg.content, str):
            total += len(msg.content) // _CHARS_PER_TOKEN
            continue
        for part in msg.content:
            if not isinstance(part, dict):
                total += len(str(part)) // _CHARS_PER_TOKEN
            elif part.get("type") == "image_url":
                detail = part.get("image_url", {}).get("detail", "low")
                total += _IMAGE_TOKENS.get(detail, _IMAGE_TOKENS["low"])
            else:
                total += len(str(part.get("text", ""))) // _CHARS_PER_TOKEN
    return total


def _estimate_text_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN if text else 0


def _truncate_text_to_tokens(text: str, token_budget: int) -> str:
    if not text or token_budget <= 0 or _estimate_text_tokens(text) <= token_budget:
        return text
    max_chars = max(0, token_budget * _CHARS_PER_TOKEN)
    if max_chars <= 0:
        return ""
    return text[:max_chars].rstrip() + "\n[trimmed to context budget]"


def strip_all_images(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Replace every image_url block with a text placeholder."""
    result = list(messages)
    for i, msg in enumerate(result):
        if not isinstance(msg.content, list):
            continue
        has_image = any(isinstance(p, dict) and p.get("type") == "image_url" for p in msg.content)
        if not has_image:
            continue
        new_msg = copy.deepcopy(msg)
        new_msg.content = [
            p
            if not (isinstance(p, dict) and p.get("type") == "image_url")
            else {
                "type": "text",
                "text": IMAGE_PLACEHOLDER.format(
                    camera=p.get("image_url", {}).get("camera", "?"),
                    captured_at=p.get("image_url", {}).get("captured_at", "?"),
                ),
            }
            for p in new_msg.content
        ]
        result[i] = new_msg
    return result


def model_supports_vision(model: str) -> bool:
    """Heuristic for text-only models that reject image blocks."""
    m = model.lower()
    if any(x in m for x in ("gpt-oss", "coder", "code", "llama-3.3", "ling-")):
        return False
    vision_markers = (
        "gpt-4o",
        "gpt-4.1",
        "claude",
        "gemini",
        "vision",
        "-vl",
        "vl-",
        "vl:",
        "omni",
        "gemma-4",
        "gemma-3",
        "gemma4",
        "gemma3",
        "pixtral",
        "qwen2.5-vl",
        "qwen-vl",
        "llava",
    )
    return any(marker in m for marker in vision_markers)


def strip_old_images(messages: list[BaseMessage], keep_last_n_captures: int = 2) -> list[BaseMessage]:
    """Keep images from the newest N capture tool messages; replace older images."""
    result = list(messages)
    captures_seen = 0
    for i in range(len(result) - 1, -1, -1):
        msg = result[i]
        if not isinstance(msg, ToolMessage) or not isinstance(msg.content, list):
            continue
        has_image = any(isinstance(p, dict) and p.get("type") == "image_url" for p in msg.content)
        if not has_image:
            continue
        captures_seen += 1
        if captures_seen <= keep_last_n_captures:
            continue
        new_msg = copy.deepcopy(msg)
        new_msg.content = [
            p
            if not (isinstance(p, dict) and p.get("type") == "image_url")
            else {
                "type": "text",
                "text": IMAGE_PLACEHOLDER.format(
                    camera=p.get("image_url", {}).get("camera", "?"),
                    captured_at=p.get("image_url", {}).get("captured_at", "?"),
                ),
            }
            for p in new_msg.content
        ]
        result[i] = new_msg
    return result


def _render_as_text(messages: list[BaseMessage]) -> str:
    lines = []
    for msg in messages:
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        lines.append(f"[{msg.type}] {text}")
    return "\n".join(lines)


def _split_at_token_budget(messages: list[BaseMessage], keep_tail_tokens: int) -> tuple[list[BaseMessage], list[BaseMessage]]:
    tail: list[BaseMessage] = []
    total = 0
    for msg in reversed(messages):
        total += count_tokens([msg])
        if total > keep_tail_tokens and tail:
            break
        tail.insert(0, msg)
    head = messages[: len(messages) - len(tail)]
    return head, tail


async def build_context(ctx: RunContext, system_prompt: str | ContextLayers, history: list[BaseMessage]) -> list[BaseMessage]:
    """Build LLM messages using seven layers and bottom-up trimming."""
    budget = ctx.settings.agent_context_token_budget
    allocation = allocate_context_budget(budget)
    msgs = [m for m in history if not isinstance(m, SystemMessage)]
    rendered_system_prompt = system_prompt.render(allocation) if isinstance(system_prompt, ContextLayers) else system_prompt

    msgs = strip_old_images(msgs, keep_last_n_captures=2)
    system = SystemMessage(rendered_system_prompt)
    total = count_tokens([system]) + count_tokens(msgs)

    if total > budget:
        head, tail = _split_at_token_budget(msgs, keep_tail_tokens=allocation.short_term_memory)
        if head:
            from .llm import summarize_messages

            summary = await summarize_messages(ctx, _render_as_text(head))
            msgs = [SystemMessage(f"Summary of older short-term conversation:\n{summary}")] + tail

    if count_tokens([system]) + count_tokens(msgs) > budget:
        msgs = strip_all_images(msgs)

    if count_tokens([system]) + count_tokens(msgs) > budget:
        raise AppError("AGENT_010", detail={"budget": budget})

    return [system] + msgs
