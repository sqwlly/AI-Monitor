#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Causal Tracker
因果链追踪器 - 追踪事件之间的因果关系

功能：
1. 事件捕获（命令/输出/文件变更/状态转换）
2. 因果关系推断（时序/内容/逻辑关联）
3. 因果链存储与查询
4. 根因分析与影响预测

Usage:
    python3 causal_tracker.py record <session_id> <event_type> <event_data>
    python3 causal_tracker.py link <cause_event_id> <effect_event_id> [--type temporal]
    python3 causal_tracker.py trace <event_id> [--direction backward|forward]
    python3 causal_tracker.py root-cause <event_id>
    python3 causal_tracker.py impact <event_id>
"""

import argparse
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
from typing import Any, Dict, List, Optional, Set, Tuple

# 数据库路径
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


class EventType(Enum):
    """事件类型"""
    COMMAND = "command"             # 命令执行
    OUTPUT = "output"               # 输出产生
    FILE_CHANGE = "file_change"     # 文件变更
    STATE_CHANGE = "state_change"   # 状态转换
    ERROR = "error"                 # 错误发生
    DECISION = "decision"           # 决策做出
    USER_INPUT = "user_input"       # 用户输入


class LinkType(Enum):
    """因果链接类型"""
    TEMPORAL = "temporal"           # 时序关联（A 发生后 B 发生）
    CONTENT = "content"             # 内容关联（A 的输出在 B 的输入中）
    LOGICAL = "logical"             # 逻辑关联（A 是 B 的前置条件）
    INFERRED = "inferred"           # 推断关联


@dataclass
class CausalEvent:
    """因果事件"""
    event_id: str = ""
    session_id: str = ""
    event_type: EventType = EventType.OUTPUT
    event_data: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    timestamp: int = 0

    def __post_init__(self):
        if not self.event_id:
            self.event_id = str(uuid.uuid4())[:8]
        if not self.timestamp:
            self.timestamp = int(time.time())
        if isinstance(self.event_type, str):
            self.event_type = EventType(self.event_type)

    def to_dict(self) -> Dict:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "event_type": self.event_type.value,
            "event_data": self.event_data,
            "summary": self.summary,
            "timestamp": self.timestamp,
        }

    def to_context_string(self) -> str:
        """生成用于显示的字符串"""
        type_icons = {
            EventType.COMMAND: "⚡",
            EventType.OUTPUT: "📤",
            EventType.FILE_CHANGE: "📝",
            EventType.STATE_CHANGE: "🔄",
            EventType.ERROR: "❌",
            EventType.DECISION: "🎯",
            EventType.USER_INPUT: "👤",
        }
        icon = type_icons.get(self.event_type, "•")
        return f"{icon} [{self.event_id}] {self.summary or self.event_type.value}"


@dataclass
class CausalLink:
    """因果链接"""
    link_id: str = ""
    cause_event_id: str = ""
    effect_event_id: str = ""
    link_type: LinkType = LinkType.TEMPORAL
    confidence: float = 0.5
    evidence: str = ""

    def __post_init__(self):
        if not self.link_id:
            self.link_id = str(uuid.uuid4())[:8]
        if isinstance(self.link_type, str):
            self.link_type = LinkType(self.link_type)

    def to_dict(self) -> Dict:
        return {
            "link_id": self.link_id,
            "cause_event_id": self.cause_event_id,
            "effect_event_id": self.effect_event_id,
            "link_type": self.link_type.value,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass
class CausalChain:
    """因果链"""
    events: List[CausalEvent] = field(default_factory=list)
    links: List[CausalLink] = field(default_factory=list)
    root_event: Optional[CausalEvent] = None
    terminal_event: Optional[CausalEvent] = None

    def to_dict(self) -> Dict:
        return {
            "events": [e.to_dict() for e in self.events],
            "links": [l.to_dict() for l in self.links],
            "root_event": self.root_event.to_dict() if self.root_event else None,
            "terminal_event": self.terminal_event.to_dict() if self.terminal_event else None,
        }

    def to_tree_string(self, indent: int = 0) -> str:
        """生成树形字符串"""
        if not self.events:
            return "Empty chain"

        lines = []
        for i, event in enumerate(self.events):
            prefix = "  " * indent
            if i == 0:
                lines.append(f"{prefix}🌳 {event.to_context_string()}")
            else:
                connector = "├─" if i < len(self.events) - 1 else "└─"
                lines.append(f"{prefix}{connector} {event.to_context_string()}")

        return "\n".join(lines)


class CausalTracker:
    """因果链追踪器"""

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
                CREATE TABLE IF NOT EXISTS causal_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_data TEXT DEFAULT '{}',
                    summary TEXT,
                    timestamp INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_causal_events_session
                ON causal_events(session_id, timestamp DESC)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS causal_links (
                    link_id TEXT PRIMARY KEY,
                    cause_event_id TEXT NOT NULL,
                    effect_event_id TEXT NOT NULL,
                    link_type TEXT NOT NULL,
                    confidence REAL DEFAULT 0.5,
                    evidence TEXT,
                    FOREIGN KEY (cause_event_id) REFERENCES causal_events(event_id),
                    FOREIGN KEY (effect_event_id) REFERENCES causal_events(event_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_causal_links_cause
                ON causal_links(cause_event_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_causal_links_effect
                ON causal_links(effect_event_id)
            """)

    def record_event(self, session_id: str, event_type: EventType,
                    event_data: Dict, summary: str = "") -> CausalEvent:
        """记录事件"""
        event = CausalEvent(
            session_id=session_id,
            event_type=event_type,
            event_data=event_data,
            summary=summary or self._generate_summary(event_type, event_data),
        )

        self._save_event(event)

        # 自动推断与最近事件的因果关系
        self._auto_link(event)

        return event

    def _generate_summary(self, event_type: EventType, event_data: Dict) -> str:
        """生成事件摘要"""
        if event_type == EventType.COMMAND:
            return event_data.get("command", "")[:50]
        elif event_type == EventType.ERROR:
            return event_data.get("message", "")[:50]
        elif event_type == EventType.FILE_CHANGE:
            files = event_data.get("files", [])
            if files:
                return f"Changed {len(files)} files"
            return "File changes"
        elif event_type == EventType.STATE_CHANGE:
            old = event_data.get("old_state", "?")
            new = event_data.get("new_state", "?")
            return f"{old} → {new}"
        else:
            return event_type.value

    def _save_event(self, event: CausalEvent):
        """保存事件"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO causal_events
                (event_id, session_id, event_type, event_data, summary, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                event.event_id,
                event.session_id,
                event.event_type.value,
                json.dumps(event.event_data, ensure_ascii=False),
                event.summary,
                event.timestamp,
            ))

    def _auto_link(self, new_event: CausalEvent):
        """自动推断因果链接"""
        # 获取最近的事件
        recent_events = self._get_recent_events(new_event.session_id, limit=5)

        for event in recent_events:
            if event.event_id == new_event.event_id:
                continue

            # 时序关联（30秒内的事件）
            if new_event.timestamp - event.timestamp < 30:
                link_type = LinkType.TEMPORAL
                confidence = max(0.3, 1.0 - (new_event.timestamp - event.timestamp) / 30)

                # 检测内容关联
                if self._has_content_link(event, new_event):
                    link_type = LinkType.CONTENT
                    confidence = min(1.0, confidence + 0.3)

                # 检测逻辑关联
                logical_link = self._detect_logical_link(event, new_event)
                if logical_link:
                    link_type = LinkType.LOGICAL
                    confidence = min(1.0, confidence + 0.2)

                self.add_link(
                    cause_event_id=event.event_id,
                    effect_event_id=new_event.event_id,
                    link_type=link_type,
                    confidence=confidence,
                )
                break  # 只链接最相关的前一个事件

    def _has_content_link(self, cause: CausalEvent, effect: CausalEvent) -> bool:
        """检测内容关联"""
        cause_output = json.dumps(cause.event_data)
        effect_input = json.dumps(effect.event_data)

        # 简单的内容重叠检测
        cause_words = set(re.findall(r'\w+', cause_output.lower()))
        effect_words = set(re.findall(r'\w+', effect_input.lower()))

        if cause_words and effect_words:
            overlap = len(cause_words & effect_words) / len(cause_words)
            return overlap > 0.3

        return False

    def _detect_logical_link(self, cause: CausalEvent, effect: CausalEvent) -> bool:
        """检测逻辑关联"""
        # 命令 -> 输出
        if cause.event_type == EventType.COMMAND and effect.event_type == EventType.OUTPUT:
            return True

        # 命令 -> 错误
        if cause.event_type == EventType.COMMAND and effect.event_type == EventType.ERROR:
            return True

        # 文件变更 -> 状态变化
        if cause.event_type == EventType.FILE_CHANGE and effect.event_type == EventType.STATE_CHANGE:
            return True

        # 用户输入 -> 决策
        if cause.event_type == EventType.USER_INPUT and effect.event_type == EventType.DECISION:
            return True

        return False

    def add_link(self, cause_event_id: str, effect_event_id: str,
                link_type: LinkType = LinkType.TEMPORAL, confidence: float = 0.5,
                evidence: str = "") -> CausalLink:
        """添加因果链接"""
        link = CausalLink(
            cause_event_id=cause_event_id,
            effect_event_id=effect_event_id,
            link_type=link_type,
            confidence=confidence,
            evidence=evidence,
        )

        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO causal_links
                (link_id, cause_event_id, effect_event_id, link_type, confidence, evidence)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                link.link_id,
                link.cause_event_id,
                link.effect_event_id,
                link.link_type.value,
                link.confidence,
                link.evidence,
            ))

        return link

    def get_event(self, event_id: str) -> Optional[CausalEvent]:
        """获取事件"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM causal_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if row:
                return self._row_to_event(row)
        return None

    def _get_recent_events(self, session_id: str, limit: int = 10) -> List[CausalEvent]:
        """获取最近的事件"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM causal_events
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (session_id, limit)).fetchall()
            return [self._row_to_event(row) for row in rows]

    def trace_backward(self, event_id: str, max_depth: int = 10) -> CausalChain:
        """向后追踪因果链（找根因）"""
        visited = set()
        events = []
        links = []

        def trace(eid: str, depth: int):
            if depth > max_depth or eid in visited:
                return
            visited.add(eid)

            event = self.get_event(eid)
            if event:
                events.insert(0, event)

            # 获取指向此事件的链接
            with self._get_conn() as conn:
                rows = conn.execute("""
                    SELECT * FROM causal_links
                    WHERE effect_event_id = ?
                    ORDER BY confidence DESC
                """, (eid,)).fetchall()

                for row in rows:
                    link = self._row_to_link(row)
                    links.insert(0, link)
                    trace(link.cause_event_id, depth + 1)

        trace(event_id, 0)

        return CausalChain(
            events=events,
            links=links,
            root_event=events[0] if events else None,
            terminal_event=events[-1] if events else None,
        )

    def trace_forward(self, event_id: str, max_depth: int = 10) -> CausalChain:
        """向前追踪因果链（找影响）"""
        visited = set()
        events = []
        links = []

        def trace(eid: str, depth: int):
            if depth > max_depth or eid in visited:
                return
            visited.add(eid)

            event = self.get_event(eid)
            if event:
                events.append(event)

            # 获取从此事件出发的链接
            with self._get_conn() as conn:
                rows = conn.execute("""
                    SELECT * FROM causal_links
                    WHERE cause_event_id = ?
                    ORDER BY confidence DESC
                """, (eid,)).fetchall()

                for row in rows:
                    link = self._row_to_link(row)
                    links.append(link)
                    trace(link.effect_event_id, depth + 1)

        trace(event_id, 0)

        return CausalChain(
            events=events,
            links=links,
            root_event=events[0] if events else None,
            terminal_event=events[-1] if events else None,
        )

    def find_root_cause(self, event_id: str) -> Optional[CausalEvent]:
        """找到根本原因"""
        chain = self.trace_backward(event_id)
        return chain.root_event

    def predict_impact(self, event_id: str) -> List[CausalEvent]:
        """预测影响范围"""
        chain = self.trace_forward(event_id)
        return chain.events[1:] if len(chain.events) > 1 else []

    def get_rollback_path(self, event_id: str) -> List[CausalEvent]:
        """获取回滚路径（需要撤销的事件）"""
        chain = self.trace_backward(event_id)

        # 找出所有可回滚的事件（命令、文件变更）
        rollback_events = []
        for event in chain.events:
            if event.event_type in [EventType.COMMAND, EventType.FILE_CHANGE]:
                rollback_events.append(event)

        return rollback_events

    def get_session_timeline(self, session_id: str, limit: int = 50) -> List[CausalEvent]:
        """获取会话时间线"""
        return self._get_recent_events(session_id, limit)

    def get_summary(self, session_id: str) -> str:
        """获取因果摘要（用于 LLM 上下文）"""
        events = self._get_recent_events(session_id, limit=5)
        if not events:
            return ""

        lines = ["[causal] 最近事件:"]
        for event in events:
            lines.append(f"  {event.to_context_string()}")

        return "\n".join(lines)

    def _row_to_event(self, row) -> CausalEvent:
        """将数据库行转换为 CausalEvent"""
        return CausalEvent(
            event_id=row['event_id'],
            session_id=row['session_id'],
            event_type=EventType(row['event_type']),
            event_data=json.loads(row['event_data'] or '{}'),
            summary=row['summary'] or "",
            timestamp=row['timestamp'],
        )

    def _row_to_link(self, row) -> CausalLink:
        """将数据库行转换为 CausalLink"""
        return CausalLink(
            link_id=row['link_id'],
            cause_event_id=row['cause_event_id'],
            effect_event_id=row['effect_event_id'],
            link_type=LinkType(row['link_type']),
            confidence=row['confidence'] or 0.5,
            evidence=row['evidence'] or "",
        )


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Causal Tracker',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # record
    p_record = subparsers.add_parser('record', help='Record an event')
    p_record.add_argument('session_id', help='Session ID')
    p_record.add_argument('event_type', choices=[e.value for e in EventType])
    p_record.add_argument('event_data', help='Event data as JSON string')
    p_record.add_argument('--summary', '-s', default='', help='Event summary')

    # link
    p_link = subparsers.add_parser('link', help='Add a causal link')
    p_link.add_argument('cause_event_id', help='Cause event ID')
    p_link.add_argument('effect_event_id', help='Effect event ID')
    p_link.add_argument('--type', '-t', choices=[l.value for l in LinkType],
                       default='temporal', help='Link type')
    p_link.add_argument('--confidence', '-c', type=float, default=0.5)

    # trace
    p_trace = subparsers.add_parser('trace', help='Trace causal chain')
    p_trace.add_argument('event_id', help='Event ID to trace from')
    p_trace.add_argument('--direction', '-d', choices=['backward', 'forward'],
                        default='backward', help='Trace direction')
    p_trace.add_argument('--depth', type=int, default=10)

    # root-cause
    p_root = subparsers.add_parser('root-cause', help='Find root cause')
    p_root.add_argument('event_id', help='Event ID')

    # impact
    p_impact = subparsers.add_parser('impact', help='Predict impact')
    p_impact.add_argument('event_id', help='Event ID')

    # timeline
    p_timeline = subparsers.add_parser('timeline', help='Show session timeline')
    p_timeline.add_argument('session_id', help='Session ID')
    p_timeline.add_argument('--limit', '-l', type=int, default=20)

    # summary
    p_summary = subparsers.add_parser('summary', help='Get causal summary')
    p_summary.add_argument('session_id', help='Session ID')

    args = parser.parse_args(argv)
    tracker = CausalTracker()

    try:
        if args.command == 'record':
            try:
                event_data = json.loads(args.event_data)
            except json.JSONDecodeError:
                event_data = {"raw": args.event_data}

            event = tracker.record_event(
                session_id=args.session_id,
                event_type=EventType(args.event_type),
                event_data=event_data,
                summary=args.summary,
            )
            print(json.dumps(event.to_dict(), indent=2, ensure_ascii=False))

        elif args.command == 'link':
            link = tracker.add_link(
                cause_event_id=args.cause_event_id,
                effect_event_id=args.effect_event_id,
                link_type=LinkType(args.type),
                confidence=args.confidence,
            )
            print(f"Link created: {link.cause_event_id} → {link.effect_event_id}")

        elif args.command == 'trace':
            if args.direction == 'backward':
                chain = tracker.trace_backward(args.event_id, args.depth)
            else:
                chain = tracker.trace_forward(args.event_id, args.depth)

            print(chain.to_tree_string())

        elif args.command == 'root-cause':
            root = tracker.find_root_cause(args.event_id)
            if root:
                print(f"Root cause: {root.to_context_string()}")
                print(json.dumps(root.to_dict(), indent=2, ensure_ascii=False))
            else:
                print("No root cause found")

        elif args.command == 'impact':
            impact = tracker.predict_impact(args.event_id)
            if impact:
                print("Predicted impact:")
                for event in impact:
                    print(f"  - {event.to_context_string()}")
            else:
                print("No predicted impact")

        elif args.command == 'timeline':
            events = tracker.get_session_timeline(args.session_id, args.limit)
            for event in events:
                print(event.to_context_string())

        elif args.command == 'summary':
            summary = tracker.get_summary(args.session_id)
            if summary:
                print(summary)
            else:
                print("No events recorded")

        else:
            parser.print_help()
            return 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
