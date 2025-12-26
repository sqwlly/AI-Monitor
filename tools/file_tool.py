#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor File Tool
文件系统工具 - 安全的文件操作

功能：
1. 文件读取（指定文件/行范围/内容搜索）
2. 文件分析（代码结构/依赖关系/变更影响）
3. 文件操作建议（只生成建议/diff/命令序列）
4. 安全控制（只读模式/敏感文件保护/操作审计）
"""

import hashlib
import json
import os
import re
import sys
from compat_dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from tool_dispatcher import BaseTool, ToolSpec, ToolCategory, ToolPermission
except ImportError:
    # Standalone fallback
    from dataclasses import dataclass

    class ToolCategory:
        FILE = "file"

    class ToolPermission:
        READ = "read"

    @dataclass
    class ToolSpec:
        name: str
        category: Any
        description: str
        permissions: List[Any]
        input_schema: Dict
        output_schema: Dict
        keywords: List[str] = field(default_factory=list)
        examples: List[Dict] = field(default_factory=list)
        priority: int = 50

    class BaseTool:
        pass


class FileTool(BaseTool):
    """File system tool with safety restrictions"""

    # Protected paths that cannot be read
    PROTECTED_PATHS = [
        "/etc/shadow", "/etc/passwd", "/etc/sudoers",
        ".env", ".ssh", "credentials", "secrets",
        "private_key", "id_rsa", "id_ed25519",
    ]

    # Maximum file size to read (5MB)
    MAX_FILE_SIZE = 5 * 1024 * 1024

    # Maximum lines to return
    MAX_LINES = 1000

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="file",
            category=ToolCategory.FILE,
            description="Read and analyze files with safety restrictions",
            permissions=[ToolPermission.READ],
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "head", "tail", "lines", "info", "exists", "list", "analyze"],
                        "description": "Action to perform",
                    },
                    "path": {"type": "string", "description": "File or directory path"},
                    "start_line": {"type": "integer", "description": "Start line for 'lines' action"},
                    "end_line": {"type": "integer", "description": "End line for 'lines' action"},
                    "num_lines": {"type": "integer", "description": "Number of lines for head/tail"},
                    "pattern": {"type": "string", "description": "Pattern for analysis"},
                },
                "required": ["action", "path"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "content": {"type": "string"},
                    "info": {"type": "object"},
                    "error": {"type": "string"},
                },
            },
            keywords=["file", "read", "open", "view", "cat", "head", "tail", "list", "ls"],
            examples=[
                {"action": "read", "path": "/path/to/file.py"},
                {"action": "head", "path": "/path/to/file.txt", "num_lines": 20},
                {"action": "lines", "path": "/path/to/file.py", "start_line": 10, "end_line": 50},
                {"action": "list", "path": "/path/to/directory"},
            ],
            priority=70,
        )

    def execute(
        self,
        action: str,
        path: str,
        start_line: int = 1,
        end_line: int = 0,
        num_lines: int = 10,
        pattern: str = "",
        **kwargs
    ) -> Dict[str, Any]:
        """Execute file operation"""
        # Resolve path
        file_path = Path(path).expanduser().resolve()

        # Safety check
        if not self._is_safe_path(str(file_path)):
            return {
                "success": False,
                "error": "Access to this path is restricted for security",
            }

        if action == "read":
            return self._read_file(file_path)
        elif action == "head":
            return self._head_file(file_path, num_lines)
        elif action == "tail":
            return self._tail_file(file_path, num_lines)
        elif action == "lines":
            return self._read_lines(file_path, start_line, end_line)
        elif action == "info":
            return self._file_info(file_path)
        elif action == "exists":
            return self._file_exists(file_path)
        elif action == "list":
            return self._list_directory(file_path)
        elif action == "analyze":
            return self._analyze_file(file_path, pattern)
        else:
            return {"success": False, "error": f"Unknown action: {action}"}

    def _is_safe_path(self, path: str) -> bool:
        """Check if path is safe to access"""
        path_lower = path.lower()
        for protected in self.PROTECTED_PATHS:
            if protected.lower() in path_lower:
                return False
        return True

    def _read_file(self, path: Path) -> Dict[str, Any]:
        """Read entire file content"""
        try:
            if not path.exists():
                return {"success": False, "error": f"File not found: {path}"}

            if not path.is_file():
                return {"success": False, "error": f"Not a file: {path}"}

            # Check file size
            size = path.stat().st_size
            if size > self.MAX_FILE_SIZE:
                return {
                    "success": False,
                    "error": f"File too large ({size} bytes). Use 'head' or 'lines' action.",
                }

            content = path.read_text(errors="replace")
            lines = content.split("\n")

            if len(lines) > self.MAX_LINES:
                content = "\n".join(lines[:self.MAX_LINES])
                content += f"\n\n... (truncated, {len(lines) - self.MAX_LINES} more lines)"

            return {
                "success": True,
                "content": content,
                "info": {
                    "path": str(path),
                    "size": size,
                    "lines": len(lines),
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _head_file(self, path: Path, num_lines: int) -> Dict[str, Any]:
        """Read first N lines of file"""
        try:
            if not path.exists() or not path.is_file():
                return {"success": False, "error": f"File not found: {path}"}

            lines = []
            with open(path, "r", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= num_lines:
                        break
                    lines.append(line.rstrip("\n"))

            return {
                "success": True,
                "content": "\n".join(lines),
                "info": {"lines_returned": len(lines)},
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tail_file(self, path: Path, num_lines: int) -> Dict[str, Any]:
        """Read last N lines of file"""
        try:
            if not path.exists() or not path.is_file():
                return {"success": False, "error": f"File not found: {path}"}

            lines = path.read_text(errors="replace").split("\n")
            tail_lines = lines[-num_lines:] if len(lines) > num_lines else lines

            return {
                "success": True,
                "content": "\n".join(tail_lines),
                "info": {"lines_returned": len(tail_lines)},
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _read_lines(self, path: Path, start: int, end: int) -> Dict[str, Any]:
        """Read specific line range"""
        try:
            if not path.exists() or not path.is_file():
                return {"success": False, "error": f"File not found: {path}"}

            lines = path.read_text(errors="replace").split("\n")

            # Adjust indices (1-based input, 0-based internal)
            start_idx = max(0, start - 1)
            end_idx = end if end > 0 else len(lines)

            selected = lines[start_idx:end_idx]

            # Add line numbers
            numbered = [
                f"{start_idx + i + 1:4d}: {line}"
                for i, line in enumerate(selected)
            ]

            return {
                "success": True,
                "content": "\n".join(numbered),
                "info": {
                    "start_line": start_idx + 1,
                    "end_line": start_idx + len(selected),
                    "total_lines": len(lines),
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _file_info(self, path: Path) -> Dict[str, Any]:
        """Get file information"""
        try:
            if not path.exists():
                return {"success": False, "error": f"Path not found: {path}"}

            stat = path.stat()
            info = {
                "path": str(path),
                "exists": True,
                "is_file": path.is_file(),
                "is_directory": path.is_dir(),
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "created": stat.st_ctime,
            }

            if path.is_file():
                # Try to count lines
                try:
                    with open(path, "r", errors="replace") as f:
                        info["lines"] = sum(1 for _ in f)
                except:
                    pass

                # Get file extension
                info["extension"] = path.suffix

            return {"success": True, "info": info}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _file_exists(self, path: Path) -> Dict[str, Any]:
        """Check if file exists"""
        return {
            "success": True,
            "exists": path.exists(),
            "is_file": path.is_file() if path.exists() else False,
            "is_directory": path.is_dir() if path.exists() else False,
        }

    def _list_directory(self, path: Path) -> Dict[str, Any]:
        """List directory contents"""
        try:
            if not path.exists():
                return {"success": False, "error": f"Directory not found: {path}"}

            if not path.is_dir():
                return {"success": False, "error": f"Not a directory: {path}"}

            entries = []
            for entry in sorted(path.iterdir()):
                if self._is_safe_path(str(entry)):
                    entries.append({
                        "name": entry.name,
                        "is_file": entry.is_file(),
                        "is_directory": entry.is_dir(),
                        "size": entry.stat().st_size if entry.is_file() else None,
                    })

            return {
                "success": True,
                "path": str(path),
                "entries": entries,
                "count": len(entries),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _analyze_file(self, path: Path, pattern: str = "") -> Dict[str, Any]:
        """Analyze file structure"""
        try:
            if not path.exists() or not path.is_file():
                return {"success": False, "error": f"File not found: {path}"}

            content = path.read_text(errors="replace")
            lines = content.split("\n")

            analysis = {
                "path": str(path),
                "extension": path.suffix,
                "lines": len(lines),
                "size": path.stat().st_size,
                "encoding": "utf-8",  # assumed
            }

            # Language-specific analysis
            ext = path.suffix.lower()

            if ext in [".py"]:
                analysis["language"] = "python"
                analysis["structure"] = self._analyze_python(content)
            elif ext in [".js", ".ts", ".jsx", ".tsx"]:
                analysis["language"] = "javascript"
                analysis["structure"] = self._analyze_javascript(content)
            elif ext in [".sh", ".bash"]:
                analysis["language"] = "shell"
                analysis["structure"] = self._analyze_shell(content)

            # Pattern search if provided
            if pattern:
                matches = []
                for i, line in enumerate(lines, 1):
                    if re.search(pattern, line, re.IGNORECASE):
                        matches.append({"line": i, "content": line.strip()[:100]})
                analysis["pattern_matches"] = matches[:50]  # Limit matches

            return {"success": True, "analysis": analysis}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _analyze_python(self, content: str) -> Dict[str, Any]:
        """Analyze Python file structure"""
        structure = {
            "imports": [],
            "functions": [],
            "classes": [],
        }

        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()

            if stripped.startswith("import ") or stripped.startswith("from "):
                structure["imports"].append({"line": i, "statement": stripped[:80]})
            elif stripped.startswith("def "):
                match = re.match(r"def\s+(\w+)", stripped)
                if match:
                    structure["functions"].append({"line": i, "name": match.group(1)})
            elif stripped.startswith("class "):
                match = re.match(r"class\s+(\w+)", stripped)
                if match:
                    structure["classes"].append({"line": i, "name": match.group(1)})

        return structure

    def _analyze_javascript(self, content: str) -> Dict[str, Any]:
        """Analyze JavaScript/TypeScript file structure"""
        structure = {
            "imports": [],
            "functions": [],
            "classes": [],
            "exports": [],
        }

        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()

            if stripped.startswith("import "):
                structure["imports"].append({"line": i, "statement": stripped[:80]})
            elif "function " in stripped:
                match = re.search(r"function\s+(\w+)", stripped)
                if match:
                    structure["functions"].append({"line": i, "name": match.group(1)})
            elif stripped.startswith("class "):
                match = re.match(r"class\s+(\w+)", stripped)
                if match:
                    structure["classes"].append({"line": i, "name": match.group(1)})
            elif stripped.startswith("export "):
                structure["exports"].append({"line": i, "statement": stripped[:80]})

        return structure

    def _analyze_shell(self, content: str) -> Dict[str, Any]:
        """Analyze shell script structure"""
        structure = {
            "functions": [],
            "variables": [],
            "comments": 0,
        }

        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()

            if stripped.startswith("#"):
                structure["comments"] += 1
            elif "() {" in stripped or "function " in stripped:
                match = re.search(r"(\w+)\s*\(\)", stripped)
                if match:
                    structure["functions"].append({"line": i, "name": match.group(1)})
            elif "=" in stripped and not stripped.startswith("#"):
                match = re.match(r"^(\w+)=", stripped)
                if match:
                    structure["variables"].append({"line": i, "name": match.group(1)})

        return structure


# Allow standalone testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python file_tool.py <action> <path> [options]")
        sys.exit(1)

    tool = FileTool()
    action = sys.argv[1]
    path = sys.argv[2]

    result = tool.execute(action=action, path=path)
    print(json.dumps(result, indent=2))
