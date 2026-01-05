#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tool Dispatcher Unit Tests
Tests for tool registry, dispatcher, and execution
"""

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import Mock, patch

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from tool_dispatcher import (
    ToolRegistry,
    ToolDispatcher,
    ToolSpec,
    ToolCategory,
    ToolPermission,
    ToolStatus,
    BaseTool,
    ToolCall,
    ShellTool,
    EchoTool
)
from core.security import CommandValidator
from core.exceptions import ToolError, SecurityError


# Mock tool for testing
class MockTool(BaseTool):
    """Mock tool for testing"""

    def __init__(self, name="mock_tool", category=ToolCategory.ANALYSIS):
        self._spec = ToolSpec(
            name=name,
            category=category,
            description="A mock tool for testing",
            permissions=[ToolPermission.READ],
            input_schema={
                "type": "object",
                "properties": {
                    "input": {"type": "string"}
                },
                "required": ["input"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "result": {"type": "string"}
                }
            },
            keywords=["mock", "test"],
            examples=[{"input": "test"}],
            priority=50
        )
        self.execute_result = "mock result"

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, **kwargs) -> Any:
        if "input" not in kwargs:
            raise ToolError("Missing required field: input")
        return {"result": self.execute_result}


class TestToolSpec:
    """Tool specification tests"""

    def test_tool_spec_creation(self):
        """Test creating a tool specification"""
        spec = ToolSpec(
            name="test_tool",
            category=ToolCategory.FILE,
            description="Test tool",
            permissions=[ToolPermission.READ, ToolPermission.WRITE],
            input_schema={"type": "object"},
            output_schema={"type": "object"}
        )

        assert spec.name == "test_tool"
        assert spec.category == ToolCategory.FILE
        assert len(spec.permissions) == 2
        assert spec.status == ToolStatus.AVAILABLE
        assert spec.priority == 50

    def test_tool_spec_to_dict(self):
        """Test converting tool spec to dictionary"""
        spec = ToolSpec(
            name="test_tool",
            category=ToolCategory.SEARCH,
            description="Test tool",
            permissions=[ToolPermission.READ],
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            keywords=["search", "find"]
        )

        data = spec.to_dict()

        assert data["name"] == "test_tool"
        assert data["category"] == "search"
        assert data["permissions"] == ["read"]
        assert data["keywords"] == ["search", "find"]


class TestToolCall:
    """Tool call record tests"""

    def test_tool_call_creation(self):
        """Test creating a tool call record"""
        call = ToolCall(
            call_id="abc123",
            tool_name="test_tool",
            arguments={"input": "test"},
            result={"output": "success"},
            success=True,
            duration_ms=100
        )

        assert call.call_id == "abc123"
        assert call.tool_name == "test_tool"
        assert call.success is True

    def test_tool_call_to_dict(self):
        """Test converting tool call to dictionary"""
        call = ToolCall(
            call_id="xyz",
            tool_name="tool",
            arguments={"arg": "value"},
            result="result",
            success=False,
            error="Test error"
        )

        data = call.to_dict()

        assert data["call_id"] == "xyz"
        assert data["arguments"] == {"arg": "value"}
        assert data["success"] is False
        assert data["error"] == "Test error"


class TestToolRegistry:
    """Tool registry tests"""

    def test_register_tool(self):
        """Test registering a tool"""
        registry = ToolRegistry()
        tool = MockTool()

        registry.register(tool)

        assert registry.get_tool("mock_tool") is tool
        assert registry.get_spec("mock_tool") is tool.spec

    def test_unregister_tool(self):
        """Test unregistering a tool"""
        registry = ToolRegistry()
        tool = MockTool()

        registry.register(tool)
        assert registry.get_tool("mock_tool") is not None

        registry.unregister("mock_tool")
        assert registry.get_tool("mock_tool") is None

    def test_list_tools_all(self):
        """Test listing all tools"""
        registry = ToolRegistry()
        registry.register(MockTool("tool1", ToolCategory.FILE))
        registry.register(MockTool("tool2", ToolCategory.SEARCH))
        registry.register(MockTool("tool3", ToolCategory.ANALYSIS))

        tools = registry.list_tools()

        assert len(tools) == 3

    def test_list_tools_by_category(self):
        """Test listing tools filtered by category"""
        registry = ToolRegistry()
        registry.register(MockTool("file_tool", ToolCategory.FILE))
        registry.register(MockTool("search_tool", ToolCategory.SEARCH))
        registry.register(MockTool("another_file", ToolCategory.FILE))

        file_tools = registry.list_tools(category=ToolCategory.FILE)

        assert len(file_tools) == 2
        assert all(t.category == ToolCategory.FILE for t in file_tools)

    def test_list_tools_by_permission(self):
        """Test listing tools filtered by permission"""
        # Create tools with different permissions
        read_tool = MockTool("read_tool", ToolCategory.FILE)
        read_tool._spec.permissions = [ToolPermission.READ]

        write_tool = MockTool("write_tool", ToolCategory.FILE)
        write_tool._spec.permissions = [ToolPermission.WRITE]

        registry = ToolRegistry()
        registry.register(read_tool)
        registry.register(write_tool)

        write_tools = registry.list_tools(permission=ToolPermission.WRITE)

        assert len(write_tools) == 1
        assert write_tools[0].name == "write_tool"

    def test_find_tools_by_keywords(self):
        """Test finding tools by keywords"""
        registry = ToolRegistry()
        registry.register(MockTool("file_reader", ToolCategory.FILE))
        registry.register(MockTool("search_engine", ToolCategory.SEARCH))
        registry.register(MockTool("data_analyzer", ToolCategory.ANALYSIS))

        # Find by name match
        results = registry.find_tools(["file"])
        assert len(results) >= 1
        assert "file_reader" in [r.name for r in results]

        # Find by description
        results = registry.find_tools(["mock"])
        assert len(results) == 3  # All have "mock" in description

    def test_find_tools_scoring(self):
        """Test that keyword matching scores correctly"""
        registry = ToolRegistry()
        tool = MockTool("test_tool", ToolCategory.ANALYSIS)
        registry.register(tool)

        # Name match should score higher than description match
        results = registry.find_tools(["test_tool"])
        assert len(results) == 1
        assert results[0].name == "test_tool"


class TestToolDispatcher:
    """Tool dispatcher tests"""

    def test_dispatcher_initialization(self, tmp_path):
        """Test dispatcher initializes correctly"""
        db_path = tmp_path / "test.db"
        dispatcher = ToolDispatcher(db_path=db_path)

        assert dispatcher.registry is not None
        assert dispatcher.db_path == db_path

    def test_call_tool_success(self, tmp_path):
        """Test successful tool call"""
        dispatcher = ToolDispatcher(db_path=tmp_path / "test.db")
        tool = MockTool()
        dispatcher.registry.register(tool)

        result = dispatcher.call("mock_tool", arguments={"input": "test"})

        assert result.success is True
        assert result.tool_name == "mock_tool"
        assert result.result == {"result": "mock result"}

    def test_call_tool_missing_required(self, tmp_path):
        """Test calling tool with missing required arguments"""
        dispatcher = ToolDispatcher(db_path=tmp_path / "test.db")
        tool = MockTool()
        dispatcher.registry.register(tool)

        result = dispatcher.call("mock_tool", arguments={})

        assert result.success is False
        assert "Missing required field" in result.error

    def test_call_unknown_tool(self, tmp_path):
        """Test calling unknown tool"""
        dispatcher = ToolDispatcher(db_path=tmp_path / "test.db")

        result = dispatcher.call("unknown_tool")

        assert result.success is False
        assert "not found" in result.error.lower() or "no such tool" in result.error.lower()

    def test_get_stats(self, tmp_path):
        """Test getting tool statistics"""
        dispatcher = ToolDispatcher(db_path=tmp_path / "test.db")
        tool = MockTool()
        dispatcher.registry.register(tool)

        # Make some calls
        dispatcher.call("mock_tool", arguments={"input": "test1"})
        dispatcher.call("mock_tool", arguments={"input": "test2"})

        stats = dispatcher.get_stats("mock_tool")

        assert stats["total_calls"] == 2
        assert stats["success_count"] == 2
        assert stats["failure_count"] == 0


class TestShellTool:
    """Shell tool tests"""

    def test_shell_tool_safe_command(self, tmp_path):
        """Test shell tool executes safe commands"""
        tool = ShellTool()

        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        # Execute safe command (skip on Python 3.6 due to capture_output)
        import sys
        if sys.version_info >= (3, 7):
            result = tool.execute(command=f"cat {test_file}")
            assert "hello" in result
        else:
            # Python 3.6 compatibility - just test validation passes
            # The actual execution uses capture_output which is 3.7+
            is_safe, _ = CommandValidator.validate(f"cat {test_file}")
            assert is_safe

    def test_shell_tool_blocked_command(self, tmp_path):
        """Test shell tool blocks dangerous commands"""
        tool = ShellTool()

        with pytest.raises((SecurityError, Exception)):
            tool.execute(command="rm -rf /")

    def test_shell_tool_injection_blocked(self, tmp_path):
        """Test shell tool blocks command injection"""
        tool = ShellTool()

        with pytest.raises((SecurityError, Exception)):
            tool.execute(command="ls; rm -rf /")


class TestEchoTool:
    """Echo tool tests"""

    def test_echo_tool(self):
        """Test echo tool returns input"""
        tool = EchoTool()

        result = tool.execute(message="test message")

        # EchoTool returns a dict with 'echoed' key
        assert result == {"echoed": "test message"}

    def test_echo_tool_spec(self):
        """Test echo tool has valid spec"""
        tool = EchoTool()

        spec = tool.spec

        assert spec.name == "echo"
        assert spec.category == ToolCategory.COMMUNICATION
        assert ToolPermission.READ in spec.permissions


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
