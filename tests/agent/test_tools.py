"""P0 tests for agent.tools.base — pure logic, no DB."""
from __future__ import annotations

from agent.tools.base import ApprovalPolicy, BaseTool, ToolResult


class TestToolResult:
    def test_success_when_no_error(self):
        result = ToolResult(output={"data": "test"})
        assert result.success is True

    def test_not_success_when_error(self):
        result = ToolResult(output=None, error="something broke")
        assert result.success is False

    def test_as_dict_success(self):
        result = ToolResult(output={"url": "https://example.com", "content": "hello"})
        assert result.as_dict() == {"output": {"url": "https://example.com", "content": "hello"}}

    def test_as_dict_error(self):
        result = ToolResult(output=None, error="connection timeout")
        assert result.as_dict() == {"error": "connection timeout"}

    def test_duration_ms_default(self):
        result = ToolResult(output="ok")
        assert result.duration_ms == 0

    def test_duration_ms_set(self):
        result = ToolResult(output="ok", duration_ms=1234)
        assert result.duration_ms == 1234


class TestApprovalPolicy:
    def test_auto_value(self):
        assert ApprovalPolicy.AUTO == "auto"

    def test_requires_approval_value(self):
        assert ApprovalPolicy.REQUIRES_APPROVAL == "requires_approval"


class TestToLLMSchema:
    def test_schema_format(self):
        """to_llm_schema returns valid OpenAI function calling format."""
        from agent.tools.web import WebReadTool

        tool = WebReadTool()
        schema = tool.to_llm_schema()

        assert schema["type"] == "function"
        assert "function" in schema
        assert schema["function"]["name"] == "web_read"
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]
        assert schema["function"]["parameters"]["type"] == "object"

    def test_schema_has_required_fields(self):
        """Schema parameters include 'required' list."""
        from agent.tools.web import WebReadTool

        tool = WebReadTool()
        schema = tool.to_llm_schema()
        params = schema["function"]["parameters"]
        assert "required" in params
        assert "url" in params["required"]

    def test_search_tool_schema(self):
        """web_search schema has query as required."""
        from agent.tools.search import WebSearchTool

        tool = WebSearchTool()
        schema = tool.to_llm_schema()
        assert schema["function"]["name"] == "web_search"
        assert "query" in schema["function"]["parameters"]["required"]

    def test_all_tools_have_valid_schemas(self):
        """Every registered tool produces a valid schema."""
        from agent.tools import all_tools

        for name, tool in all_tools().items():
            schema = tool.to_llm_schema()
            assert schema["type"] == "function", f"{name} missing type"
            assert schema["function"]["name"] == name, f"{name} name mismatch"
            assert "description" in schema["function"], f"{name} missing description"
            assert "parameters" in schema["function"], f"{name} missing parameters"
