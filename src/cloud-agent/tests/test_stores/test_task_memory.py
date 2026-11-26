from stores import reset_console_stores
from stores.sessions import session_store
from stores.task_memory import extract_task_update, render_task_memory, task_memory_store


def test_task_memory_records_completed_run():
    reset_console_stores()
    session = session_store.create("robot-001", user_id="user_dev", title="Morning")

    memory = task_memory_store.record_run(
        session["id"],
        run_id="run_1",
        user_message="Pick the black bowl",
        assistant_reply="Done.",
        status="COMPLETED",
    )

    assert memory["active_goal"] == "Pick the black bowl"
    assert memory["completed_tasks"][-1]["task"] == "Pick the black bowl"
    assert memory["last_run_id"] == "run_1"

    prompt = render_task_memory(task_memory_store.get(session["id"]))
    assert "Pick the black bowl" in prompt


def test_task_memory_extracts_structured_subgoals():
    update = extract_task_update(
        run_id="run_1",
        user_message="Pick and place the bowl",
        status="COMPLETED",
        agent_state={
            "tool_call_history": [
                {
                    "tool_name": "take_action",
                    "arguments": {"subgoal": "move near the black bowl", "k": 50},
                    "status": "SUCCEEDED",
                },
                {
                    "tool_name": "take_action",
                    "arguments": {"subgoal": "release the bowl into the basket", "k": 20},
                    "status": "SUCCEEDED",
                },
            ]
        },
    )

    assert update["subgoals"] == [
        "move near the black bowl",
        "release the bowl into the basket",
    ]
    assert update["completed_subgoals"][0]["subgoal"] == "move near the black bowl"
    assert update["remaining_tasks"] == []


def test_task_memory_extracts_failed_action_and_remaining_task():
    update = extract_task_update(
        run_id="run_2",
        user_message="Pick the black bowl",
        status="FAILED",
        agent_state={
            "error": {"code": "EDGE_EPISODE"},
            "tool_call_history": [
                {
                    "tool_name": "take_action",
                    "arguments": {"instruction": "grasp the black bowl", "k": 40},
                    "status": "FAILED",
                    "error_code": "EDGE_EPISODE",
                }
            ],
        },
    )

    assert update["last_failed_action"]["subgoal"] == "grasp the black bowl"
    assert update["last_failed_action"]["error_code"] == "EDGE_EPISODE"
    assert update["remaining_tasks"][-1]["task"] == "Pick the black bowl"


def test_task_memory_cancelled_run_is_interrupted_not_remaining():
    reset_console_stores()
    session = session_store.create("robot-001", user_id="user_dev", title="Morning")

    memory = task_memory_store.record_run(
        session["id"],
        run_id="run_cancel",
        user_message="Pick the chocolate pudding into the basket",
        assistant_reply="Đã huỷ.",
        status="CANCELLED",
        agent_state={
            "cancel_requested": True,
            "tool_call_history": [
                {
                    "tool_name": "take_action",
                    "arguments": {"subgoal": "put the chocolate pudding in the basket"},
                    "status": "FAILED",
                    "error_code": "EDGE_EPISODE",
                }
            ],
        },
    )

    assert memory["active_goal"] is None
    assert memory["remaining_tasks"] == []
    assert memory["failed_tasks"] == []
    assert memory["interrupted_tasks"][-1]["task"] == "Pick the chocolate pudding into the basket"

    prompt = render_task_memory(memory)
    assert "Interrupted/cancelled tasks" in prompt
    assert "KHONG tu tiep tuc" in prompt
