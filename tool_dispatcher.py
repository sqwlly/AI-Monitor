#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Tool Dispatcher
工具调度器 - 注册、选择和调用工具

功能：
1. 工具注册（能力描述/输入输出规范/权限要求）
2. 工具选择（根据需求选择/组合编排/备选准备）
3. 工具调用（参数准备/调用执行/结果处理）
4. 工具监控（调用统计/错误统计/性能监控）

Usage:
    python3 tool_dispatcher.py list
    python3 tool_dispatcher.py call <tool_name> [--args <json>]
    python3 tool_dispatcher.py recommend <task_description>
    python3 tool_dispatcher.py stats [--tool <tool_name>]
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from compat_dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

# Database path
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


class ToolCategory(Enum):
    """Tool category"""
    FILE = "file"                  # File operations
    SEARCH = "search"              # Search operations
    ANALYSIS = "analysis"          # Code/text analysis
    EXECUTION = "execution"        # Command execution
    COMMUNICATION = "communication"  # Notifications, messaging
    MEMORY = "memory"              # Memory/storage operations


class ToolPermission(Enum):
    """Required permission level"""
    READ = "read"                  # Read-only access
    WRITE = "write"                # Write access required
    EXECUTE = "execute"            # Can execute commands
    ADMIN = "admin"                # Administrative operations


class ToolStatus(Enum):
    """Tool availability status"""
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


@dataclass
class ToolSpec:
    """Tool specification"""
    name: str
    category: ToolCategory
    description: str
    permissions: List[ToolPermission]
    input_schema: Dict[str, Any]       # JSON Schema for input
    output_schema: Dict[str, Any]      # JSON Schema for output
    keywords: List[str] = field(default_factory=list)
    examples: List[Dict[str, Any]] = field(default_factory=list)
    status: ToolStatus = ToolStatus.AVAILABLE
    priority: int = 50                 # Higher = preferred when multiple match

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "category": self.category.value,
            "description": self.description,
            "permissions": [p.value for p in self.permissions],
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "keywords": self.keywords,
            "examples": self.examples,
            "status": self.status.value,
            "priority": self.priority,
        }


@dataclass
class ToolCall:
    """Record of a tool call"""
    call_id: str = ""
    tool_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    success: bool = True
    error: str = ""
    duration_ms: int = 0
    created_at: int = 0

    def to_dict(self) -> Dict:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": self.result,
            "success": self.success,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at,
        }


class BaseTool(ABC):
    """Base class for tools"""

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """Return tool specification"""
        pass

    @abstractmethod
    def execute(self, **kwargs) -> Any:
        """Execute the tool"""
        pass

    def validate_input(self, **kwargs) -> Tuple[bool, str]:
        """Validate input arguments"""
        # Basic validation - subclasses can override
        required = self.spec.input_schema.get("required", [])
        for field in required:
            if field not in kwargs:
                return False, f"Missing required field: {field}"
        return True, ""


class ToolRegistry:
    """Tool registry and dispatcher"""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._specs: Dict[str, ToolSpec] = {}

    def register(self, tool: BaseTool):
        """Register a tool"""
        spec = tool.spec
        self._tools[spec.name] = tool
        self._specs[spec.name] = spec

    def unregister(self, name: str):
        """Unregister a tool"""
        if name in self._tools:
            del self._tools[name]
            del self._specs[name]

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name"""
        return self._tools.get(name)

    def get_spec(self, name: str) -> Optional[ToolSpec]:
        """Get a tool spec by name"""
        return self._specs.get(name)

    def list_tools(
        self,
        category: Optional[ToolCategory] = None,
        permission: Optional[ToolPermission] = None
    ) -> List[ToolSpec]:
        """List available tools"""
        specs = list(self._specs.values())

        if category:
            specs = [s for s in specs if s.category == category]

        if permission:
            specs = [s for s in specs if permission in s.permissions]

        return sorted(specs, key=lambda s: s.priority, reverse=True)

    def find_tools(self, keywords: List[str]) -> List[ToolSpec]:
        """Find tools matching keywords"""
        matches = []
        keywords_lower = [k.lower() for k in keywords]

        for spec in self._specs.values():
            score = 0
            for kw in keywords_lower:
                if kw in spec.name.lower():
                    score += 3
                if kw in spec.description.lower():
                    score += 2
                if any(kw in tool_kw.lower() for tool_kw in spec.keywords):
                    score += 1

            if score > 0:
                matches.append((spec, score))

        matches.sort(key=lambda x: x[1], reverse=True)
        return [m[0] for m in matches]


class ToolDispatcher:
    """Tool dispatch and execution engine"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.registry = ToolRegistry()
        self._ensure_db()
        self._register_builtin_tools()

    def _ensure_db(self):
        """Ensure database exists with proper schema"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tool_calls (
                    call_id TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    arguments TEXT,
                    result TEXT,
                    success INTEGER DEFAULT 1,
                    error TEXT,
                    duration_ms INTEGER,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tool_stats (
                    tool_name TEXT PRIMARY KEY,
                    total_calls INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    total_duration_ms INTEGER DEFAULT 0,
                    last_called INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_calls_tool
                    ON tool_calls(tool_name);
                CREATE INDEX IF NOT EXISTS idx_calls_time
                    ON tool_calls(created_at);
            """)

    @contextmanager
    def _get_conn(self):
        """Get database connection"""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _register_builtin_tools(self):
        """Register built-in tools"""
        # Import and register file tool
        try:
            from tools.file_tool import FileTool
            self.registry.register(FileTool())
        except ImportError:
            pass

        # Import and register search tool
        try:
            from tools.search_tool import SearchTool
            self.registry.register(SearchTool())
        except ImportError:
            pass

        # Register shell tool
        self.registry.register(ShellTool())

        # Register echo tool (for testing)
        self.registry.register(EchoTool())

    # ==================== Tool Calling ====================

    def call(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout: int = 30
    ) -> ToolCall:
        """Call a tool by name"""
        arguments = arguments or {}
        call_id = str(uuid.uuid4())[:8]
        start_time = time.time()

        tool = self.registry.get_tool(tool_name)
        if not tool:
            return ToolCall(
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
                success=False,
                error=f"Tool '{tool_name}' not found",
                created_at=int(time.time()),
            )

        # Validate input
        is_valid, error = tool.validate_input(**arguments)
        if not is_valid:
            return ToolCall(
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
                success=False,
                error=f"Invalid input: {error}",
                created_at=int(time.time()),
            )

        # Execute tool
        try:
            result = tool.execute(**arguments)
            duration_ms = int((time.time() - start_time) * 1000)

            call = ToolCall(
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
                result=result,
                success=True,
                duration_ms=duration_ms,
                created_at=int(time.time()),
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            call = ToolCall(
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
                success=False,
                error=str(e),
                duration_ms=duration_ms,
                created_at=int(time.time()),
            )

        # Record call
        self._record_call(call)

        return call

    def _record_call(self, call: ToolCall):
        """Record tool call to database"""
        with self._get_conn() as conn:
            # Record individual call
            conn.execute("""
                INSERT INTO tool_calls (
                    call_id, tool_name, arguments, result,
                    success, error, duration_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                call.call_id,
                call.tool_name,
                json.dumps(call.arguments),
                json.dumps(call.result) if call.result else None,
                1 if call.success else 0,
                call.error,
                call.duration_ms,
                call.created_at,
            ))

            # Update stats
            conn.execute("""
                INSERT INTO tool_stats (tool_name, total_calls, success_count,
                                        failure_count, total_duration_ms, last_called)
                VALUES (?, 1, ?, ?, ?, ?)
                ON CONFLICT(tool_name) DO UPDATE SET
                    total_calls = total_calls + 1,
                    success_count = success_count + ?,
                    failure_count = failure_count + ?,
                    total_duration_ms = total_duration_ms + ?,
                    last_called = ?
            """, (
                call.tool_name,
                1 if call.success else 0,
                0 if call.success else 1,
                call.duration_ms,
                call.created_at,
                1 if call.success else 0,
                0 if call.success else 1,
                call.duration_ms,
                call.created_at,
            ))

    # ==================== Tool Selection ====================

    def recommend(
        self,
        task_description: str,
        max_tools: int = 3
    ) -> List[Dict[str, Any]]:
        """Recommend tools for a task"""
        # Extract keywords from task
        words = task_description.lower().split()
        keywords = [w for w in words if len(w) > 3]

        # Find matching tools
        matches = self.registry.find_tools(keywords)

        # Add category-based recommendations
        category_keywords = {
            ToolCategory.FILE: ["file", "read", "write", "create", "delete", "open"],
            ToolCategory.SEARCH: ["search", "find", "grep", "locate", "pattern"],
            ToolCategory.EXECUTION: ["run", "execute", "shell", "command", "script"],
            ToolCategory.ANALYSIS: ["analyze", "parse", "check", "validate"],
        }

        for category, cat_keywords in category_keywords.items():
            if any(kw in task_description.lower() for kw in cat_keywords):
                cat_tools = self.registry.list_tools(category=category)
                for tool in cat_tools:
                    if tool not in matches:
                        matches.append(tool)

        recommendations = []
        for spec in matches[:max_tools]:
            recommendations.append({
                "name": spec.name,
                "description": spec.description,
                "category": spec.category.value,
                "permissions": [p.value for p in spec.permissions],
                "relevance": "high" if spec in matches[:1] else "medium",
            })

        return recommendations

    def compose(
        self,
        tools: List[str],
        arguments_list: List[Dict[str, Any]]
    ) -> List[ToolCall]:
        """Execute multiple tools in sequence"""
        results = []
        context = {}  # Shared context between tools

        for i, (tool_name, arguments) in enumerate(zip(tools, arguments_list)):
            # Inject context
            args = {**arguments, "_context": context}

            call = self.call(tool_name, args)
            results.append(call)

            # Update context with result
            if call.success and call.result:
                context[f"step_{i}_result"] = call.result

            # Stop on failure unless configured otherwise
            if not call.success:
                break

        return results

    # ==================== Statistics ====================

    def get_stats(
        self,
        tool_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get tool usage statistics"""
        with self._get_conn() as conn:
            if tool_name:
                row = conn.execute("""
                    SELECT * FROM tool_stats WHERE tool_name = ?
                """, (tool_name,)).fetchone()

                if not row:
                    return {"error": "No stats for tool"}

                return {
                    "tool_name": row["tool_name"],
                    "total_calls": row["total_calls"],
                    "success_count": row["success_count"],
                    "failure_count": row["failure_count"],
                    "success_rate": round(row["success_count"] / max(1, row["total_calls"]), 3),
                    "avg_duration_ms": round(row["total_duration_ms"] / max(1, row["total_calls"])),
                    "last_called": row["last_called"],
                }

            # All tools stats
            rows = conn.execute("""
                SELECT * FROM tool_stats ORDER BY total_calls DESC
            """).fetchall()

            return {
                "total_tools": len(self.registry._tools),
                "tools": [
                    {
                        "name": row["tool_name"],
                        "calls": row["total_calls"],
                        "success_rate": round(row["success_count"] / max(1, row["total_calls"]), 3),
                        "avg_duration_ms": round(row["total_duration_ms"] / max(1, row["total_calls"])),
                    }
                    for row in rows
                ],
            }

    def list_tools(self) -> List[Dict[str, Any]]:
        """List all available tools"""
        specs = self.registry.list_tools()
        return [s.to_dict() for s in specs]


# ==================== Built-in Tools ====================

class EchoTool(BaseTool):
    """Simple echo tool for testing"""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="echo",
            category=ToolCategory.COMMUNICATION,
            description="Echo back the input message",
            permissions=[ToolPermission.READ],
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to echo"},
                },
                "required": ["message"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "echoed": {"type": "string"},
                },
            },
            keywords=["echo", "test", "debug"],
            priority=10,
        )

    def execute(self, message: str = "", **kwargs) -> Dict[str, str]:
        return {"echoed": message}


class ShellTool(BaseTool):
    """Execute shell commands (read-only by default)"""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="shell",
            category=ToolCategory.EXECUTION,
            description="Execute shell commands (with safety restrictions)",
            permissions=[ToolPermission.EXECUTE],
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 10},
                    "cwd": {"type": "string", "description": "Working directory"},
                },
                "required": ["command"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "returncode": {"type": "integer"},
                },
            },
            keywords=["shell", "command", "execute", "run", "bash"],
            priority=60,
        )

    # Commands that are blocked for safety
    BLOCKED_PATTERNS = [
        "rm -rf", "rm -r /", "dd if=", "> /dev/",
        "mkfs", "fdisk", "format",
        ":(){", "fork bomb",
    ]

    def execute(
        self,
        command: str,
        timeout: int = 10,
        cwd: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        # Safety check
        cmd_lower = command.lower()
        for pattern in self.BLOCKED_PATTERNS:
            if pattern in cmd_lower:
                raise ValueError(f"Blocked dangerous command pattern: {pattern}")

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Command timed out after {timeout}s",
                "returncode": -1,
            }


# ==================== CLI Interface ====================

def main():
    parser = argparse.ArgumentParser(
        description="Claude Monitor Tool Dispatcher"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # list command
    subparsers.add_parser("list", help="List available tools")

    # call command
    call_parser = subparsers.add_parser("call", help="Call a tool")
    call_parser.add_argument("tool_name", help="Tool name")
    call_parser.add_argument("--args", help="Arguments JSON")

    # recommend command
    rec_parser = subparsers.add_parser("recommend", help="Recommend tools")
    rec_parser.add_argument("task_description", help="Task description")

    # stats command
    stats_parser = subparsers.add_parser("stats", help="Show statistics")
    stats_parser.add_argument("--tool", help="Tool name filter")

    args = parser.parse_args()
    dispatcher = ToolDispatcher()

    if args.command == "list":
        tools = dispatcher.list_tools()
        print(json.dumps(tools, indent=2))

    elif args.command == "call":
        arguments = json.loads(args.args) if args.args else {}
        result = dispatcher.call(args.tool_name, arguments)
        print(json.dumps(result.to_dict(), indent=2))

    elif args.command == "recommend":
        recommendations = dispatcher.recommend(args.task_description)
        print(json.dumps(recommendations, indent=2))

    elif args.command == "stats":
        stats = dispatcher.get_stats(args.tool)
        print(json.dumps(stats, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
