from agents.scene_memory import empty_scene_memory, scene_memory_prompt, update_scene_memory


def test_scene_memory_merges_structured_detections():
    memory = update_scene_memory(
        empty_scene_memory(),
        tool_name="check_success",
        data={
            "detections": {
                "black bowl": {"confidence": 0.82, "location_hint": "left side"},
                "basket": {"confidence": 0.91, "location_hint": "back right"},
            }
        },
        images=[],
    )

    labels = {obj["label"] for obj in memory["objects"]}
    assert labels == {"black bowl", "basket"}
    assert memory["requires_fresh_capture_before_motion"] is True

    prompt = scene_memory_prompt(memory)
    assert "black bowl" in prompt
    assert "không được điều khiển robot chỉ dựa trên memory cũ" in prompt
