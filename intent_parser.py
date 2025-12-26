#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Intent Parser
意图解析器 - 从对话/输出中提取用户意图

功能：
1. 捕获任务声明："我要实现..."、"帮我..."、"请..."
2. 从 git commit message 提取意图
3. 从 TODO 注释提取意图
4. 结构化存储意图

Usage:
    python3 intent_parser.py detect <session_id> <text>
    python3 intent_parser.py get <session_id>
    python3 intent_parser.py list <session_id>
    python3 intent_parser.py update <intent_id> <status>
    python3 intent_parser.py summary <session_id>
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

# 数据库路径
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))

# 意图动作类型
ACTION_TYPES = {
    "implement": ["实现", "开发", "写", "创建", "新增", "添加", "implement", "create", "add", "develop", "write"],
    "fix": ["修复", "修", "解决", "处理", "fix", "solve", "resolve", "debug", "repair"],
    "refactor": ["重构", "优化", "改进", "整理", "refactor", "optimize", "improve", "clean"],
    "test": ["测试", "验证", "检查", "test", "verify", "check", "validate"],
    "deploy": ["部署", "发布", "上线", "deploy", "release", "publish", "ship"],
    "config": ["配置", "设置", "config", "setup", "configure"],
    "doc": ["文档", "注释", "说明", "document", "comment", "explain"],
    "review": ["审查", "检视", "review", "inspect"],
    "investigate": ["调查", "分析", "研究", "investigate", "analyze", "research", "explore"],
}

# 意图触发模式
INTENT_PATTERNS = [
    # 中文模式
    (r"(?:我要|我想|请|帮我|需要|希望)[\s]*(.+?)(?:。|$|\n)", "zh_request"),
    (r"(?:实现|开发|创建|添加|修复|重构|测试|部署)[\s]*(.+?)(?:。|$|\n)", "zh_action"),
    (r"目标[是：:]\s*(.+?)(?:。|$|\n)", "zh_goal"),
    (r"任务[是：:]\s*(.+?)(?:。|$|\n)", "zh_task"),
    # 英文模式
    (r"(?:I want to|I need to|Please|Help me|Let's)\s+(.+?)(?:\.|$|\n)", "en_request"),
    (r"(?:implement|create|add|fix|refactor|test|deploy)\s+(.+?)(?:\.|$|\n)", "en_action"),
    (r"Goal:\s*(.+?)(?:\.|$|\n)", "en_goal"),
    (r"Task:\s*(.+?)(?:\.|$|\n)", "en_task"),
    # Git commit 模式
    (r"^(feat|fix|refactor|test|docs|chore|style|perf)[\(:](.+?)[\):]?\s*(.+?)$", "git_commit"),
]

# 成功标准关键词
SUCCESS_KEYWORDS = {
    "test_pass": ["测试通过", "test pass", "tests pass", "all tests", "✓", "✔"],
    "build_success": ["构建成功", "build success", "build passed", "compiled"],
    "deploy_success": ["部署成功", "deploy success", "deployed", "published"],
    "no_error": ["无错误", "no error", "0 errors", "error free"],
}


class Intent:
    """意图数据结构"""

    def __init__(self, intent_id=None, session_id=None, raw_text="",
                 action="unknown", target="", constraints=None,
                 success_criteria=None, status="active", confidence=0.5,
                 source="unknown", created_at=None, completed_at=None, **kwargs):
        self.intent_id = intent_id or str(uuid.uuid4())[:8]
        self.session_id = session_id
        self.raw_text = raw_text
        self.action = action
        self.target = target
        self.constraints = constraints or {}
        self.success_criteria = success_criteria or []
        self.status = status  # active/completed/abandoned/superseded
        self.confidence = confidence
        self.source = source  # user_input/git_commit/todo_comment/inferred
        self.created_at = created_at or int(time.time())
        self.completed_at = completed_at

    def to_dict(self):
        return {
            "intent_id": self.intent_id,
            "session_id": self.session_id,
            "raw_text": self.raw_text,
            "action": self.action,
            "target": self.target,
            "constraints": self.constraints,
            "success_criteria": self.success_criteria,
            "status": self.status,
            "confidence": self.confidence,
            "source": self.source,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    def to_context_string(self):
        """生成用于 LLM 上下文的字符串"""
        action_zh = {
            "implement": "实现",
            "fix": "修复",
            "refactor": "重构",
            "test": "测试",
            "deploy": "部署",
            "config": "配置",
            "doc": "文档",
            "review": "审查",
            "investigate": "调查",
            "unknown": "处理",
        }.get(self.action, self.action)

        result = f"[intent] 当前目标: {action_zh} {self.target}"
        if self.constraints:
            constraints_str = ", ".join(f"{k}={v}" for k, v in self.constraints.items())
            result += f" (约束: {constraints_str})"
        if self.success_criteria:
            result += f" | 成功标准: {', '.join(self.success_criteria[:2])}"
        result += f" | 置信度: {self.confidence:.0%}"
        return result


class IntentParser:
    """意图解析器"""

    def __init__(self, db_path=None):
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
                CREATE TABLE IF NOT EXISTS intents (
                    intent_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    raw_text TEXT,
                    action TEXT,
                    target TEXT,
                    constraints TEXT,
                    success_criteria TEXT,
                    status TEXT DEFAULT 'active',
                    confidence REAL DEFAULT 0.5,
                    source TEXT DEFAULT 'unknown',
                    created_at INTEGER,
                    completed_at INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_intents_session
                ON intents(session_id, status)
            """)

    def detect_intent(self, session_id, text):
        """从文本中检测意图"""
        if not text or not text.strip():
            return None

        text = text.strip()
        detected_intents = []

        # 尝试各种模式匹配
        for pattern, source_type in INTENT_PATTERNS:
            matches = re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                intent = self._parse_match(match, source_type, text)
                if intent and intent.confidence >= 0.3:
                    detected_intents.append(intent)

        # 如果没有匹配，尝试启发式检测
        if not detected_intents:
            intent = self._heuristic_detect(text)
            if intent:
                detected_intents.append(intent)

        # 选择置信度最高的意图
        if detected_intents:
            best_intent = max(detected_intents, key=lambda x: x.confidence)
            best_intent.session_id = session_id

            # 检查是否与现有意图重复
            existing = self.get_active_intent(session_id)
            if existing and self._is_similar_intent(existing, best_intent):
                # 更新置信度但不创建新意图
                return existing

            # 保存新意图
            self._save_intent(best_intent)
            return best_intent

        return None

    def _parse_match(self, match, source_type, full_text):
        """解析正则匹配结果"""
        if source_type == "git_commit":
            # Git commit 格式: type(scope): description
            groups = match.groups()
            if len(groups) >= 3:
                commit_type, scope, description = groups[0], groups[1], groups[2]
                action = self._map_git_type_to_action(commit_type)
                return Intent(
                    raw_text=match.group(0),
                    action=action,
                    target=f"{scope}: {description}".strip(": "),
                    source="git_commit",
                    confidence=0.8,
                )
        else:
            # 其他模式
            captured = match.group(1).strip() if match.lastindex >= 1 else ""
            if len(captured) < 3:
                return None

            action, target = self._extract_action_target(captured)
            confidence = 0.6 if source_type.endswith("_request") else 0.5

            return Intent(
                raw_text=captured,
                action=action,
                target=target,
                source="user_input" if "request" in source_type else "inferred",
                confidence=confidence,
            )

        return None

    def _extract_action_target(self, text):
        """从文本中提取动作和目标"""
        text = text.strip()

        # 尝试匹配动作关键词
        for action, keywords in ACTION_TYPES.items():
            for kw in keywords:
                if text.lower().startswith(kw.lower()):
                    target = text[len(kw):].strip()
                    # 清理目标文本
                    target = re.sub(r'^[：:\s]+', '', target)
                    return action, target

                # 检查是否包含关键词
                pattern = rf'\b{re.escape(kw)}\b'
                if re.search(pattern, text, re.IGNORECASE):
                    # 提取关键词后面的部分作为目标
                    parts = re.split(pattern, text, maxsplit=1, flags=re.IGNORECASE)
                    if len(parts) > 1:
                        target = parts[1].strip()
                        target = re.sub(r'^[：:\s]+', '', target)
                        if target:
                            return action, target

        # 无法识别动作，整体作为目标
        return "unknown", text

    def _map_git_type_to_action(self, git_type):
        """将 git commit type 映射到 action"""
        mapping = {
            "feat": "implement",
            "fix": "fix",
            "refactor": "refactor",
            "test": "test",
            "docs": "doc",
            "style": "refactor",
            "perf": "refactor",
            "chore": "config",
        }
        return mapping.get(git_type.lower(), "unknown")

    def _heuristic_detect(self, text):
        """启发式意图检测"""
        text_lower = text.lower()

        # 检查是否包含任务相关关键词
        task_indicators = [
            "功能", "feature", "模块", "module", "接口", "api",
            "页面", "page", "组件", "component", "服务", "service",
        ]

        for indicator in task_indicators:
            if indicator in text_lower:
                # 尝试提取上下文
                action, target = self._extract_action_target(text)
                if target:
                    return Intent(
                        raw_text=text[:200],
                        action=action,
                        target=target,
                        source="inferred",
                        confidence=0.4,
                    )

        return None

    def _is_similar_intent(self, intent1, intent2):
        """判断两个意图是否相似"""
        if intent1.action != intent2.action:
            return False

        # 简单的目标相似度检查
        t1 = set(intent1.target.lower().split())
        t2 = set(intent2.target.lower().split())
        if not t1 or not t2:
            return False

        overlap = len(t1 & t2) / max(len(t1), len(t2))
        return overlap > 0.5

    def _save_intent(self, intent):
        """保存意图到数据库"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO intents
                (intent_id, session_id, raw_text, action, target, constraints,
                 success_criteria, status, confidence, source, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                intent.intent_id,
                intent.session_id,
                intent.raw_text,
                intent.action,
                intent.target,
                json.dumps(intent.constraints, ensure_ascii=False),
                json.dumps(intent.success_criteria, ensure_ascii=False),
                intent.status,
                intent.confidence,
                intent.source,
                intent.created_at,
                intent.completed_at,
            ))

    def get_active_intent(self, session_id):
        """获取当前活跃的意图"""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM intents
                WHERE session_id = ? AND status = 'active'
                ORDER BY created_at DESC
                LIMIT 1
            """, (session_id,)).fetchone()

            if row:
                return self._row_to_intent(row)
        return None

    def get_all_intents(self, session_id, limit=10):
        """获取会话的所有意图"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM intents
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (session_id, limit)).fetchall()

            return [self._row_to_intent(row) for row in rows]

    def update_intent_status(self, intent_id, status):
        """更新意图状态"""
        completed_at = int(time.time()) if status == "completed" else None

        with self._get_conn() as conn:
            conn.execute("""
                UPDATE intents
                SET status = ?, completed_at = ?
                WHERE intent_id = ?
            """, (status, completed_at, intent_id))

    def get_intent_summary(self, session_id):
        """获取意图摘要（用于 LLM 上下文）"""
        active = self.get_active_intent(session_id)
        if not active:
            return ""

        return active.to_context_string()

    def _row_to_intent(self, row):
        """将数据库行转换为 Intent 对象"""
        return Intent(
            intent_id=row['intent_id'],
            session_id=row['session_id'],
            raw_text=row['raw_text'],
            action=row['action'],
            target=row['target'],
            constraints=json.loads(row['constraints'] or '{}'),
            success_criteria=json.loads(row['success_criteria'] or '[]'),
            status=row['status'],
            confidence=row['confidence'],
            source=row['source'],
            created_at=row['created_at'],
            completed_at=row['completed_at'],
        )

    def infer_success_criteria(self, intent):
        """推断成功标准"""
        criteria = []

        action_criteria = {
            "implement": ["功能可用", "测试通过"],
            "fix": ["错误消失", "测试通过"],
            "test": ["所有测试通过", "覆盖率达标"],
            "refactor": ["功能不变", "代码更清晰"],
            "deploy": ["部署成功", "服务可访问"],
        }

        if intent.action in action_criteria:
            criteria.extend(action_criteria[intent.action])

        return criteria


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Intent Parser',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # detect
    p_detect = subparsers.add_parser('detect', help='Detect intent from text')
    p_detect.add_argument('session_id', help='Session ID')
    p_detect.add_argument('text', nargs='?', help='Text to analyze (or read from stdin)')

    # get
    p_get = subparsers.add_parser('get', help='Get active intent')
    p_get.add_argument('session_id', help='Session ID')

    # list
    p_list = subparsers.add_parser('list', help='List all intents')
    p_list.add_argument('session_id', help='Session ID')
    p_list.add_argument('--limit', type=int, default=10)

    # update
    p_update = subparsers.add_parser('update', help='Update intent status')
    p_update.add_argument('intent_id', help='Intent ID')
    p_update.add_argument('status', choices=['active', 'completed', 'abandoned', 'superseded'])

    # summary
    p_summary = subparsers.add_parser('summary', help='Get intent summary for LLM context')
    p_summary.add_argument('session_id', help='Session ID')

    args = parser.parse_args(argv)
    ip = IntentParser()

    try:
        if args.command == 'detect':
            text = args.text
            if not text:
                text = sys.stdin.read()

            intent = ip.detect_intent(args.session_id, text)
            if intent:
                print(json.dumps(intent.to_dict(), indent=2, ensure_ascii=False))
            else:
                print("{}")

        elif args.command == 'get':
            intent = ip.get_active_intent(args.session_id)
            if intent:
                print(json.dumps(intent.to_dict(), indent=2, ensure_ascii=False))
            else:
                print("{}")

        elif args.command == 'list':
            intents = ip.get_all_intents(args.session_id, args.limit)
            for intent in intents:
                status_icon = {'active': '🎯', 'completed': '✅', 'abandoned': '❌', 'superseded': '↩️'}.get(intent.status, '?')
                print(f"{status_icon} [{intent.action}] {intent.target[:50]} ({intent.confidence:.0%})")

        elif args.command == 'update':
            ip.update_intent_status(args.intent_id, args.status)
            print(f"Intent {args.intent_id} updated to {args.status}")

        elif args.command == 'summary':
            summary = ip.get_intent_summary(args.session_id)
            if summary:
                print(summary)

        else:
            parser.print_help()
            return 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
