#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Search Tool
Search tool - code and document search

Features:
1. Code search (symbols/references/patterns)
2. Document search (project docs/online/error messages)
3. History search (decisions/errors/patterns)
4. Result sorting (relevance/time/importance)
"""

import json
import os
import re
import subprocess
import sys
from compat_dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import core security modules
from core.validators import RegexValidator
from core.exceptions import ValidationError

try:
    from tool_dispatcher import BaseTool, ToolSpec, ToolCategory, ToolPermission
except ImportError:
    # Standalone fallback
    from dataclasses import dataclass

    class ToolCategory:
        SEARCH = "search"

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


@dataclass
class SearchResult:
    """A single search result"""
    file: str
    line: int
    content: str
    context_before: List[str] = field(default_factory=list)
    context_after: List[str] = field(default_factory=list)
    relevance: float = 1.0

    def to_dict(self) -> Dict:
        return {
            "file": self.file,
            "line": self.line,
            "content": self.content,
            "context_before": self.context_before,
            "context_after": self.context_after,
            "relevance": round(self.relevance, 3),
        }


class SearchTool(BaseTool):
    """Code and documentation search tool"""

    # File types for different search modes
    CODE_EXTENSIONS = {
        "python": [".py"],
        "javascript": [".js", ".jsx", ".ts", ".tsx"],
        "rust": [".rs"],
        "go": [".go"],
        "java": [".java"],
        "c": [".c", ".h"],
        "cpp": [".cpp", ".hpp", ".cc", ".hh"],
        "shell": [".sh", ".bash"],
        "ruby": [".rb"],
        "php": [".php"],
    }

    DOC_EXTENSIONS = [".md", ".txt", ".rst", ".adoc", ".org"]

    # Directories to ignore
    IGNORE_DIRS = [
        "node_modules", ".git", "__pycache__", ".venv", "venv",
        "build", "dist", ".next", ".cache", "coverage",
    ]

    MAX_RESULTS = 100
    MAX_FILE_SIZE = 1024 * 1024  # 1MB

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search",
            category=ToolCategory.SEARCH,
            description="Search code and documentation with pattern matching",
            permissions=[ToolPermission.READ],
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["grep", "find", "symbol", "definition", "references"],
                        "description": "Search action to perform",
                    },
                    "pattern": {"type": "string", "description": "Search pattern (regex supported)"},
                    "path": {"type": "string", "description": "Search path", "default": "."},
                    "file_type": {
                        "type": "string",
                        "enum": ["code", "docs", "all", "python", "javascript", "rust", "go"],
                        "description": "File type filter",
                    },
                    "case_sensitive": {"type": "boolean", "default": False},
                    "context_lines": {"type": "integer", "default": 2},
                    "max_results": {"type": "integer", "default": 50},
                },
                "required": ["action", "pattern"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "results": {"type": "array"},
                    "total_matches": {"type": "integer"},
                    "files_searched": {"type": "integer"},
                },
            },
            keywords=["search", "find", "grep", "locate", "pattern", "regex", "symbol", "definition"],
            examples=[
                {"action": "grep", "pattern": "def main", "path": ".", "file_type": "python"},
                {"action": "find", "pattern": "*.py", "path": "."},
                {"action": "symbol", "pattern": "MyClass", "path": "."},
            ],
            priority=75,
        )

    def execute(
        self,
        action: str,
        pattern: str,
        path: str = ".",
        file_type: str = "all",
        case_sensitive: bool = False,
        context_lines: int = 2,
        max_results: int = 50,
        **kwargs
    ) -> Dict[str, Any]:
        """Execute search operation"""
        search_path = Path(path).expanduser().resolve()

        if not search_path.exists():
            return {"success": False, "error": f"Path not found: {path}"}

        max_results = min(max_results, self.MAX_RESULTS)

        if action == "grep":
            return self._grep_search(
                pattern, search_path, file_type,
                case_sensitive, context_lines, max_results
            )
        elif action == "find":
            return self._find_files(pattern, search_path, max_results)
        elif action == "symbol":
            return self._symbol_search(pattern, search_path, file_type, max_results)
        elif action == "definition":
            return self._definition_search(pattern, search_path, file_type, max_results)
        elif action == "references":
            return self._reference_search(pattern, search_path, file_type, max_results)
        else:
            return {"success": False, "error": f"Unknown action: {action}"}

    def _get_extensions(self, file_type: str) -> List[str]:
        """Get file extensions for a file type"""
        if file_type == "all":
            exts = []
            for ext_list in self.CODE_EXTENSIONS.values():
                exts.extend(ext_list)
            exts.extend(self.DOC_EXTENSIONS)
            return exts
        elif file_type == "code":
            exts = []
            for ext_list in self.CODE_EXTENSIONS.values():
                exts.extend(ext_list)
            return exts
        elif file_type == "docs":
            return self.DOC_EXTENSIONS
        elif file_type in self.CODE_EXTENSIONS:
            return self.CODE_EXTENSIONS[file_type]
        else:
            return []

    def _should_search_file(self, file_path: Path, extensions: List[str]) -> bool:
        """Check if file should be searched"""
        # Skip ignored directories
        for part in file_path.parts:
            if part in self.IGNORE_DIRS:
                return False

        # Check extension
        if extensions and file_path.suffix.lower() not in extensions:
            return False

        # Check file size
        try:
            if file_path.stat().st_size > self.MAX_FILE_SIZE:
                return False
        except (OSError, FileNotFoundError) as e:
            return False

        return True

    def _grep_search(
        self,
        pattern: str,
        search_path: Path,
        file_type: str,
        case_sensitive: bool,
        context_lines: int,
        max_results: int
    ) -> Dict[str, Any]:
        """Search for pattern in files"""
        results = []
        files_searched = 0
        extensions = self._get_extensions(file_type)

        try:
            # Validate regex for ReDoS protection
            is_safe, error_msg = RegexValidator.validate(pattern)
            if not is_safe:
                return {
                    "success": False,
                    "error": f"Regex validation failed: {error_msg}"
                }

            regex_flags = 0 if case_sensitive else re.IGNORECASE
            compiled = re.compile(pattern, regex_flags)
        except re.error as e:
            return {"success": False, "error": f"Invalid regex pattern: {e}"}

        # Walk directory
        if search_path.is_file():
            files = [search_path]
        else:
            files = search_path.rglob("*")

        for file_path in files:
            if len(results) >= max_results:
                break

            if not file_path.is_file():
                continue

            if not self._should_search_file(file_path, extensions):
                continue

            files_searched += 1

            try:
                lines = file_path.read_text(errors="replace").split("\n")

                for i, line in enumerate(lines):
                    if compiled.search(line):
                        # Get context
                        start = max(0, i - context_lines)
                        end = min(len(lines), i + context_lines + 1)

                        result = SearchResult(
                            file=str(file_path),
                            line=i + 1,
                            content=line.strip()[:200],
                            context_before=[l.strip()[:100] for l in lines[start:i]],
                            context_after=[l.strip()[:100] for l in lines[i + 1:end]],
                        )
                        results.append(result)

                        if len(results) >= max_results:
                            break
            except Exception:
                continue

        return {
            "success": True,
            "results": [r.to_dict() for r in results],
            "total_matches": len(results),
            "files_searched": files_searched,
            "truncated": len(results) >= max_results,
        }

    def _find_files(
        self,
        pattern: str,
        search_path: Path,
        max_results: int
    ) -> Dict[str, Any]:
        """Find files matching pattern"""
        results = []

        # Convert glob pattern
        if "*" not in pattern and "?" not in pattern:
            pattern = f"*{pattern}*"

        for file_path in search_path.rglob(pattern):
            if len(results) >= max_results:
                break

            # Skip ignored directories
            skip = False
            for part in file_path.parts:
                if part in self.IGNORE_DIRS:
                    skip = True
                    break

            if skip:
                continue

            try:
                stat = file_path.stat()
                results.append({
                    "path": str(file_path),
                    "name": file_path.name,
                    "is_file": file_path.is_file(),
                    "is_directory": file_path.is_dir(),
                    "size": stat.st_size if file_path.is_file() else None,
                    "modified": stat.st_mtime,
                })
            except OSError:
                continue

        return {
            "success": True,
            "results": results,
            "total_matches": len(results),
            "truncated": len(results) >= max_results,
        }

    def _symbol_search(
        self,
        symbol: str,
        search_path: Path,
        file_type: str,
        max_results: int
    ) -> Dict[str, Any]:
        """Search for symbol definitions and uses"""
        # Pattern for symbol as whole word
        pattern = rf"\b{re.escape(symbol)}\b"
        return self._grep_search(
            pattern, search_path, file_type,
            case_sensitive=True, context_lines=2, max_results=max_results
        )

    def _definition_search(
        self,
        name: str,
        search_path: Path,
        file_type: str,
        max_results: int
    ) -> Dict[str, Any]:
        """Search for definition of a symbol"""
        results = []
        extensions = self._get_extensions(file_type)

        # Definition patterns by language
        patterns = {
            ".py": [
                rf"^\s*def\s+{re.escape(name)}\s*\(",
                rf"^\s*class\s+{re.escape(name)}\s*[:\(]",
                rf"^\s*{re.escape(name)}\s*=",
            ],
            ".js": [
                rf"function\s+{re.escape(name)}\s*\(",
                rf"const\s+{re.escape(name)}\s*=",
                rf"let\s+{re.escape(name)}\s*=",
                rf"var\s+{re.escape(name)}\s*=",
                rf"class\s+{re.escape(name)}\s*",
            ],
            ".ts": [
                rf"function\s+{re.escape(name)}\s*[<\(]",
                rf"const\s+{re.escape(name)}\s*[:=]",
                rf"interface\s+{re.escape(name)}\s*",
                rf"type\s+{re.escape(name)}\s*=",
                rf"class\s+{re.escape(name)}\s*",
            ],
            ".go": [
                rf"func\s+{re.escape(name)}\s*\(",
                rf"type\s+{re.escape(name)}\s+",
            ],
            ".rs": [
                rf"fn\s+{re.escape(name)}\s*[<\(]",
                rf"struct\s+{re.escape(name)}\s*",
                rf"enum\s+{re.escape(name)}\s*",
                rf"trait\s+{re.escape(name)}\s*",
            ],
        }

        # Walk files
        if search_path.is_file():
            files = [search_path]
        else:
            files = search_path.rglob("*")

        for file_path in files:
            if len(results) >= max_results:
                break

            if not file_path.is_file():
                continue

            if not self._should_search_file(file_path, extensions):
                continue

            ext = file_path.suffix.lower()
            file_patterns = patterns.get(ext, [])

            # Add generic patterns
            file_patterns.extend([
                rf"^\s*{re.escape(name)}\s*[=:]",
            ])

            try:
                lines = file_path.read_text(errors="replace").split("\n")

                for i, line in enumerate(lines):
                    for pat in file_patterns:
                        if re.search(pat, line):
                            results.append({
                                "file": str(file_path),
                                "line": i + 1,
                                "content": line.strip()[:200],
                                "type": "definition",
                            })
                            break
            except (IOError, OSError, UnicodeDecodeError):
                continue

        return {
            "success": True,
            "results": results,
            "total_matches": len(results),
        }

    def _reference_search(
        self,
        name: str,
        search_path: Path,
        file_type: str,
        max_results: int
    ) -> Dict[str, Any]:
        """Search for references to a symbol"""
        # Get all uses
        symbol_result = self._symbol_search(name, search_path, file_type, max_results * 2)

        if not symbol_result["success"]:
            return symbol_result

        # Get definitions
        def_result = self._definition_search(name, search_path, file_type, max_results)

        # Filter out definitions from references
        def_locations = set()
        if def_result["success"]:
            for d in def_result["results"]:
                def_locations.add((d["file"], d["line"]))

        references = []
        for r in symbol_result["results"]:
            if (r["file"], r["line"]) not in def_locations:
                r["type"] = "reference"
                references.append(r)
                if len(references) >= max_results:
                    break

        return {
            "success": True,
            "results": references,
            "total_matches": len(references),
            "definitions_found": len(def_result.get("results", [])),
        }


# Allow standalone testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python search_tool.py <action> <pattern> [path]")
        print("Actions: grep, find, symbol, definition, references")
        sys.exit(1)

    tool = SearchTool()
    action = sys.argv[1]
    pattern = sys.argv[2]
    path = sys.argv[3] if len(sys.argv) > 3 else "."

    result = tool.execute(action=action, pattern=pattern, path=path)
    print(json.dumps(result, indent=2))
