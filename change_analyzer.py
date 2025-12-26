#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Change Analyzer
代码变更理解器 - 分析和追踪代码变更

功能：
1. 变更捕获（Git diff 解析、patch 格式解析）
2. 变更语义分析（函数/类级别摘要、影响范围）
3. 变更与目标关联
4. 变更历史追踪

Usage:
    python3 change_analyzer.py analyze <session_id> [--diff <diff_text>]
    python3 change_analyzer.py history <session_id> [--limit 10]
    python3 change_analyzer.py impact <session_id>
    python3 change_analyzer.py summary <session_id>
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from compat_dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 数据库路径
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


class ChangeType(Enum):
    """变更类型"""
    ADDED = "added"             # 新增文件
    MODIFIED = "modified"       # 修改文件
    DELETED = "deleted"         # 删除文件
    RENAMED = "renamed"         # 重命名文件
    COPIED = "copied"           # 复制文件


class ChangeScope(Enum):
    """变更范围"""
    FUNCTION = "function"       # 函数级别
    CLASS = "class"             # 类级别
    FILE = "file"               # 文件级别
    MODULE = "module"           # 模块级别
    COSMETIC = "cosmetic"       # 格式/注释


class ImpactLevel(Enum):
    """影响级别"""
    BREAKING = "breaking"       # 破坏性变更
    MAJOR = "major"             # 重大变更
    MINOR = "minor"             # 次要变更
    PATCH = "patch"             # 补丁级变更
    NONE = "none"               # 无影响


@dataclass
class FileChange:
    """文件变更"""
    file_path: str
    change_type: ChangeType
    old_path: Optional[str] = None  # 重命名时的旧路径
    additions: int = 0
    deletions: int = 0
    hunks: List[Dict] = field(default_factory=list)  # 变更块

    def to_dict(self) -> Dict:
        return {
            "file_path": self.file_path,
            "change_type": self.change_type.value,
            "old_path": self.old_path,
            "additions": self.additions,
            "deletions": self.deletions,
            "hunks": self.hunks,
        }


@dataclass
class ChangeAnalysis:
    """变更分析结果"""
    change_id: str = ""
    session_id: str = ""
    files: List[FileChange] = field(default_factory=list)
    total_additions: int = 0
    total_deletions: int = 0
    change_scope: ChangeScope = ChangeScope.FILE
    impact_level: ImpactLevel = ImpactLevel.MINOR
    summary: str = ""
    affected_functions: List[str] = field(default_factory=list)
    affected_classes: List[str] = field(default_factory=list)
    is_breaking: bool = False
    related_goal_id: Optional[str] = None
    created_at: int = 0

    def __post_init__(self):
        if not self.change_id:
            self.change_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = int(time.time())

    def to_dict(self) -> Dict:
        return {
            "change_id": self.change_id,
            "session_id": self.session_id,
            "files": [f.to_dict() for f in self.files],
            "total_additions": self.total_additions,
            "total_deletions": self.total_deletions,
            "change_scope": self.change_scope.value,
            "impact_level": self.impact_level.value,
            "summary": self.summary,
            "affected_functions": self.affected_functions,
            "affected_classes": self.affected_classes,
            "is_breaking": self.is_breaking,
            "related_goal_id": self.related_goal_id,
            "created_at": self.created_at,
        }

    def to_context_string(self) -> str:
        """生成用于 LLM 上下文的字符串"""
        impact_icons = {
            ImpactLevel.BREAKING: "🔴",
            ImpactLevel.MAJOR: "🟠",
            ImpactLevel.MINOR: "🟡",
            ImpactLevel.PATCH: "🟢",
            ImpactLevel.NONE: "⚪",
        }
        icon = impact_icons.get(self.impact_level, "?")

        parts = [f"[change] {icon} {len(self.files)} 个文件"]
        parts.append(f"+{self.total_additions}/-{self.total_deletions}")

        if self.summary:
            parts.append(self.summary[:50])

        if self.is_breaking:
            parts.append("⚠️ 破坏性变更")

        return " | ".join(parts)


# 代码结构模式
CODE_PATTERNS = {
    "python": {
        "function": r'^\s*(?:async\s+)?def\s+(\w+)\s*\(',
        "class": r'^\s*class\s+(\w+)\s*[:\(]',
        "import": r'^\s*(?:from\s+\S+\s+)?import\s+',
    },
    "javascript": {
        "function": r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(|(\w+)\s*:\s*(?:async\s*)?function)',
        "class": r'^\s*class\s+(\w+)',
        "import": r'^\s*import\s+',
    },
    "typescript": {
        "function": r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(|(\w+)\s*:\s*(?:async\s*)?function)',
        "class": r'^\s*(?:export\s+)?class\s+(\w+)',
        "interface": r'^\s*(?:export\s+)?interface\s+(\w+)',
        "import": r'^\s*import\s+',
    },
    "go": {
        "function": r'^\s*func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(',
        "struct": r'^\s*type\s+(\w+)\s+struct\s*\{',
        "interface": r'^\s*type\s+(\w+)\s+interface\s*\{',
        "import": r'^\s*import\s+',
    },
    "rust": {
        "function": r'^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)',
        "struct": r'^\s*(?:pub\s+)?struct\s+(\w+)',
        "impl": r'^\s*impl\s+(?:<[^>]+>\s+)?(\w+)',
        "import": r'^\s*use\s+',
    },
}

# 破坏性变更模式
BREAKING_PATTERNS = [
    r'(?:remove|delete|drop)\s+(?:function|method|class|interface)',
    r'(?:rename|change)\s+(?:parameter|argument|return)',
    r'(?:breaking|incompatible)\s+change',
    r'BREAKING\s*CHANGE',
]


class ChangeAnalyzer:
    """代码变更分析器"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_db(self):
        """确保数据库和表存在"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS change_analysis (
                    change_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    files_json TEXT,
                    total_additions INTEGER DEFAULT 0,
                    total_deletions INTEGER DEFAULT 0,
                    change_scope TEXT,
                    impact_level TEXT,
                    summary TEXT,
                    affected_functions TEXT DEFAULT '[]',
                    affected_classes TEXT DEFAULT '[]',
                    is_breaking INTEGER DEFAULT 0,
                    related_goal_id TEXT,
                    created_at INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_change_session
                ON change_analysis(session_id, created_at DESC)
            """)

    def analyze(self, session_id: str, diff_text: Optional[str] = None) -> ChangeAnalysis:
        """分析变更"""
        # 如果没有提供 diff，尝试获取当前 git diff
        if not diff_text:
            diff_text = self._get_git_diff()

        if not diff_text:
            return ChangeAnalysis(session_id=session_id, summary="无变更")

        # 解析 diff
        files = self._parse_diff(diff_text)

        # 分析变更
        analysis = ChangeAnalysis(
            session_id=session_id,
            files=files,
            total_additions=sum(f.additions for f in files),
            total_deletions=sum(f.deletions for f in files),
        )

        # 提取受影响的函数和类
        self._extract_affected_entities(analysis, diff_text)

        # 确定变更范围
        analysis.change_scope = self._determine_scope(analysis)

        # 评估影响级别
        analysis.impact_level = self._assess_impact(analysis, diff_text)

        # 检测破坏性变更
        analysis.is_breaking = self._detect_breaking_changes(diff_text)

        # 生成摘要
        analysis.summary = self._generate_summary(analysis)

        # 保存分析结果
        self._save_analysis(analysis)

        return analysis

    def _get_git_diff(self) -> Optional[str]:
        """获取当前 git diff"""
        try:
            # 获取暂存和未暂存的变更
            result = subprocess.run(
                ["git", "diff", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout

            # 如果没有 HEAD 变更，尝试获取暂存的变更
            result = subprocess.run(
                ["git", "diff", "--cached"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout

        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        return None

    def _parse_diff(self, diff_text: str) -> List[FileChange]:
        """解析 diff 输出"""
        files = []
        current_file = None

        lines = diff_text.split('\n')
        i = 0

        while i < len(lines):
            line = lines[i]

            # 检测文件头
            if line.startswith('diff --git'):
                # 保存之前的文件
                if current_file:
                    files.append(current_file)

                # 解析文件路径
                match = re.match(r'diff --git a/(.+) b/(.+)', line)
                if match:
                    old_path, new_path = match.groups()
                    current_file = FileChange(
                        file_path=new_path,
                        change_type=ChangeType.MODIFIED,
                    )

            elif line.startswith('new file mode'):
                if current_file:
                    current_file.change_type = ChangeType.ADDED

            elif line.startswith('deleted file mode'):
                if current_file:
                    current_file.change_type = ChangeType.DELETED

            elif line.startswith('rename from'):
                if current_file:
                    current_file.change_type = ChangeType.RENAMED
                    current_file.old_path = line[12:]

            elif line.startswith('@@'):
                # 解析 hunk 头
                if current_file:
                    hunk_match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)', line)
                    if hunk_match:
                        current_file.hunks.append({
                            "old_start": int(hunk_match.group(1)),
                            "old_count": int(hunk_match.group(2) or 1),
                            "new_start": int(hunk_match.group(3)),
                            "new_count": int(hunk_match.group(4) or 1),
                            "context": hunk_match.group(5).strip(),
                        })

            elif line.startswith('+') and not line.startswith('+++'):
                if current_file:
                    current_file.additions += 1

            elif line.startswith('-') and not line.startswith('---'):
                if current_file:
                    current_file.deletions += 1

            i += 1

        # 添加最后一个文件
        if current_file:
            files.append(current_file)

        return files

    def _extract_affected_entities(self, analysis: ChangeAnalysis, diff_text: str):
        """提取受影响的函数和类"""
        functions = set()
        classes = set()

        # 根据文件扩展名确定语言
        for file in analysis.files:
            ext = Path(file.file_path).suffix.lower()
            lang = self._detect_language(ext)

            if lang and lang in CODE_PATTERNS:
                patterns = CODE_PATTERNS[lang]

                # 从 hunk 上下文中提取
                for hunk in file.hunks:
                    context = hunk.get("context", "")

                    # 检测函数
                    if "function" in patterns:
                        match = re.search(patterns["function"], context)
                        if match:
                            func_name = next((g for g in match.groups() if g), None)
                            if func_name:
                                functions.add(func_name)

                    # 检测类
                    for key in ["class", "struct", "interface", "impl"]:
                        if key in patterns:
                            match = re.search(patterns[key], context)
                            if match:
                                class_name = next((g for g in match.groups() if g), None)
                                if class_name:
                                    classes.add(class_name)

        # 从 diff 内容中进一步提取
        for line in diff_text.split('\n'):
            if line.startswith('+') or line.startswith('-'):
                content = line[1:]
                for lang, patterns in CODE_PATTERNS.items():
                    if "function" in patterns:
                        match = re.search(patterns["function"], content)
                        if match:
                            func_name = next((g for g in match.groups() if g), None)
                            if func_name:
                                functions.add(func_name)

        analysis.affected_functions = list(functions)[:20]
        analysis.affected_classes = list(classes)[:10]

    def _detect_language(self, ext: str) -> Optional[str]:
        """根据扩展名检测语言"""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".go": "go",
            ".rs": "rust",
        }
        return ext_map.get(ext)

    def _determine_scope(self, analysis: ChangeAnalysis) -> ChangeScope:
        """确定变更范围"""
        if analysis.affected_functions or analysis.affected_classes:
            if len(analysis.affected_functions) > 5 or len(analysis.affected_classes) > 2:
                return ChangeScope.MODULE
            elif analysis.affected_classes:
                return ChangeScope.CLASS
            else:
                return ChangeScope.FUNCTION

        # 检查是否只是格式变更
        if analysis.total_additions == analysis.total_deletions:
            # 可能是重构或格式化
            if analysis.total_additions < 10:
                return ChangeScope.COSMETIC

        if len(analysis.files) > 3:
            return ChangeScope.MODULE

        return ChangeScope.FILE

    def _assess_impact(self, analysis: ChangeAnalysis, diff_text: str) -> ImpactLevel:
        """评估影响级别"""
        # 检测破坏性变更模式
        for pattern in BREAKING_PATTERNS:
            if re.search(pattern, diff_text, re.IGNORECASE):
                return ImpactLevel.BREAKING

        # 基于变更量评估
        total_changes = analysis.total_additions + analysis.total_deletions

        if total_changes > 500:
            return ImpactLevel.MAJOR
        elif total_changes > 100:
            return ImpactLevel.MINOR
        elif total_changes > 10:
            return ImpactLevel.PATCH
        else:
            return ImpactLevel.NONE

    def _detect_breaking_changes(self, diff_text: str) -> bool:
        """检测破坏性变更"""
        # 检测删除的公共接口
        deleted_patterns = [
            r'^-\s*(?:export\s+)?(?:public\s+)?(?:def|function|class|interface)\s+\w+',
            r'^-\s*(?:pub\s+)?(?:fn|struct|trait)\s+\w+',
        ]

        for pattern in deleted_patterns:
            if re.search(pattern, diff_text, re.MULTILINE):
                return True

        # 检测 API 签名变更
        for pattern in BREAKING_PATTERNS:
            if re.search(pattern, diff_text, re.IGNORECASE):
                return True

        return False

    def _generate_summary(self, analysis: ChangeAnalysis) -> str:
        """生成变更摘要"""
        parts = []

        # 文件变更统计
        change_types = {}
        for f in analysis.files:
            ct = f.change_type.value
            change_types[ct] = change_types.get(ct, 0) + 1

        type_str = ", ".join(f"{v}个{k}" for k, v in change_types.items())
        if type_str:
            parts.append(type_str)

        # 受影响的实体
        if analysis.affected_functions:
            parts.append(f"影响函数: {', '.join(analysis.affected_functions[:3])}")
        if analysis.affected_classes:
            parts.append(f"影响类: {', '.join(analysis.affected_classes[:2])}")

        # 变更量
        parts.append(f"+{analysis.total_additions}/-{analysis.total_deletions} 行")

        return "; ".join(parts)

    def _save_analysis(self, analysis: ChangeAnalysis):
        """保存分析结果"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO change_analysis
                (change_id, session_id, files_json, total_additions, total_deletions,
                 change_scope, impact_level, summary, affected_functions, affected_classes,
                 is_breaking, related_goal_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                analysis.change_id,
                analysis.session_id,
                json.dumps([f.to_dict() for f in analysis.files], ensure_ascii=False),
                analysis.total_additions,
                analysis.total_deletions,
                analysis.change_scope.value,
                analysis.impact_level.value,
                analysis.summary,
                json.dumps(analysis.affected_functions, ensure_ascii=False),
                json.dumps(analysis.affected_classes, ensure_ascii=False),
                1 if analysis.is_breaking else 0,
                analysis.related_goal_id,
                analysis.created_at,
            ))

    def get_history(self, session_id: str, limit: int = 10) -> List[ChangeAnalysis]:
        """获取变更历史"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM change_analysis
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (session_id, limit)).fetchall()

            return [self._row_to_analysis(row) for row in rows]

    def get_impact_summary(self, session_id: str) -> Dict:
        """获取影响摘要"""
        history = self.get_history(session_id, limit=20)

        return {
            "total_changes": len(history),
            "total_additions": sum(a.total_additions for a in history),
            "total_deletions": sum(a.total_deletions for a in history),
            "breaking_changes": sum(1 for a in history if a.is_breaking),
            "affected_files": len(set(f.file_path for a in history for f in a.files)),
            "affected_functions": len(set(f for a in history for f in a.affected_functions)),
        }

    def get_latest_summary(self, session_id: str) -> str:
        """获取最新变更摘要（用于 LLM 上下文）"""
        history = self.get_history(session_id, limit=1)
        if history:
            return history[0].to_context_string()
        return ""

    def _row_to_analysis(self, row) -> ChangeAnalysis:
        """将数据库行转换为 ChangeAnalysis"""
        files_data = json.loads(row['files_json'] or '[]')
        files = []
        for fd in files_data:
            files.append(FileChange(
                file_path=fd['file_path'],
                change_type=ChangeType(fd['change_type']),
                old_path=fd.get('old_path'),
                additions=fd.get('additions', 0),
                deletions=fd.get('deletions', 0),
                hunks=fd.get('hunks', []),
            ))

        return ChangeAnalysis(
            change_id=row['change_id'],
            session_id=row['session_id'],
            files=files,
            total_additions=row['total_additions'],
            total_deletions=row['total_deletions'],
            change_scope=ChangeScope(row['change_scope']) if row['change_scope'] else ChangeScope.FILE,
            impact_level=ImpactLevel(row['impact_level']) if row['impact_level'] else ImpactLevel.MINOR,
            summary=row['summary'] or "",
            affected_functions=json.loads(row['affected_functions'] or '[]'),
            affected_classes=json.loads(row['affected_classes'] or '[]'),
            is_breaking=bool(row['is_breaking']),
            related_goal_id=row['related_goal_id'],
            created_at=row['created_at'],
        )


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Change Analyzer',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # analyze
    p_analyze = subparsers.add_parser('analyze', help='Analyze changes')
    p_analyze.add_argument('session_id', help='Session ID')
    p_analyze.add_argument('--diff', '-d', help='Diff text (or read from stdin if not provided)')

    # history
    p_history = subparsers.add_parser('history', help='Get change history')
    p_history.add_argument('session_id', help='Session ID')
    p_history.add_argument('--limit', '-l', type=int, default=10)

    # impact
    p_impact = subparsers.add_parser('impact', help='Get impact summary')
    p_impact.add_argument('session_id', help='Session ID')

    # summary
    p_summary = subparsers.add_parser('summary', help='Get latest change summary')
    p_summary.add_argument('session_id', help='Session ID')

    args = parser.parse_args(argv)
    analyzer = ChangeAnalyzer()

    try:
        if args.command == 'analyze':
            diff_text = args.diff
            if not diff_text and not sys.stdin.isatty():
                diff_text = sys.stdin.read()

            analysis = analyzer.analyze(args.session_id, diff_text)
            print(json.dumps(analysis.to_dict(), indent=2, ensure_ascii=False))

        elif args.command == 'history':
            history = analyzer.get_history(args.session_id, args.limit)
            for a in history:
                impact_icons = {
                    ImpactLevel.BREAKING: "🔴",
                    ImpactLevel.MAJOR: "🟠",
                    ImpactLevel.MINOR: "🟡",
                    ImpactLevel.PATCH: "🟢",
                    ImpactLevel.NONE: "⚪",
                }
                icon = impact_icons.get(a.impact_level, "?")
                print(f"{icon} [{a.change_id}] {a.summary}")

        elif args.command == 'impact':
            impact = analyzer.get_impact_summary(args.session_id)
            print(json.dumps(impact, indent=2, ensure_ascii=False))

        elif args.command == 'summary':
            summary = analyzer.get_latest_summary(args.session_id)
            if summary:
                print(summary)
            else:
                print("No changes recorded")

        else:
            parser.print_help()
            return 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
