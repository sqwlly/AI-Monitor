#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Goal Decomposer
目标分解引擎 - 将高层目标分解为可执行的任务层次

功能：
1. 目标层次结构（Goal -> Milestone -> Task -> Step）
2. 自动分解策略（模板/LLM/历史模式）
3. 依赖关系管理
4. 进度追踪

Usage:
    python3 goal_decomposer.py create <session_id> <intent_id> <title> [--description]
    python3 goal_decomposer.py decompose <goal_id> [--strategy template|llm|auto]
    python3 goal_decomposer.py status <session_id>
    python3 goal_decomposer.py update <goal_id> <status>
    python3 goal_decomposer.py tree <session_id>
    python3 goal_decomposer.py progress <goal_id>
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
from compat_dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# 数据库路径
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


class GoalLevel(Enum):
    """目标层级"""
    GOAL = "goal"               # 顶层目标
    MILESTONE = "milestone"     # 里程碑
    TASK = "task"               # 具体任务
    STEP = "step"               # 执行步骤


class GoalStatus(Enum):
    """目标状态"""
    PENDING = "pending"         # 待开始
    IN_PROGRESS = "in_progress" # 进行中
    COMPLETED = "completed"     # 已完成
    BLOCKED = "blocked"         # 被阻塞
    FAILED = "failed"           # 失败
    SKIPPED = "skipped"         # 已跳过


@dataclass
class Goal:
    """目标数据结构"""
    goal_id: str = ""
    intent_id: Optional[str] = None
    session_id: Optional[str] = None
    parent_id: Optional[str] = None
    level: GoalLevel = GoalLevel.GOAL
    title: str = ""
    description: str = ""
    status: GoalStatus = GoalStatus.PENDING
    progress: float = 0.0
    dependencies: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: int = 0
    started_at: Optional[int] = None
    completed_at: Optional[int] = None

    def __post_init__(self):
        if not self.goal_id:
            self.goal_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = int(time.time())
        if isinstance(self.level, str):
            self.level = GoalLevel(self.level)
        if isinstance(self.status, str):
            self.status = GoalStatus(self.status)

    def to_dict(self) -> Dict:
        return {
            "goal_id": self.goal_id,
            "intent_id": self.intent_id,
            "session_id": self.session_id,
            "parent_id": self.parent_id,
            "level": self.level.value,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "progress": round(self.progress, 2),
            "dependencies": self.dependencies,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    def to_context_string(self) -> str:
        """生成用于 LLM 上下文的字符串"""
        level_icons = {
            GoalLevel.GOAL: "🎯",
            GoalLevel.MILESTONE: "🏁",
            GoalLevel.TASK: "📋",
            GoalLevel.STEP: "▪️",
        }
        status_icons = {
            GoalStatus.PENDING: "⬜",
            GoalStatus.IN_PROGRESS: "🔄",
            GoalStatus.COMPLETED: "✅",
            GoalStatus.BLOCKED: "🚫",
            GoalStatus.FAILED: "❌",
            GoalStatus.SKIPPED: "⏭️",
        }
        icon = level_icons.get(self.level, "•")
        status = status_icons.get(self.status, "?")
        return f"{icon}{status} {self.title} ({self.progress:.0%})"


# 任务分解模板
DECOMPOSITION_TEMPLATES = {
    "implement": {
        "keywords": ["实现", "开发", "创建", "新增", "implement", "create", "add", "build"],
        "milestones": [
            {"title": "设计与规划", "tasks": ["理解需求", "设计方案", "确定技术栈"]},
            {"title": "核心开发", "tasks": ["搭建基础结构", "实现核心逻辑", "处理边界情况"]},
            {"title": "测试验证", "tasks": ["编写单元测试", "集成测试", "手动验证"]},
            {"title": "完善收尾", "tasks": ["代码审查", "文档更新", "清理优化"]},
        ],
    },
    "fix": {
        "keywords": ["修复", "修", "解决", "fix", "solve", "resolve", "debug"],
        "milestones": [
            {"title": "问题定位", "tasks": ["复现问题", "分析日志", "定位根因"]},
            {"title": "修复实施", "tasks": ["编写修复代码", "验证修复效果"]},
            {"title": "回归测试", "tasks": ["运行相关测试", "确认无副作用"]},
        ],
    },
    "refactor": {
        "keywords": ["重构", "优化", "改进", "refactor", "optimize", "improve"],
        "milestones": [
            {"title": "现状分析", "tasks": ["理解现有代码", "识别问题点", "制定重构方案"]},
            {"title": "渐进重构", "tasks": ["重构核心模块", "保持向后兼容"]},
            {"title": "验证优化", "tasks": ["运行测试套件", "性能对比", "代码审查"]},
        ],
    },
    "test": {
        "keywords": ["测试", "验证", "test", "verify", "validate"],
        "milestones": [
            {"title": "测试准备", "tasks": ["分析测试范围", "准备测试数据"]},
            {"title": "执行测试", "tasks": ["运行自动化测试", "手动测试关键路径"]},
            {"title": "结果分析", "tasks": ["分析失败用例", "生成测试报告"]},
        ],
    },
    "deploy": {
        "keywords": ["部署", "发布", "上线", "deploy", "release", "publish"],
        "milestones": [
            {"title": "部署准备", "tasks": ["确认版本", "备份数据", "准备回滚方案"]},
            {"title": "执行部署", "tasks": ["部署到环境", "验证部署结果"]},
            {"title": "上线验证", "tasks": ["功能验证", "性能监控", "确认稳定"]},
        ],
    },
}


class GoalDecomposer:
    """目标分解引擎"""

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
                CREATE TABLE IF NOT EXISTS goals (
                    goal_id TEXT PRIMARY KEY,
                    intent_id TEXT,
                    session_id TEXT,
                    parent_id TEXT,
                    level TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    status TEXT DEFAULT 'pending',
                    progress REAL DEFAULT 0,
                    dependencies TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}',
                    created_at INTEGER,
                    started_at INTEGER,
                    completed_at INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_goals_session
                ON goals(session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_goals_parent
                ON goals(parent_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_goals_intent
                ON goals(intent_id)
            """)

    def create_goal(self, session_id: str, intent_id: Optional[str], title: str,
                   description: str = "", level: GoalLevel = GoalLevel.GOAL,
                   parent_id: Optional[str] = None) -> Goal:
        """创建目标"""
        goal = Goal(
            intent_id=intent_id,
            session_id=session_id,
            parent_id=parent_id,
            level=level,
            title=title,
            description=description,
        )
        self._save_goal(goal)
        return goal

    def decompose(self, goal_id: str, strategy: str = "auto") -> List[Goal]:
        """分解目标为子目标"""
        goal = self.get_goal(goal_id)
        if not goal:
            return []

        if strategy == "template":
            return self._decompose_by_template(goal)
        elif strategy == "history":
            return self._decompose_by_history(goal)
        else:  # auto
            # 先尝试模板，再尝试历史
            results = self._decompose_by_template(goal)
            if not results:
                results = self._decompose_by_history(goal)
            return results

    def _decompose_by_template(self, goal: Goal) -> List[Goal]:
        """基于模板分解目标"""
        title_lower = goal.title.lower()
        description_lower = goal.description.lower() if goal.description else ""
        text = f"{title_lower} {description_lower}"

        # 匹配模板
        matched_template = None
        for template_type, template in DECOMPOSITION_TEMPLATES.items():
            for keyword in template["keywords"]:
                if keyword in text:
                    matched_template = template
                    break
            if matched_template:
                break

        if not matched_template:
            # 默认使用 implement 模板
            matched_template = DECOMPOSITION_TEMPLATES["implement"]

        # 创建子目标
        created_goals = []
        next_level = self._get_next_level(goal.level)

        if goal.level == GoalLevel.GOAL:
            # 创建里程碑
            for milestone_data in matched_template["milestones"]:
                milestone = self.create_goal(
                    session_id=goal.session_id,
                    intent_id=goal.intent_id,
                    title=milestone_data["title"],
                    level=GoalLevel.MILESTONE,
                    parent_id=goal.goal_id,
                )
                created_goals.append(milestone)

                # 创建任务
                for task_title in milestone_data["tasks"]:
                    task = self.create_goal(
                        session_id=goal.session_id,
                        intent_id=goal.intent_id,
                        title=task_title,
                        level=GoalLevel.TASK,
                        parent_id=milestone.goal_id,
                    )
                    created_goals.append(task)

        elif goal.level == GoalLevel.MILESTONE:
            # 从模板中找到对应的任务
            for milestone_data in matched_template["milestones"]:
                if self._is_similar(goal.title, milestone_data["title"]):
                    for task_title in milestone_data["tasks"]:
                        task = self.create_goal(
                            session_id=goal.session_id,
                            intent_id=goal.intent_id,
                            title=task_title,
                            level=GoalLevel.TASK,
                            parent_id=goal.goal_id,
                        )
                        created_goals.append(task)
                    break

        return created_goals

    def _decompose_by_history(self, goal: Goal) -> List[Goal]:
        """基于历史相似任务分解"""
        # 查找相似的历史目标
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM goals
                WHERE level = ? AND status = 'completed'
                AND session_id != ?
                ORDER BY completed_at DESC
                LIMIT 20
            """, (goal.level.value, goal.session_id)).fetchall()

        # 找到最相似的
        best_match = None
        best_score = 0

        for row in rows:
            score = self._calculate_similarity(goal.title, row['title'])
            if score > best_score and score > 0.5:
                best_score = score
                best_match = row

        if not best_match:
            return []

        # 复制其子目标结构
        children = self._get_children(best_match['goal_id'])
        created_goals = []

        for child in children:
            new_child = self.create_goal(
                session_id=goal.session_id,
                intent_id=goal.intent_id,
                title=child.title,
                description=child.description,
                level=child.level,
                parent_id=goal.goal_id,
            )
            created_goals.append(new_child)

        return created_goals

    def _get_next_level(self, current: GoalLevel) -> GoalLevel:
        """获取下一层级"""
        level_order = [GoalLevel.GOAL, GoalLevel.MILESTONE, GoalLevel.TASK, GoalLevel.STEP]
        current_idx = level_order.index(current)
        if current_idx < len(level_order) - 1:
            return level_order[current_idx + 1]
        return current

    def _is_similar(self, text1: str, text2: str) -> bool:
        """判断两个文本是否相似"""
        return self._calculate_similarity(text1, text2) > 0.5

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """计算文本相似度（简单实现）"""
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 or not words2:
            return 0.0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0

    def _save_goal(self, goal: Goal):
        """保存目标到数据库"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO goals
                (goal_id, intent_id, session_id, parent_id, level, title,
                 description, status, progress, dependencies, metadata,
                 created_at, started_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                goal.goal_id,
                goal.intent_id,
                goal.session_id,
                goal.parent_id,
                goal.level.value,
                goal.title,
                goal.description,
                goal.status.value,
                goal.progress,
                json.dumps(goal.dependencies, ensure_ascii=False),
                json.dumps(goal.metadata, ensure_ascii=False),
                goal.created_at,
                goal.started_at,
                goal.completed_at,
            ))

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """获取目标"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM goals WHERE goal_id = ?", (goal_id,)
            ).fetchone()
            if row:
                return self._row_to_goal(row)
        return None

    def _get_children(self, parent_id: str) -> List[Goal]:
        """获取子目标"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE parent_id = ? ORDER BY created_at",
                (parent_id,)
            ).fetchall()
            return [self._row_to_goal(row) for row in rows]

    def get_root_goals(self, session_id: str) -> List[Goal]:
        """获取会话的根目标"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM goals
                WHERE session_id = ? AND parent_id IS NULL
                ORDER BY created_at DESC
            """, (session_id,)).fetchall()
            return [self._row_to_goal(row) for row in rows]

    def get_active_goal(self, session_id: str) -> Optional[Goal]:
        """获取当前活跃的目标（最近的进行中目标）"""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM goals
                WHERE session_id = ? AND status = 'in_progress'
                ORDER BY created_at DESC
                LIMIT 1
            """, (session_id,)).fetchone()
            if row:
                return self._row_to_goal(row)
        return None

    def update_status(self, goal_id: str, status: GoalStatus):
        """更新目标状态"""
        now = int(time.time())

        with self._get_conn() as conn:
            updates = {"status": status.value}

            if status == GoalStatus.IN_PROGRESS:
                conn.execute("""
                    UPDATE goals SET status = ?, started_at = ?
                    WHERE goal_id = ? AND started_at IS NULL
                """, (status.value, now, goal_id))
            elif status in [GoalStatus.COMPLETED, GoalStatus.FAILED, GoalStatus.SKIPPED]:
                conn.execute("""
                    UPDATE goals SET status = ?, completed_at = ?, progress = ?
                    WHERE goal_id = ?
                """, (status.value, now, 1.0 if status == GoalStatus.COMPLETED else 0.0, goal_id))
            else:
                conn.execute("""
                    UPDATE goals SET status = ? WHERE goal_id = ?
                """, (status.value, goal_id))

        # 更新父目标进度
        goal = self.get_goal(goal_id)
        if goal and goal.parent_id:
            self._update_parent_progress(goal.parent_id)

    def update_progress(self, goal_id: str, progress: float):
        """更新目标进度"""
        progress = max(0.0, min(1.0, progress))

        with self._get_conn() as conn:
            conn.execute("""
                UPDATE goals SET progress = ? WHERE goal_id = ?
            """, (progress, goal_id))

        # 更新父目标进度
        goal = self.get_goal(goal_id)
        if goal and goal.parent_id:
            self._update_parent_progress(goal.parent_id)

    def _update_parent_progress(self, parent_id: str):
        """更新父目标的进度（基于子目标）"""
        children = self._get_children(parent_id)
        if not children:
            return

        # 计算平均进度
        total_progress = sum(c.progress for c in children)
        avg_progress = total_progress / len(children)

        with self._get_conn() as conn:
            conn.execute("""
                UPDATE goals SET progress = ? WHERE goal_id = ?
            """, (avg_progress, parent_id))

        # 递归更新
        parent = self.get_goal(parent_id)
        if parent and parent.parent_id:
            self._update_parent_progress(parent.parent_id)

    def add_dependency(self, goal_id: str, depends_on: str):
        """添加依赖关系"""
        goal = self.get_goal(goal_id)
        if goal and depends_on not in goal.dependencies:
            goal.dependencies.append(depends_on)
            self._save_goal(goal)

    def check_blocked(self, goal_id: str) -> bool:
        """检查目标是否被阻塞"""
        goal = self.get_goal(goal_id)
        if not goal or not goal.dependencies:
            return False

        for dep_id in goal.dependencies:
            dep = self.get_goal(dep_id)
            if dep and dep.status != GoalStatus.COMPLETED:
                return True

        return False

    def get_tree(self, session_id: str) -> Dict:
        """获取目标树结构"""
        roots = self.get_root_goals(session_id)
        return [self._build_tree(goal) for goal in roots]

    def _build_tree(self, goal: Goal) -> Dict:
        """构建目标树"""
        children = self._get_children(goal.goal_id)
        return {
            **goal.to_dict(),
            "children": [self._build_tree(c) for c in children],
        }

    def get_status_summary(self, session_id: str) -> str:
        """获取目标状态摘要（用于 LLM 上下文）"""
        active = self.get_active_goal(session_id)
        if not active:
            roots = self.get_root_goals(session_id)
            if roots:
                active = roots[0]
            else:
                return ""

        # 构建摘要
        parts = [f"[goal] {active.to_context_string()}"]

        # 添加子目标进度
        children = self._get_children(active.goal_id)
        if children:
            completed = sum(1 for c in children if c.status == GoalStatus.COMPLETED)
            parts.append(f"子任务: {completed}/{len(children)}")

        return " | ".join(parts)

    def print_tree(self, session_id: str, indent: int = 0):
        """打印目标树"""
        roots = self.get_root_goals(session_id)
        for root in roots:
            self._print_node(root, indent)

    def _print_node(self, goal: Goal, indent: int):
        """打印节点"""
        prefix = "  " * indent
        print(f"{prefix}{goal.to_context_string()}")
        children = self._get_children(goal.goal_id)
        for child in children:
            self._print_node(child, indent + 1)

    def _row_to_goal(self, row) -> Goal:
        """将数据库行转换为 Goal 对象"""
        return Goal(
            goal_id=row['goal_id'],
            intent_id=row['intent_id'],
            session_id=row['session_id'],
            parent_id=row['parent_id'],
            level=GoalLevel(row['level']),
            title=row['title'],
            description=row['description'] or "",
            status=GoalStatus(row['status']),
            progress=row['progress'] or 0.0,
            dependencies=json.loads(row['dependencies'] or '[]'),
            metadata=json.loads(row['metadata'] or '{}'),
            created_at=row['created_at'],
            started_at=row['started_at'],
            completed_at=row['completed_at'],
        )


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Goal Decomposer',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # create
    p_create = subparsers.add_parser('create', help='Create a new goal')
    p_create.add_argument('session_id', help='Session ID')
    p_create.add_argument('intent_id', help='Intent ID (or "none")')
    p_create.add_argument('title', help='Goal title')
    p_create.add_argument('--description', '-d', default='', help='Goal description')

    # decompose
    p_decompose = subparsers.add_parser('decompose', help='Decompose goal into sub-goals')
    p_decompose.add_argument('goal_id', help='Goal ID')
    p_decompose.add_argument('--strategy', '-s', choices=['template', 'history', 'auto'],
                            default='auto', help='Decomposition strategy')

    # status
    p_status = subparsers.add_parser('status', help='Get goal status summary')
    p_status.add_argument('session_id', help='Session ID')

    # update
    p_update = subparsers.add_parser('update', help='Update goal status')
    p_update.add_argument('goal_id', help='Goal ID')
    p_update.add_argument('status', choices=['pending', 'in_progress', 'completed',
                                             'blocked', 'failed', 'skipped'])

    # tree
    p_tree = subparsers.add_parser('tree', help='Show goal tree')
    p_tree.add_argument('session_id', help='Session ID')
    p_tree.add_argument('--json', action='store_true', help='Output as JSON')

    # progress
    p_progress = subparsers.add_parser('progress', help='Update goal progress')
    p_progress.add_argument('goal_id', help='Goal ID')
    p_progress.add_argument('progress', type=float, help='Progress (0.0-1.0)')

    args = parser.parse_args(argv)
    decomposer = GoalDecomposer()

    try:
        if args.command == 'create':
            intent_id = None if args.intent_id == 'none' else args.intent_id
            goal = decomposer.create_goal(
                session_id=args.session_id,
                intent_id=intent_id,
                title=args.title,
                description=args.description,
            )
            print(json.dumps(goal.to_dict(), indent=2, ensure_ascii=False))

        elif args.command == 'decompose':
            goals = decomposer.decompose(args.goal_id, args.strategy)
            print(f"Created {len(goals)} sub-goals:")
            for g in goals:
                print(f"  - [{g.level.value}] {g.title}")

        elif args.command == 'status':
            summary = decomposer.get_status_summary(args.session_id)
            if summary:
                print(summary)
            else:
                print("No active goals")

        elif args.command == 'update':
            decomposer.update_status(args.goal_id, GoalStatus(args.status))
            print(f"Goal {args.goal_id} updated to {args.status}")

        elif args.command == 'tree':
            if args.json:
                tree = decomposer.get_tree(args.session_id)
                print(json.dumps(tree, indent=2, ensure_ascii=False))
            else:
                decomposer.print_tree(args.session_id)

        elif args.command == 'progress':
            decomposer.update_progress(args.goal_id, args.progress)
            print(f"Goal {args.goal_id} progress updated to {args.progress:.0%}")

        else:
            parser.print_help()
            return 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
