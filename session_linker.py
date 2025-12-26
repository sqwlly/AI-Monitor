#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Session Linker
跨会话关联器 - 关联和迁移跨会话的知识

功能：
1. 会话相似度计算（基于项目/意图/错误模式）
2. 知识迁移（经验复用/策略迁移）
3. 项目知识库（经验积累/最佳实践）
4. 跨项目学习（通用模式/框架经验）

Usage:
    python3 session_linker.py find-similar <session_id> [--limit 5]
    python3 session_linker.py transfer <from_session> <to_session>
    python3 session_linker.py learn <session_id>
    python3 session_linker.py recommend <session_id>
    python3 session_linker.py knowledge <project_path>
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
from typing import Any, Dict, List, Optional, Set, Tuple

# 数据库路径
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


class KnowledgeType(Enum):
    """知识类型"""
    ERROR_FIX = "error_fix"             # 错误修复经验
    STRATEGY = "strategy"               # 成功策略
    PATTERN = "pattern"                 # 通用模式
    BEST_PRACTICE = "best_practice"     # 最佳实践
    PITFALL = "pitfall"                 # 常见陷阱


@dataclass
class SessionInfo:
    """会话信息"""
    session_id: str
    project_path: str = ""
    project_name: str = ""
    language: str = ""
    framework: str = ""
    intent_summary: str = ""
    error_signatures: List[str] = field(default_factory=list)
    success_patterns: List[str] = field(default_factory=list)
    created_at: int = 0
    completed_at: Optional[int] = None
    outcome: str = "unknown"  # success/partial/failed/abandoned

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "project_path": self.project_path,
            "project_name": self.project_name,
            "language": self.language,
            "framework": self.framework,
            "intent_summary": self.intent_summary,
            "error_signatures": self.error_signatures,
            "success_patterns": self.success_patterns,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "outcome": self.outcome,
        }


@dataclass
class Knowledge:
    """知识项"""
    knowledge_id: str = ""
    knowledge_type: KnowledgeType = KnowledgeType.PATTERN
    title: str = ""
    content: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    project_path: Optional[str] = None
    language: Optional[str] = None
    framework: Optional[str] = None
    source_sessions: List[str] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    confidence: float = 0.5
    created_at: int = 0
    last_used_at: int = 0

    def __post_init__(self):
        if not self.knowledge_id:
            self.knowledge_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = int(time.time())
        if isinstance(self.knowledge_type, str):
            self.knowledge_type = KnowledgeType(self.knowledge_type)

    def to_dict(self) -> Dict:
        return {
            "knowledge_id": self.knowledge_id,
            "knowledge_type": self.knowledge_type.value,
            "title": self.title,
            "content": self.content,
            "context": self.context,
            "project_path": self.project_path,
            "language": self.language,
            "framework": self.framework,
            "source_sessions": self.source_sessions,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }

    def to_context_string(self) -> str:
        """生成用于显示的字符串"""
        type_icons = {
            KnowledgeType.ERROR_FIX: "🔧",
            KnowledgeType.STRATEGY: "📋",
            KnowledgeType.PATTERN: "🔄",
            KnowledgeType.BEST_PRACTICE: "✨",
            KnowledgeType.PITFALL: "⚠️",
        }
        icon = type_icons.get(self.knowledge_type, "•")
        return f"{icon} [{self.confidence:.0%}] {self.title}"


@dataclass
class SimilarSession:
    """相似会话"""
    session_info: SessionInfo
    similarity_score: float
    matching_aspects: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "session": self.session_info.to_dict(),
            "similarity_score": self.similarity_score,
            "matching_aspects": self.matching_aspects,
        }


class SessionLinker:
    """跨会话关联器"""

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
            # 会话信息表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_info (
                    session_id TEXT PRIMARY KEY,
                    project_path TEXT,
                    project_name TEXT,
                    language TEXT,
                    framework TEXT,
                    intent_summary TEXT,
                    error_signatures TEXT DEFAULT '[]',
                    success_patterns TEXT DEFAULT '[]',
                    created_at INTEGER,
                    completed_at INTEGER,
                    outcome TEXT DEFAULT 'unknown'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_project
                ON session_info(project_path)
            """)

            # 知识库表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_base (
                    knowledge_id TEXT PRIMARY KEY,
                    knowledge_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT,
                    context TEXT DEFAULT '{}',
                    project_path TEXT,
                    language TEXT,
                    framework TEXT,
                    source_sessions TEXT DEFAULT '[]',
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    confidence REAL DEFAULT 0.5,
                    created_at INTEGER,
                    last_used_at INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_type
                ON knowledge_base(knowledge_type)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_project
                ON knowledge_base(project_path)
            """)

    def register_session(self, session_id: str, project_path: str = "",
                        language: str = "", framework: str = "",
                        intent_summary: str = "") -> SessionInfo:
        """注册会话信息"""
        project_name = Path(project_path).name if project_path else ""

        info = SessionInfo(
            session_id=session_id,
            project_path=project_path,
            project_name=project_name,
            language=language,
            framework=framework,
            intent_summary=intent_summary,
            created_at=int(time.time()),
        )

        self._save_session_info(info)
        return info

    def _save_session_info(self, info: SessionInfo):
        """保存会话信息"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO session_info
                (session_id, project_path, project_name, language, framework,
                 intent_summary, error_signatures, success_patterns,
                 created_at, completed_at, outcome)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                info.session_id,
                info.project_path,
                info.project_name,
                info.language,
                info.framework,
                info.intent_summary,
                json.dumps(info.error_signatures, ensure_ascii=False),
                json.dumps(info.success_patterns, ensure_ascii=False),
                info.created_at,
                info.completed_at,
                info.outcome,
            ))

    def update_session_outcome(self, session_id: str, outcome: str,
                              error_signatures: List[str] = None,
                              success_patterns: List[str] = None):
        """更新会话结果"""
        with self._get_conn() as conn:
            updates = ["outcome = ?", "completed_at = ?"]
            values = [outcome, int(time.time())]

            if error_signatures is not None:
                updates.append("error_signatures = ?")
                values.append(json.dumps(error_signatures, ensure_ascii=False))

            if success_patterns is not None:
                updates.append("success_patterns = ?")
                values.append(json.dumps(success_patterns, ensure_ascii=False))

            values.append(session_id)

            conn.execute(f"""
                UPDATE session_info
                SET {', '.join(updates)}
                WHERE session_id = ?
            """, values)

    def find_similar_sessions(self, session_id: str, limit: int = 5) -> List[SimilarSession]:
        """找到相似的会话"""
        current = self._get_session_info(session_id)
        if not current:
            return []

        all_sessions = self._get_all_sessions(exclude_id=session_id)
        similarities = []

        for other in all_sessions:
            score, aspects = self._calculate_similarity(current, other)
            if score > 0.1:
                similarities.append(SimilarSession(
                    session_info=other,
                    similarity_score=score,
                    matching_aspects=aspects,
                ))

        # 按相似度排序
        similarities.sort(key=lambda x: -x.similarity_score)

        return similarities[:limit]

    def _calculate_similarity(self, a: SessionInfo, b: SessionInfo) -> Tuple[float, List[str]]:
        """计算两个会话的相似度"""
        score = 0.0
        aspects = []

        # 同一项目（最高权重）
        if a.project_path and a.project_path == b.project_path:
            score += 0.4
            aspects.append("same_project")

        # 同一项目名
        elif a.project_name and a.project_name == b.project_name:
            score += 0.2
            aspects.append("same_project_name")

        # 同一语言
        if a.language and a.language == b.language:
            score += 0.15
            aspects.append("same_language")

        # 同一框架
        if a.framework and a.framework == b.framework:
            score += 0.15
            aspects.append("same_framework")

        # 相似的意图
        if a.intent_summary and b.intent_summary:
            intent_sim = self._text_similarity(a.intent_summary, b.intent_summary)
            if intent_sim > 0.3:
                score += intent_sim * 0.2
                aspects.append("similar_intent")

        # 相似的错误签名
        if a.error_signatures and b.error_signatures:
            common_errors = set(a.error_signatures) & set(b.error_signatures)
            if common_errors:
                score += min(0.2, len(common_errors) * 0.05)
                aspects.append("similar_errors")

        return min(1.0, score), aspects

    def _text_similarity(self, a: str, b: str) -> float:
        """计算文本相似度"""
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())

        if not words_a or not words_b:
            return 0.0

        intersection = len(words_a & words_b)
        union = len(words_a | words_b)

        return intersection / union if union > 0 else 0.0

    def transfer_knowledge(self, from_session: str, to_session: str) -> List[Knowledge]:
        """从一个会话迁移知识到另一个会话"""
        from_info = self._get_session_info(from_session)
        to_info = self._get_session_info(to_session)

        if not from_info or not to_info:
            return []

        # 获取来源会话的知识
        knowledge = self._get_session_knowledge(from_session)

        # 过滤适用的知识
        applicable = []
        for k in knowledge:
            if self._is_knowledge_applicable(k, to_info):
                applicable.append(k)

        return applicable

    def _is_knowledge_applicable(self, knowledge: Knowledge, target: SessionInfo) -> bool:
        """判断知识是否适用于目标会话"""
        # 项目级知识只适用于同一项目
        if knowledge.project_path:
            if knowledge.project_path != target.project_path:
                return False

        # 语言特定知识
        if knowledge.language:
            if knowledge.language != target.language:
                return False

        # 框架特定知识
        if knowledge.framework:
            if knowledge.framework != target.framework:
                return False

        return True

    def learn_from_session(self, session_id: str) -> List[Knowledge]:
        """从会话中学习知识"""
        info = self._get_session_info(session_id)
        if not info:
            return []

        learned = []

        # 如果会话成功，提取成功策略
        if info.outcome == "success":
            for pattern in info.success_patterns:
                k = self._create_or_update_knowledge(
                    knowledge_type=KnowledgeType.STRATEGY,
                    title=f"成功策略: {pattern[:50]}",
                    content=pattern,
                    context={"intent": info.intent_summary},
                    project_path=info.project_path,
                    language=info.language,
                    framework=info.framework,
                    source_session=session_id,
                    is_success=True,
                )
                learned.append(k)

        # 提取错误修复经验
        for sig in info.error_signatures:
            # 检查是否有对应的成功修复
            if info.outcome == "success":
                k = self._create_or_update_knowledge(
                    knowledge_type=KnowledgeType.ERROR_FIX,
                    title=f"错误修复: {sig[:50]}",
                    content=f"错误签名: {sig}\n会话结果: 成功修复",
                    context={"error_signature": sig},
                    project_path=info.project_path,
                    language=info.language,
                    framework=info.framework,
                    source_session=session_id,
                    is_success=True,
                )
                learned.append(k)

        return learned

    def _create_or_update_knowledge(self, knowledge_type: KnowledgeType,
                                   title: str, content: str, context: Dict,
                                   project_path: str = None, language: str = None,
                                   framework: str = None, source_session: str = None,
                                   is_success: bool = True) -> Knowledge:
        """创建或更新知识项"""
        # 查找现有知识
        existing = self._find_similar_knowledge(title, content, project_path)

        if existing:
            # 更新现有知识
            if source_session and source_session not in existing.source_sessions:
                existing.source_sessions.append(source_session)

            if is_success:
                existing.success_count += 1
            else:
                existing.failure_count += 1

            # 重新计算置信度
            total = existing.success_count + existing.failure_count
            existing.confidence = existing.success_count / total if total > 0 else 0.5

            existing.last_used_at = int(time.time())
            self._save_knowledge(existing)
            return existing

        else:
            # 创建新知识
            k = Knowledge(
                knowledge_type=knowledge_type,
                title=title,
                content=content,
                context=context,
                project_path=project_path,
                language=language,
                framework=framework,
                source_sessions=[source_session] if source_session else [],
                success_count=1 if is_success else 0,
                failure_count=0 if is_success else 1,
                confidence=0.5,
            )
            self._save_knowledge(k)
            return k

    def _find_similar_knowledge(self, title: str, content: str,
                               project_path: str = None) -> Optional[Knowledge]:
        """查找相似的知识"""
        with self._get_conn() as conn:
            if project_path:
                rows = conn.execute("""
                    SELECT * FROM knowledge_base
                    WHERE project_path = ?
                    ORDER BY confidence DESC
                    LIMIT 50
                """, (project_path,)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM knowledge_base
                    ORDER BY confidence DESC
                    LIMIT 50
                """).fetchall()

            for row in rows:
                k = self._row_to_knowledge(row)
                # 简单的相似度检查
                if (self._text_similarity(title, k.title) > 0.7 or
                    self._text_similarity(content, k.content) > 0.7):
                    return k

        return None

    def _save_knowledge(self, knowledge: Knowledge):
        """保存知识"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO knowledge_base
                (knowledge_id, knowledge_type, title, content, context,
                 project_path, language, framework, source_sessions,
                 success_count, failure_count, confidence, created_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                knowledge.knowledge_id,
                knowledge.knowledge_type.value,
                knowledge.title,
                knowledge.content,
                json.dumps(knowledge.context, ensure_ascii=False),
                knowledge.project_path,
                knowledge.language,
                knowledge.framework,
                json.dumps(knowledge.source_sessions, ensure_ascii=False),
                knowledge.success_count,
                knowledge.failure_count,
                knowledge.confidence,
                knowledge.created_at,
                knowledge.last_used_at,
            ))

    def get_recommendations(self, session_id: str, limit: int = 5) -> List[Knowledge]:
        """获取推荐的知识"""
        info = self._get_session_info(session_id)
        if not info:
            return []

        # 获取适用的知识
        with self._get_conn() as conn:
            # 优先获取同一项目的知识
            rows = conn.execute("""
                SELECT * FROM knowledge_base
                WHERE (project_path = ? OR project_path IS NULL)
                AND (language = ? OR language IS NULL)
                AND confidence >= 0.3
                ORDER BY confidence DESC, last_used_at DESC
                LIMIT ?
            """, (info.project_path, info.language, limit * 2)).fetchall()

            knowledge = [self._row_to_knowledge(row) for row in rows]

        # 过滤和排序
        applicable = [k for k in knowledge if self._is_knowledge_applicable(k, info)]

        return applicable[:limit]

    def get_project_knowledge(self, project_path: str, limit: int = 20) -> List[Knowledge]:
        """获取项目知识库"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM knowledge_base
                WHERE project_path = ?
                ORDER BY confidence DESC, success_count DESC
                LIMIT ?
            """, (project_path, limit)).fetchall()

            return [self._row_to_knowledge(row) for row in rows]

    def get_summary(self, session_id: str) -> str:
        """获取跨会话知识摘要"""
        recommendations = self.get_recommendations(session_id, limit=3)

        if not recommendations:
            return ""

        lines = ["[knowledge] 相关经验:"]
        for k in recommendations:
            lines.append(f"  {k.to_context_string()}")

        return "\n".join(lines)

    def _get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """获取会话信息"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM session_info WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row:
                return self._row_to_session_info(row)
        return None

    def _get_all_sessions(self, exclude_id: str = None, limit: int = 100) -> List[SessionInfo]:
        """获取所有会话"""
        with self._get_conn() as conn:
            if exclude_id:
                rows = conn.execute("""
                    SELECT * FROM session_info
                    WHERE session_id != ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (exclude_id, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM session_info
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,)).fetchall()

            return [self._row_to_session_info(row) for row in rows]

    def _get_session_knowledge(self, session_id: str) -> List[Knowledge]:
        """获取与会话相关的知识"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM knowledge_base
                WHERE source_sessions LIKE ?
                ORDER BY confidence DESC
            """, (f'%{session_id}%',)).fetchall()

            return [self._row_to_knowledge(row) for row in rows]

    def _row_to_session_info(self, row) -> SessionInfo:
        """将数据库行转换为 SessionInfo"""
        return SessionInfo(
            session_id=row['session_id'],
            project_path=row['project_path'] or "",
            project_name=row['project_name'] or "",
            language=row['language'] or "",
            framework=row['framework'] or "",
            intent_summary=row['intent_summary'] or "",
            error_signatures=json.loads(row['error_signatures'] or '[]'),
            success_patterns=json.loads(row['success_patterns'] or '[]'),
            created_at=row['created_at'],
            completed_at=row['completed_at'],
            outcome=row['outcome'] or "unknown",
        )

    def _row_to_knowledge(self, row) -> Knowledge:
        """将数据库行转换为 Knowledge"""
        return Knowledge(
            knowledge_id=row['knowledge_id'],
            knowledge_type=KnowledgeType(row['knowledge_type']),
            title=row['title'],
            content=row['content'] or "",
            context=json.loads(row['context'] or '{}'),
            project_path=row['project_path'],
            language=row['language'],
            framework=row['framework'],
            source_sessions=json.loads(row['source_sessions'] or '[]'),
            success_count=row['success_count'] or 0,
            failure_count=row['failure_count'] or 0,
            confidence=row['confidence'] or 0.5,
            created_at=row['created_at'],
            last_used_at=row['last_used_at'],
        )


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Session Linker',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # register
    p_register = subparsers.add_parser('register', help='Register a session')
    p_register.add_argument('session_id', help='Session ID')
    p_register.add_argument('--project', '-p', default='', help='Project path')
    p_register.add_argument('--language', '-l', default='', help='Language')
    p_register.add_argument('--framework', '-f', default='', help='Framework')
    p_register.add_argument('--intent', '-i', default='', help='Intent summary')

    # find-similar
    p_similar = subparsers.add_parser('find-similar', help='Find similar sessions')
    p_similar.add_argument('session_id', help='Session ID')
    p_similar.add_argument('--limit', '-l', type=int, default=5)

    # transfer
    p_transfer = subparsers.add_parser('transfer', help='Transfer knowledge between sessions')
    p_transfer.add_argument('from_session', help='Source session ID')
    p_transfer.add_argument('to_session', help='Target session ID')

    # learn
    p_learn = subparsers.add_parser('learn', help='Learn from a session')
    p_learn.add_argument('session_id', help='Session ID')

    # recommend
    p_recommend = subparsers.add_parser('recommend', help='Get knowledge recommendations')
    p_recommend.add_argument('session_id', help='Session ID')
    p_recommend.add_argument('--limit', '-l', type=int, default=5)

    # knowledge
    p_knowledge = subparsers.add_parser('knowledge', help='Get project knowledge')
    p_knowledge.add_argument('project_path', help='Project path')
    p_knowledge.add_argument('--limit', '-l', type=int, default=20)

    # summary
    p_summary = subparsers.add_parser('summary', help='Get knowledge summary')
    p_summary.add_argument('session_id', help='Session ID')

    args = parser.parse_args(argv)
    linker = SessionLinker()

    try:
        if args.command == 'register':
            info = linker.register_session(
                session_id=args.session_id,
                project_path=args.project,
                language=args.language,
                framework=args.framework,
                intent_summary=args.intent,
            )
            print(json.dumps(info.to_dict(), indent=2, ensure_ascii=False))

        elif args.command == 'find-similar':
            similar = linker.find_similar_sessions(args.session_id, args.limit)

            if similar:
                for s in similar:
                    aspects = ", ".join(s.matching_aspects)
                    print(f"[{s.similarity_score:.0%}] {s.session_info.session_id} ({aspects})")
            else:
                print("No similar sessions found")

        elif args.command == 'transfer':
            knowledge = linker.transfer_knowledge(args.from_session, args.to_session)

            if knowledge:
                print(f"Transferable knowledge ({len(knowledge)} items):")
                for k in knowledge:
                    print(f"  {k.to_context_string()}")
            else:
                print("No applicable knowledge to transfer")

        elif args.command == 'learn':
            learned = linker.learn_from_session(args.session_id)

            if learned:
                print(f"Learned {len(learned)} knowledge items:")
                for k in learned:
                    print(f"  {k.to_context_string()}")
            else:
                print("No knowledge learned from this session")

        elif args.command == 'recommend':
            recommendations = linker.get_recommendations(args.session_id, args.limit)

            if recommendations:
                print("Recommended knowledge:")
                for k in recommendations:
                    print(f"  {k.to_context_string()}")
            else:
                print("No recommendations available")

        elif args.command == 'knowledge':
            knowledge = linker.get_project_knowledge(args.project_path, args.limit)

            if knowledge:
                print(f"Project knowledge ({len(knowledge)} items):")
                for k in knowledge:
                    print(f"  {k.to_context_string()}")
            else:
                print("No knowledge for this project")

        elif args.command == 'summary':
            summary = linker.get_summary(args.session_id)
            if summary:
                print(summary)
            else:
                print("No knowledge summary available")

        else:
            parser.print_help()
            return 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
