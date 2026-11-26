import pytest
import sys, os
sys.path.insert(0, os.path.abspath('src/cloud-agent'))

from langchain_core.messages import HumanMessage, ToolMessage
from agents.nodes.observe import observe_node
from agents.tools.mcp_client import ToolResult, ImageContent
from agents.tools.serialize import build_tool_message, build_image_message
from pydantic import BaseModel

class DummyContext:
    def __init__(self):
        self.emitter = self
        self.run_id = "test_run"
    def emit(self, *args, **kwargs):
        pass

@pytest.fixture
def dummy_run_context(monkeypatch):
    monkeypatch.setattr("cloud_agent.agents.nodes.observe.get_run_context", lambda _: DummyContext())

@pytest.mark.asyncio
async def test_observe_node_with_images(dummy_run_context):
    img = ImageContent(url="http://example.com/img.jpg", camera_id="cam1", captured_at="now", detail="low")
    result = ToolResult(ok=True, tool_name="capture", data={"foo": "bar"}, images=[img])
    call = {"name": "capture", "id": "call_123"}
    
    state = {"pending_tool_results": [(call, result)]}
    
    out = await observe_node(state, None)
    
    messages = out["messages"]
    assert len(messages) == 2
    assert isinstance(messages[0], ToolMessage)
    assert 'untrusted="true"' in messages[0].content
    assert "http://example.com/img.jpg" not in messages[0].content
    
    assert isinstance(messages[1], HumanMessage)
    assert len(messages[1].content) == 1
    assert messages[1].content[0]["type"] == "image_url"
    assert messages[1].content[0]["image_url"]["url"] == "http://example.com/img.jpg"

@pytest.mark.asyncio
async def test_observe_node_without_images(dummy_run_context):
    result = ToolResult(ok=True, tool_name="capture", data={"foo": "bar"}, images=[])
    call = {"name": "capture", "id": "call_123"}
    
    state = {"pending_tool_results": [(call, result)]}
    
    out = await observe_node(state, None)
    
    messages = out["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], ToolMessage)
