#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Working Memory
工作记忆管理器 - 管理当前会话的短期记忆

功能：
1. 短期记忆（最近命令/输出/目标状态/阻塞点）
2. 记忆容量管理（重要性淘汰/相关性保留/动态调整）
3. 记忆检索（按时间/相关性/类型）
4. 记忆摘要（阶段摘要/关键信息提取/上下文压缩）

Usage:
    python3 working_memory.py add <session_id> <memory_type> <content>
    python3 working_memory.py get <session_id> [--type command|output|goal|blocker]
    python3 working_memory.py search <session_id> <query>
    python3 working_memory.py summarize <session_id>
    python3 working_memory.py compact <session_id>
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from compat_dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# 数据库路径
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


class MemoryType(Enum):
    """记忆类型"""
    COMMAND = "command"         # 命令
    OUTPUT = "output"           # 输出
    GOAL = "goal"               # 目标状态
    BLOCKER = "blocker"         # 阻塞点
    DECISION = "decision"       # 决策
    ERROR = "error"             # 错误
    MILESTONE = "milestone"     # 里程碑
    CONTEXT = "context"         # 上下文信息


class Importance(Enum):
    """重要性级别"""
    CRITICAL = 1    # 关键（永不淘汰）
    HIGH = 2        # 高（最后淘汰）
    MEDIUM = 3      # 中（正常淘汰）
    LOW = 4         # 低（优先淘汰）


# 容量配置
CAPACITY_CONFIG = {
    MemoryType.COMMAND: 20,
    MemoryType.OUTPUT: 30,
    MemoryType.GOAL: 5,
    MemoryType.BLOCKER: 10,
    MemoryType.DECISION: 15,
    MemoryType.ERROR: 20,
    MemoryType.MILESTONE: 10,
    MemoryType.CONTEXT: 10,
}

# 默认重要性
DEFAULT_IMPORTANCE = {
    MemoryType.COMMAND: Importance.MEDIUM,
    MemoryType.OUTPUT: Importance.LOW,
    MemoryType.GOAL: Importance.HIGH,
    MemoryType.BLOCKER: Importance.HIGH,
    MemoryType.DECISION: Importance.MEDIUM,
    MemoryType.ERROR: Importance.HIGH,
    MemoryType.MILESTONE: Importance.CRITICAL,
    MemoryType.CONTEXT: Importance.LOW,
}


@dataclass
class MemoryItem:
    """记忆项"""
    memory_id: str = ""
    session_id: str = ""
    memory_type: MemoryType = MemoryType.OUTPUT
    content: str = ""
    importance: Importance = Importance.MEDIUM
    relevance_score: float = 1.0
    access_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: int = 0
    last_accessed_at: int = 0

    def __post_init__(self):
        if not self.memory_id:
            self.memory_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = int(time.time())
        if not self.last_accessed_at:
            self.last_accessed_at = self.created_at
        if isinstance(self.memory_type, str):
            self.memory_type = MemoryType(self.memory_type)
        if isinstance(self.importance, int):
            self.importance = Importance(self.importance)

    def to_dict(self) -> Dict:
        return {
            "memory_id": self.memory_id,
            "session_id": self.session_id,
            "memory_type": self.memory_type.value,
            "content": self.content,
            "importance": self.importance.value,
            "relevance_score": self.relevance_score,
            "access_count": self.access_count,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
        }

    def content_hash(self) -> str:
        """计算内容哈希"""
        return hashlib.md5(self.content.encode()).hexdigest()[:8]

    def to_context_string(self) -> str:
        """生成用于显示的字符串"""
        type_icons = {
            MemoryType.COMMAND: "⚡",
            MemoryType.OUTPUT: "📤",
            MemoryType.GOAL: "🎯",
            MemoryType.BLOCKER: "🚫",
            MemoryType.DECISION: "🎲",
            MemoryType.ERROR: "❌",
            MemoryType.MILESTONE: "🏁",
            MemoryType.CONTEXT: "📋",
        }
        icon = type_icons.get(self.memory_type, "•")
        preview = self.content[:60].replace('\n', ' ')
        return f"{icon} {preview}"


@dataclass
class MemorySummary:
    """记忆摘要"""
    session_id: str = ""
    total_items: int = 0
    by_type: Dict[str, int] = field(default_factory=dict)
    active_goal: str = ""
    current_blockers: List[str] = field(default_factory=list)
    recent_errors: List[str] = field(default_factory=list)
    key_decisions: List[str] = field(default_factory=list)
    compressed_context: str = ""

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "total_items": self.total_items,
            "by_type": self.by_type,
            "active_goal": self.active_goal,
            "current_blockers": self.current_blockers,
            "recent_errors": self.recent_errors,
            "key_decisions": self.key_decisions,
            "compressed_context": self.compressed_context,
        }

    def to_context_string(self) -> str:
        """生成用于 LLM 上下文的字符串"""
        parts = []

        if self.active_goal:
            parts.append(f"[目标] {self.active_goal}")

        if self.current_blockers:
            parts.append(f"[阻塞] {'; '.join(self.current_blockers[:2])}")

        if self.recent_errors:
            parts.append(f"[错误] {self.recent_errors[0]}")

        if self.key_decisions:
            parts.append(f"[决策] {self.key_decisions[0]}")

        if self.compressed_context:
            parts.append(f"[上下文] {self.compressed_context[:100]}")

        return "\n".join(parts) if parts else "无记忆摘要"


class WorkingMemory:
    """工作记忆管理器"""

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
                CREATE TABLE IF NOT EXISTS working_memory (
                    memory_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT,
                    importance INTEGER DEFAULT 3,
                    relevance_score REAL DEFAULT 1.0,
                    access_count INTEGER DEFAULT 0,
                    metadata TEXT DEFAULT '{}',
                    created_at INTEGER,
                    last_accessed_at INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_wm_session_type
                ON working_memory(session_id, memory_type)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_wm_session_time
                ON working_memory(session_id, created_at DESC)
            """)

    def add(self, session_id: str, memory_type: MemoryType, content: str,
           importance: Optional[Importance] = None, metadata: Dict = None) -> Optional[MemoryItem]:
        """添加记忆项"""
        if not content or not content.strip():
            return None

        # 使用默认重要性
        if importance is None:
            importance = DEFAULT_IMPORTANCE.get(memory_type, Importance.MEDIUM)

        item = MemoryItem(
            session_id=session_id,
            memory_type=memory_type,
            content=content.strip(),
            importance=importance,
            metadata=metadata or {},
        )

        # 检查去重
        if self._is_duplicate(session_id, item):
            return None

        # 检查容量，必要时淘汰
        self._enforce_capacity(session_id, memory_type)

        # 保存
        self._save_item(item)

        return item

    def _is_duplicate(self, session_id: str, item: MemoryItem) -> bool:
        """检查是否重复"""
        content_hash = item.content_hash()

        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT 1 FROM working_memory
                WHERE session_id = ? AND content_hash = ?
                LIMIT 1
            """, (session_id, content_hash)).fetchone()

            return row is not None

    def _enforce_capacity(self, session_id: str, memory_type: MemoryType):
        """强制容量限制"""
        capacity = CAPACITY_CONFIG.get(memory_type, 20)

        with self._get_conn() as conn:
            # 获取当前数量
            count = conn.execute("""
                SELECT COUNT(*) FROM working_memory
                WHERE session_id = ? AND memory_type = ?
            """, (session_id, memory_type.value)).fetchone()[0]

            if count >= capacity:
                # 需要淘汰
                to_delete = count - capacity + 1

                # 按重要性和时间淘汰（保留关键项）
                conn.execute("""
                    DELETE FROM working_memory
                    WHERE memory_id IN (
                        SELECT memory_id FROM working_memory
                        WHERE session_id = ? AND memory_type = ?
                        AND importance > 1
                        ORDER BY importance DESC, last_accessed_at ASC
                        LIMIT ?
                    )
                """, (session_id, memory_type.value, to_delete))

    def _save_item(self, item: MemoryItem):
        """保存记忆项"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO working_memory
                (memory_id, session_id, memory_type, content, content_hash,
                 importance, relevance_score, access_count, metadata,
                 created_at, last_accessed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.memory_id,
                item.session_id,
                item.memory_type.value,
                item.content,
                item.content_hash(),
                item.importance.value,
                item.relevance_score,
                item.access_count,
                json.dumps(item.metadata, ensure_ascii=False),
                item.created_at,
                item.last_accessed_at,
            ))

    def get(self, session_id: str, memory_type: Optional[MemoryType] = None,
           limit: int = 20) -> List[MemoryItem]:
        """获取记忆项"""
        with self._get_conn() as conn:
            if memory_type:
                rows = conn.execute("""
                    SELECT * FROM working_memory
                    WHERE session_id = ? AND memory_type = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (session_id, memory_type.value, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM working_memory
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (session_id, limit)).fetchall()

            items = [self._row_to_item(row) for row in rows]

            # 更新访问时间
            for item in items:
                self._touch(item.memory_id)

            return items

    def _touch(self, memory_id: str):
        """更新访问时间"""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE working_memory
                SET last_accessed_at = ?, access_count = access_count + 1
                WHERE memory_id = ?
            """, (int(time.time()), memory_id))

    def search(self, session_id: str, query: str, limit: int = 10) -> List[MemoryItem]:
        """搜索记忆"""
        query_words = set(query.lower().split())

        items = self.get(session_id, limit=100)
        results = []

        for item in items:
            content_words = set(item.content.lower().split())
            overlap = len(query_words & content_words)
            if overlap > 0:
                item.relevance_score = overlap / len(query_words)
                results.append(item)

        # 按相关性排序
        results.sort(key=lambda x: -x.relevance_score)

        return results[:limit]

    def get_by_type(self, session_id: str, memory_types: List[MemoryType],
                   limit_per_type: int = 5) -> Dict[MemoryType, List[MemoryItem]]:
        """按类型批量获取"""
        result = {}
        for mt in memory_types:
            result[mt] = self.get(session_id, mt, limit_per_type)
        return result

    def summarize(self, session_id: str) -> MemorySummary:
        """生成记忆摘要"""
        summary = MemorySummary(session_id=session_id)

        # 统计各类型数量
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT memory_type, COUNT(*) as cnt
                FROM working_memory
                WHERE session_id = ?
                GROUP BY memory_type
            """, (session_id,)).fetchall()

            for row in rows:
                summary.by_type[row['memory_type']] = row['cnt']
                summary.total_items += row['cnt']

        # 获取关键信息
        goals = self.get(session_id, MemoryType.GOAL, limit=1)
        if goals:
            summary.active_goal = goals[0].content[:100]

        blockers = self.get(session_id, MemoryType.BLOCKER, limit=3)
        summary.current_blockers = [b.content[:50] for b in blockers]

        errors = self.get(session_id, MemoryType.ERROR, limit=3)
        summary.recent_errors = [e.content[:50] for e in errors]

        decisions = self.get(session_id, MemoryType.DECISION, limit=3)
        summary.key_decisions = [d.content[:50] for d in decisions]

        # 生成压缩上下文
        summary.compressed_context = self._generate_compressed_context(session_id)

        return summary

    def _generate_compressed_context(self, session_id: str) -> str:
        """生成压缩的上下文"""
        parts = []

        # 最近的命令
        commands = self.get(session_id, MemoryType.COMMAND, limit=3)
        if commands:
            parts.append("最近命令: " + "; ".join(c.content[:30] for c in commands))

        # 最近的输出（只保留关键词）
        outputs = self.get(session_id, MemoryType.OUTPUT, limit=3)
        if outputs:
            keywords = set()
            for o in outputs:
                # 提取可能的关键词
                words = re.findall(r'(?:error|success|failed|warning|completed|started)',
                                  o.content.lower())
                keywords.update(words)
            if keywords:
                parts.append("状态关键词: " + ", ".join(keywords))

        return "; ".join(parts)

    def compact(self, session_id: str, aggressive: bool = False):
        """压缩记忆（清理低优先级项）"""
        with self._get_conn() as conn:
            if aggressive:
                # 激进模式：只保留高重要性项
                conn.execute("""
                    DELETE FROM working_memory
                    WHERE session_id = ? AND importance > 2
                """, (session_id,))
            else:
                # 正常模式：删除最旧的低优先级项
                for memory_type in MemoryType:
                    capacity = CAPACITY_CONFIG.get(memory_type, 20)
                    keep = capacity // 2

                    conn.execute("""
                        DELETE FROM working_memory
                        WHERE memory_id IN (
                            SELECT memory_id FROM working_memory
                            WHERE session_id = ? AND memory_type = ?
                            AND importance >= 3
                            ORDER BY last_accessed_at ASC
                            LIMIT (
                                SELECT MAX(0, COUNT(*) - ?)
                                FROM working_memory
                                WHERE session_id = ? AND memory_type = ?
                            )
                        )
                    """, (session_id, memory_type.value, keep,
                          session_id, memory_type.value))

    def clear(self, session_id: str, memory_type: Optional[MemoryType] = None):
        """清空记忆"""
        with self._get_conn() as conn:
            if memory_type:
                conn.execute("""
                    DELETE FROM working_memory
                    WHERE session_id = ? AND memory_type = ?
                """, (session_id, memory_type.value))
            else:
                conn.execute("""
                    DELETE FROM working_memory
                    WHERE session_id = ?
                """, (session_id,))

    def get_context_for_llm(self, session_id: str, max_tokens: int = 500) -> str:
        """获取用于 LLM 的上下文"""
        summary = self.summarize(session_id)
        return summary.to_context_string()

    def _row_to_item(self, row) -> MemoryItem:
        """将数据库行转换为 MemoryItem"""
        return MemoryItem(
            memory_id=row['memory_id'],
            session_id=row['session_id'],
            memory_type=MemoryType(row['memory_type']),
            content=row['content'],
            importance=Importance(row['importance']),
            relevance_score=row['relevance_score'] or 1.0,
            access_count=row['access_count'] or 0,
            metadata=json.loads(row['metadata'] or '{}'),
            created_at=row['created_at'],
            last_accessed_at=row['last_accessed_at'],
        )


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Working Memory',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # add
    p_add = subparsers.add_parser('add', help='Add a memory item')
    p_add.add_argument('session_id', help='Session ID')
    p_add.add_argument('memory_type', choices=[m.value for m in MemoryType])
    p_add.add_argument('content', nargs='?', help='Content (or read from stdin)')
    p_add.add_argument('--importance', '-i', type=int, choices=[1, 2, 3, 4], default=3)

    # get
    p_get = subparsers.add_parser('get', help='Get memory items')
    p_get.add_argument('session_id', help='Session ID')
    p_get.add_argument('--type', '-t', choices=[m.value for m in MemoryType])
    p_get.add_argument('--limit', '-l', type=int, default=20)

    # search
    p_search = subparsers.add_parser('search', help='Search memory')
    p_search.add_argument('session_id', help='Session ID')
    p_search.add_argument('query', help='Search query')
    p_search.add_argument('--limit', '-l', type=int, default=10)

    # summarize
    p_summarize = subparsers.add_parser('summarize', help='Summarize memory')
    p_summarize.add_argument('session_id', help='Session ID')
    p_summarize.add_argument('--json', action='store_true', help='Output as JSON')

    # compact
    p_compact = subparsers.add_parser('compact', help='Compact memory')
    p_compact.add_argument('session_id', help='Session ID')
    p_compact.add_argument('--aggressive', '-a', action='store_true')

    # clear
    p_clear = subparsers.add_parser('clear', help='Clear memory')
    p_clear.add_argument('session_id', help='Session ID')
    p_clear.add_argument('--type', '-t', choices=[m.value for m in MemoryType])

    # context
    p_context = subparsers.add_parser('context', help='Get LLM context')
    p_context.add_argument('session_id', help='Session ID')
    p_context.add_argument('--max-tokens', '-t', type=int, default=500)

    args = parser.parse_args(argv)
    memory = WorkingMemory()

    try:
        if args.command == 'add':
            content = args.content
            if not content:
                content = sys.stdin.read()

            item = memory.add(
                session_id=args.session_id,
                memory_type=MemoryType(args.memory_type),
                content=content,
                importance=Importance(args.importance),
            )

            if item:
                print(json.dumps(item.to_dict(), indent=2, ensure_ascii=False))
            else:
                print("Item was duplicate or empty, not added")

        elif args.command == 'get':
            memory_type = MemoryType(args.type) if args.type else None
            items = memory.get(args.session_id, memory_type, args.limit)

            for item in items:
                print(item.to_context_string())

        elif args.command == 'search':
            items = memory.search(args.session_id, args.query, args.limit)

            for item in items:
                print(f"[{item.relevance_score:.0%}] {item.to_context_string()}")

        elif args.command == 'summarize':
            summary = memory.summarize(args.session_id)

            if args.json:
                print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(summary.to_context_string())

        elif args.command == 'compact':
            memory.compact(args.session_id, args.aggressive)
            print(f"Memory compacted for session {args.session_id}")

        elif args.command == 'clear':
            memory_type = MemoryType(args.type) if args.type else None
            memory.clear(args.session_id, memory_type)
            print(f"Memory cleared for session {args.session_id}")

        elif args.command == 'context':
            context = memory.get_context_for_llm(args.session_id, args.max_tokens)
            print(context)

        else:
            parser.print_help()
            return 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
