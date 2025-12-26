#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Context Fusion
上下文融合器 - 整合多源信息生成优化的 LLM 上下文

功能：
1. 多源信息融合（终端输出/文件变更/Git状态/项目结构/历史决策）
2. 信息优先级排序
3. 上下文压缩（去重/摘要/关键信息提取）
4. 上下文格式化（为 LLM 优化）

Usage:
    python3 context_fusion.py build <session_id> [--max-tokens 2000]
    python3 context_fusion.py add <session_id> <source_type> <content>
    python3 context_fusion.py prioritize <session_id>
    python3 context_fusion.py compress <text> [--max-lines 50]
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from compat_dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 数据库路径
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


class SourceType(Enum):
    """信息来源类型"""
    TERMINAL_OUTPUT = "terminal_output"
    FILE_CHANGE = "file_change"
    GIT_STATUS = "git_status"
    PROJECT_CONTEXT = "project_context"
    INTENT = "intent"
    GOAL = "goal"
    PROGRESS = "progress"
    ERROR = "error"
    DECISION_HISTORY = "decision_history"
    USER_INPUT = "user_input"


class Priority(Enum):
    """信息优先级"""
    CRITICAL = 1    # 错误、阻塞
    HIGH = 2        # 目标相关、用户输入
    MEDIUM = 3      # 进度、文件变更
    LOW = 4         # 普通输出、历史
    BACKGROUND = 5  # 项目结构等静态信息


# 优先级配置
PRIORITY_CONFIG = {
    SourceType.ERROR: Priority.CRITICAL,
    SourceType.USER_INPUT: Priority.HIGH,
    SourceType.INTENT: Priority.HIGH,
    SourceType.GOAL: Priority.HIGH,
    SourceType.PROGRESS: Priority.MEDIUM,
    SourceType.FILE_CHANGE: Priority.MEDIUM,
    SourceType.TERMINAL_OUTPUT: Priority.MEDIUM,
    SourceType.GIT_STATUS: Priority.MEDIUM,
    SourceType.DECISION_HISTORY: Priority.LOW,
    SourceType.PROJECT_CONTEXT: Priority.BACKGROUND,
}

# 各来源的 Token 预算比例
TOKEN_BUDGET_RATIO = {
    Priority.CRITICAL: 0.30,    # 30% 给关键信息
    Priority.HIGH: 0.25,        # 25% 给高优先级
    Priority.MEDIUM: 0.25,      # 25% 给中优先级
    Priority.LOW: 0.15,         # 15% 给低优先级
    Priority.BACKGROUND: 0.05,  # 5% 给背景信息
}


@dataclass
class ContextItem:
    """上下文项"""
    source_type: SourceType
    content: str
    priority: Priority = Priority.MEDIUM
    timestamp: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    relevance_score: float = 1.0  # 与当前目标的相关度

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = int(time.time())
        if isinstance(self.source_type, str):
            self.source_type = SourceType(self.source_type)
        if isinstance(self.priority, int):
            self.priority = Priority(self.priority)
        elif isinstance(self.priority, str):
            self.priority = Priority[self.priority.upper()]

    def to_dict(self) -> Dict:
        return {
            "source_type": self.source_type.value,
            "content": self.content,
            "priority": self.priority.value,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "relevance_score": self.relevance_score,
        }

    def content_hash(self) -> str:
        """计算内容哈希（用于去重）"""
        return hashlib.md5(self.content.encode()).hexdigest()[:8]

    def estimate_tokens(self) -> int:
        """估算 token 数量（粗略估计）"""
        # 英文约 4 字符/token，中文约 1.5 字符/token
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', self.content))
        other_chars = len(self.content) - chinese_chars
        return int(chinese_chars / 1.5 + other_chars / 4)


@dataclass
class FusedContext:
    """融合后的上下文"""
    items: List[ContextItem] = field(default_factory=list)
    total_tokens: int = 0
    session_id: str = ""
    built_at: int = 0

    def to_formatted_string(self) -> str:
        """生成格式化的上下文字符串"""
        sections = {}

        # 按来源类型分组
        for item in self.items:
            source = item.source_type.value
            if source not in sections:
                sections[source] = []
            sections[source].append(item.content)

        # 格式化输出
        parts = []

        # 按优先级顺序输出
        source_order = [
            SourceType.ERROR,
            SourceType.INTENT,
            SourceType.GOAL,
            SourceType.PROGRESS,
            SourceType.USER_INPUT,
            SourceType.TERMINAL_OUTPUT,
            SourceType.FILE_CHANGE,
            SourceType.GIT_STATUS,
            SourceType.DECISION_HISTORY,
            SourceType.PROJECT_CONTEXT,
        ]

        for source_type in source_order:
            source = source_type.value
            if source in sections and sections[source]:
                section_header = self._get_section_header(source_type)
                content = "\n".join(sections[source])
                parts.append(f"{section_header}\n{content}")

        return "\n\n".join(parts)

    def _get_section_header(self, source_type: SourceType) -> str:
        """获取段落标题"""
        headers = {
            SourceType.ERROR: "【错误信息】",
            SourceType.INTENT: "【当前意图】",
            SourceType.GOAL: "【目标进度】",
            SourceType.PROGRESS: "【任务进度】",
            SourceType.USER_INPUT: "【用户输入】",
            SourceType.TERMINAL_OUTPUT: "【终端输出】",
            SourceType.FILE_CHANGE: "【文件变更】",
            SourceType.GIT_STATUS: "【Git状态】",
            SourceType.DECISION_HISTORY: "【历史决策】",
            SourceType.PROJECT_CONTEXT: "【项目上下文】",
        }
        return headers.get(source_type, f"【{source_type.value}】")


class ContextFusion:
    """上下文融合器"""

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
                CREATE TABLE IF NOT EXISTS context_items (
                    item_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT,
                    priority INTEGER DEFAULT 3,
                    timestamp INTEGER,
                    metadata TEXT DEFAULT '{}',
                    relevance_score REAL DEFAULT 1.0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_context_session
                ON context_items(session_id, timestamp DESC)
            """)

    def add_item(self, session_id: str, source_type: SourceType, content: str,
                priority: Optional[Priority] = None, metadata: Dict = None) -> ContextItem:
        """添加上下文项"""
        if not content or not content.strip():
            return None

        # 使用默认优先级
        if priority is None:
            priority = PRIORITY_CONFIG.get(source_type, Priority.MEDIUM)

        item = ContextItem(
            source_type=source_type,
            content=content.strip(),
            priority=priority,
            metadata=metadata or {},
        )

        # 检查去重
        if self._is_duplicate(session_id, item):
            return None

        # 保存
        self._save_item(session_id, item)
        return item

    def _is_duplicate(self, session_id: str, item: ContextItem) -> bool:
        """检查是否重复"""
        content_hash = item.content_hash()

        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT 1 FROM context_items
                WHERE session_id = ? AND content_hash = ?
                AND timestamp > ?
                LIMIT 1
            """, (session_id, content_hash, int(time.time()) - 300)).fetchone()

            return row is not None

    def _save_item(self, session_id: str, item: ContextItem):
        """保存上下文项"""
        import uuid
        item_id = str(uuid.uuid4())[:8]

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO context_items
                (item_id, session_id, source_type, content, content_hash,
                 priority, timestamp, metadata, relevance_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item_id,
                session_id,
                item.source_type.value,
                item.content,
                item.content_hash(),
                item.priority.value,
                item.timestamp,
                json.dumps(item.metadata, ensure_ascii=False),
                item.relevance_score,
            ))

    def build_context(self, session_id: str, max_tokens: int = 2000,
                     current_intent: str = "") -> FusedContext:
        """构建融合上下文"""
        # 获取所有相关项
        items = self._get_recent_items(session_id, limit=100)

        # 计算相关度
        if current_intent:
            items = self._calculate_relevance(items, current_intent)

        # 按优先级和相关度排序
        items = self._prioritize(items)

        # 去重
        items = self._deduplicate(items)

        # 按 token 预算分配
        selected_items = self._allocate_budget(items, max_tokens)

        # 压缩过长的内容
        selected_items = self._compress_items(selected_items, max_tokens)

        # 构建结果
        fused = FusedContext(
            items=selected_items,
            total_tokens=sum(item.estimate_tokens() for item in selected_items),
            session_id=session_id,
            built_at=int(time.time()),
        )

        return fused

    def _get_recent_items(self, session_id: str, limit: int = 100) -> List[ContextItem]:
        """获取最近的上下文项"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM context_items
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (session_id, limit)).fetchall()

            return [self._row_to_item(row) for row in rows]

    def _calculate_relevance(self, items: List[ContextItem], intent: str) -> List[ContextItem]:
        """计算与当前意图的相关度"""
        intent_words = set(intent.lower().split())

        for item in items:
            content_words = set(item.content.lower().split())
            if intent_words and content_words:
                overlap = len(intent_words & content_words)
                item.relevance_score = min(1.0, 0.3 + (overlap / len(intent_words)) * 0.7)
            else:
                item.relevance_score = 0.5

        return items

    def _prioritize(self, items: List[ContextItem]) -> List[ContextItem]:
        """按优先级和相关度排序"""
        return sorted(items, key=lambda x: (
            x.priority.value,                    # 优先级（越小越高）
            -x.relevance_score,                  # 相关度（越大越高）
            -x.timestamp,                        # 时间（越新越高）
        ))

    def _deduplicate(self, items: List[ContextItem]) -> List[ContextItem]:
        """去重（保留第一个出现的）"""
        seen_hashes = set()
        result = []

        for item in items:
            h = item.content_hash()
            if h not in seen_hashes:
                seen_hashes.add(h)
                result.append(item)

        return result

    def _allocate_budget(self, items: List[ContextItem], max_tokens: int) -> List[ContextItem]:
        """按优先级分配 token 预算"""
        # 计算每个优先级的预算
        budgets = {
            priority: int(max_tokens * ratio)
            for priority, ratio in TOKEN_BUDGET_RATIO.items()
        }

        # 按优先级分组
        groups = {p: [] for p in Priority}
        for item in items:
            groups[item.priority].append(item)

        # 分配
        selected = []
        used_tokens = {p: 0 for p in Priority}

        for priority in Priority:
            budget = budgets[priority]
            for item in groups[priority]:
                tokens = item.estimate_tokens()
                if used_tokens[priority] + tokens <= budget:
                    selected.append(item)
                    used_tokens[priority] += tokens
                elif tokens < budget * 0.5:
                    # 如果项目较小，尝试挤进去
                    selected.append(item)
                    used_tokens[priority] += tokens

        return selected

    def _compress_items(self, items: List[ContextItem], max_tokens: int) -> List[ContextItem]:
        """压缩过长的内容"""
        total_tokens = sum(item.estimate_tokens() for item in items)

        if total_tokens <= max_tokens:
            return items

        # 需要压缩
        ratio = max_tokens / total_tokens

        for item in items:
            if item.priority.value >= Priority.MEDIUM.value:
                # 低优先级的内容进行压缩
                item.content = self._compress_content(item.content, ratio)

        return items

    def _compress_content(self, content: str, ratio: float) -> str:
        """压缩内容"""
        lines = content.split('\n')
        target_lines = max(3, int(len(lines) * ratio))

        if len(lines) <= target_lines:
            return content

        # 保留开头和结尾
        keep_start = target_lines // 2
        keep_end = target_lines - keep_start - 1

        result = lines[:keep_start]
        result.append(f"... ({len(lines) - target_lines} 行已省略) ...")
        result.extend(lines[-keep_end:] if keep_end > 0 else [])

        return '\n'.join(result)

    def get_summary(self, session_id: str, max_tokens: int = 500) -> str:
        """获取上下文摘要（用于 LLM）"""
        fused = self.build_context(session_id, max_tokens)
        return fused.to_formatted_string()

    def clear_old_items(self, session_id: str, max_age_seconds: int = 3600):
        """清理旧的上下文项"""
        cutoff = int(time.time()) - max_age_seconds

        with self._get_conn() as conn:
            conn.execute("""
                DELETE FROM context_items
                WHERE session_id = ? AND timestamp < ?
            """, (session_id, cutoff))

    def _row_to_item(self, row) -> ContextItem:
        """将数据库行转换为 ContextItem"""
        return ContextItem(
            source_type=SourceType(row['source_type']),
            content=row['content'],
            priority=Priority(row['priority']),
            timestamp=row['timestamp'],
            metadata=json.loads(row['metadata'] or '{}'),
            relevance_score=row['relevance_score'] or 1.0,
        )


class ContextBuilder:
    """上下文构建器 - 便捷接口"""

    def __init__(self, session_id: str, fusion: Optional[ContextFusion] = None):
        self.session_id = session_id
        self.fusion = fusion or ContextFusion()

    def add_terminal_output(self, content: str) -> 'ContextBuilder':
        """添加终端输出"""
        self.fusion.add_item(self.session_id, SourceType.TERMINAL_OUTPUT, content)
        return self

    def add_error(self, content: str) -> 'ContextBuilder':
        """添加错误信息"""
        self.fusion.add_item(self.session_id, SourceType.ERROR, content)
        return self

    def add_intent(self, content: str) -> 'ContextBuilder':
        """添加意图信息"""
        self.fusion.add_item(self.session_id, SourceType.INTENT, content)
        return self

    def add_goal(self, content: str) -> 'ContextBuilder':
        """添加目标信息"""
        self.fusion.add_item(self.session_id, SourceType.GOAL, content)
        return self

    def add_progress(self, content: str) -> 'ContextBuilder':
        """添加进度信息"""
        self.fusion.add_item(self.session_id, SourceType.PROGRESS, content)
        return self

    def add_file_change(self, content: str) -> 'ContextBuilder':
        """添加文件变更信息"""
        self.fusion.add_item(self.session_id, SourceType.FILE_CHANGE, content)
        return self

    def add_git_status(self, content: str) -> 'ContextBuilder':
        """添加 Git 状态"""
        self.fusion.add_item(self.session_id, SourceType.GIT_STATUS, content)
        return self

    def add_decision_history(self, content: str) -> 'ContextBuilder':
        """添加历史决策"""
        self.fusion.add_item(self.session_id, SourceType.DECISION_HISTORY, content)
        return self

    def add_project_context(self, content: str) -> 'ContextBuilder':
        """添加项目上下文"""
        self.fusion.add_item(self.session_id, SourceType.PROJECT_CONTEXT, content)
        return self

    def build(self, max_tokens: int = 2000, current_intent: str = "") -> str:
        """构建最终上下文"""
        fused = self.fusion.build_context(self.session_id, max_tokens, current_intent)
        return fused.to_formatted_string()


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Context Fusion',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # build
    p_build = subparsers.add_parser('build', help='Build fused context')
    p_build.add_argument('session_id', help='Session ID')
    p_build.add_argument('--max-tokens', '-t', type=int, default=2000,
                        help='Maximum tokens')
    p_build.add_argument('--intent', '-i', default='', help='Current intent for relevance')
    p_build.add_argument('--json', action='store_true', help='Output as JSON')

    # add
    p_add = subparsers.add_parser('add', help='Add context item')
    p_add.add_argument('session_id', help='Session ID')
    p_add.add_argument('source_type', choices=[s.value for s in SourceType],
                      help='Source type')
    p_add.add_argument('content', nargs='?', help='Content (or read from stdin)')

    # prioritize
    p_prioritize = subparsers.add_parser('prioritize', help='Show prioritized items')
    p_prioritize.add_argument('session_id', help='Session ID')
    p_prioritize.add_argument('--limit', '-l', type=int, default=20)

    # compress
    p_compress = subparsers.add_parser('compress', help='Compress text')
    p_compress.add_argument('text', nargs='?', help='Text to compress (or read from stdin)')
    p_compress.add_argument('--max-lines', '-l', type=int, default=50)

    # clear
    p_clear = subparsers.add_parser('clear', help='Clear old items')
    p_clear.add_argument('session_id', help='Session ID')
    p_clear.add_argument('--max-age', '-a', type=int, default=3600,
                        help='Max age in seconds')

    args = parser.parse_args(argv)
    fusion = ContextFusion()

    try:
        if args.command == 'build':
            fused = fusion.build_context(args.session_id, args.max_tokens, args.intent)
            if args.json:
                output = {
                    "session_id": fused.session_id,
                    "total_tokens": fused.total_tokens,
                    "item_count": len(fused.items),
                    "built_at": fused.built_at,
                    "formatted": fused.to_formatted_string(),
                }
                print(json.dumps(output, indent=2, ensure_ascii=False))
            else:
                print(fused.to_formatted_string())

        elif args.command == 'add':
            content = args.content
            if not content:
                content = sys.stdin.read()

            source_type = SourceType(args.source_type)
            item = fusion.add_item(args.session_id, source_type, content)

            if item:
                print(f"Added {source_type.value} item (priority: {item.priority.value})")
            else:
                print("Item was duplicate or empty, not added")

        elif args.command == 'prioritize':
            items = fusion._get_recent_items(args.session_id, args.limit)
            items = fusion._prioritize(items)

            for item in items[:args.limit]:
                priority_icons = {1: "🔴", 2: "🟠", 3: "🟡", 4: "🟢", 5: "⚪"}
                icon = priority_icons.get(item.priority.value, "?")
                preview = item.content[:60].replace('\n', ' ')
                print(f"{icon} [{item.source_type.value}] {preview}...")

        elif args.command == 'compress':
            text = args.text
            if not text:
                text = sys.stdin.read()

            lines = text.split('\n')
            if len(lines) > args.max_lines:
                keep = args.max_lines // 2
                result = lines[:keep]
                result.append(f"... ({len(lines) - args.max_lines} 行已省略) ...")
                result.extend(lines[-(args.max_lines - keep - 1):])
                print('\n'.join(result))
            else:
                print(text)

        elif args.command == 'clear':
            fusion.clear_old_items(args.session_id, args.max_age)
            print(f"Cleared items older than {args.max_age} seconds")

        else:
            parser.print_help()
            return 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
