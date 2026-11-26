from types import SimpleNamespace

from agents.prompt_context import build_runtime_system_prompt


def test_runtime_prompt_orders_memory_by_scope():
    ctx = SimpleNamespace(
        base_system_prompt="BASE POLICY",
        user_id="user_1",
        user_tz="Asia/Ho_Chi_Minh",
        settings=SimpleNamespace(agent_context_token_budget=8_000),
    )
    state = {
        "long_term_memories": [
            {"memory_type": "preference", "content": "User prefers concise answers."}
        ],
        "task_memory": {
            "active_goal": "Pick the bowl",
            "completed_tasks": [],
            "failed_tasks": [],
            "completed_subgoals": [],
            "remaining_tasks": [],
            "last_failed_action": None,
            "recent_events": [],
        },
        "scene_memory": {
            "recent_observations": [
                {"summary": "Camera sees the bowl on the table.", "tool": "capture_tool"}
            ],
            "objects": {},
            "requires_fresh_capture_before_motion": True,
        },
    }

    prompt = build_runtime_system_prompt(ctx, state)

    base_idx = prompt.index("BASE POLICY")
    long_term_idx = prompt.index("## Long-term memory")
    task_idx = prompt.index("## Session task memory")
    scene_idx = prompt.index("## Layer 7 - System Context")
    assert base_idx < long_term_idx < task_idx < scene_idx
    assert "User prefers concise answers." in prompt
    assert "Pick the bowl" in prompt
