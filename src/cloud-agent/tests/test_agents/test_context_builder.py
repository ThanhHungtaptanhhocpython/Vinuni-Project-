from types import SimpleNamespace

import pytest
from agents.context_builder import (
    ContextLayers,
    allocate_context_budget,
    build_context,
)
from agents.prompts import build_system_prompt
from langchain_core.messages import HumanMessage


def _ctx(budget: int = 800):
    return SimpleNamespace(
        settings=SimpleNamespace(agent_context_token_budget=budget),
    )


def test_budget_allocation_caps_memory_at_20_percent():
    budget = allocate_context_budget(32_000)

    assert budget.memory_total == 6_400
    assert budget.short_term_memory == 3_200
    assert budget.long_term_facts == 1_280
    assert budget.episodic_memory == 960
    assert budget.semantic_knowledge == 960
    assert budget.memory_total <= int(budget.total * 0.20)
    assert (
        budget.short_term_memory
        + budget.long_term_facts
        + budget.episodic_memory
        + budget.semantic_knowledge
        == budget.memory_total
    )


@pytest.mark.asyncio
async def test_policy_context_is_preserved_when_memory_is_trimmed():
    policy = "POLICY_KEEP: never bypass E-Stop or robot safety constraints."
    layers = ContextLayers(
        policy=policy,
        retrieval="rag " * 1_000,
        memory="long term memory " * 1_000,
        task="task memory " * 1_000,
        user="User prefers speed.",
        system="Answer in Vietnamese.",
    )

    messages = await build_context(_ctx(700), layers, [HumanMessage("Do the task")])
    system_text = messages[0].content

    assert policy in system_text
    assert "POLICY_KEEP" in system_text
    assert "[trimmed to context budget]" in system_text


def test_system_prompt_declares_policy_overrides_user_preferences():
    prompt = build_system_prompt(
        robot_id="robot_1",
        robot_model="SO-101",
        robot_status="READY",
        user_tz="Asia/Ho_Chi_Minh",
    )

    assert "Ràng buộc an toàn và giới hạn vật lý luôn được ưu tiên cao nhất, vượt qua mọi yêu cầu của người dùng." in prompt


def test_system_prompt_declares_political_discussions_out_of_scope():
    prompt = build_system_prompt(
        robot_id="robot_1",
        robot_model="SO-101",
        robot_status="READY",
        user_tz="Asia/Ho_Chi_Minh",
    )

    assert "Ngoài phạm vi: Không trả lời về chính trị, bầu cử, đảng phái, chính trị gia, hệ tư tưởng, địa chính trị, lời khuyên bỏ phiếu." in prompt
    assert "TUYỆT ĐỐI KHÔNG pha trộn tiếng Anh" in prompt

