"""Tool schema conversion — free providers reject loose OpenAI schemas."""

from agents.tools.registry import jsonschema_to_pydantic


def test_array_and_enum_without_anyof_null():
    model = jsonschema_to_pydantic(
        "CaptureToolArgs",
        {
            "type": "object",
            "properties": {
                "cameras": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["top", "wrist"],
                },
                "detail_level": {
                    "type": "string",
                    "enum": ["low", "high"],
                    "default": "low",
                },
            },
        },
    )
    schema = model.model_json_schema()
    cameras = schema["properties"]["cameras"]
    detail = schema["properties"]["detail_level"]
    # Không sinh anyOf: [T, null] — Nvidia free reject pattern này
    assert "anyOf" not in cameras
    assert "anyOf" not in detail
    assert cameras["type"] == "array"
    assert cameras["items"].get("type") == "string"
    assert detail.get("enum") == ["low", "high"] or "const" in detail


def test_empty_args_schema_is_object():
    model = jsonschema_to_pydantic("GetRobotStateArgs", {"type": "object", "properties": {}})
    schema = model.model_json_schema()
    assert schema.get("type", "object") in (None, "object") or schema.get("properties") == {}


def test_anyof_nullable_schema_uses_non_null_type():
    model = jsonschema_to_pydantic(
        "TakeActionArgs",
        {
            "type": "object",
            "properties": {
                "task_id": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "default": None,
                },
                "instruction": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                },
            },
        },
    )
    schema = model.model_json_schema()
    assert schema["properties"]["task_id"]["type"] == "integer"
    assert schema["properties"]["instruction"]["type"] == "string"
