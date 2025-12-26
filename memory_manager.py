#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Memory Manager
任务记忆管理系统 - 跨会话记录项目进度和决策历史

Usage:
    python3 memory_manager.py start-session <target> [project_path]
    python3 memory_manager.py end-session <session_id> <status> [summary]
    python3 memory_manager.py record <session_id> <stage> <role> <output> <outcome>
    python3 memory_manager.py resume <project_path>
    python3 memory_manager.py stats [project_path]
    python3 memory_manager.py export <session_id> [--format json|csv]
    python3 memory_manager.py list [--status active|completed|all]
    python3 memory_manager.py clean [--days N]
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

# Python 3.6 兼容：条件导入 typing
try:
    from typing import Optional, List, Dict, Any
except ImportError:
    pass

# 默认配置
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
SCHEMA_PATH = Path(__file__).parent / "memory" / "schema.sql"

# 环境变量覆盖
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


# 简单数据容器（Python 3.6 兼容，不用 dataclass）
class Session:
    """会话记录"""
    def __init__(self, session_id, target, project_path=None, start_time=0,
                 end_time=None, status='active', summary=None,
                 total_commands=0, total_waits=0, last_stage=None, last_role=None, **kwargs):
        self.session_id = session_id
        self.target = target
        self.project_path = project_path
        self.start_time = start_time
        self.end_time = end_time
        self.status = status
        self.summary = summary
        self.total_commands = total_commands
        self.total_waits = total_waits
        self.last_stage = last_stage
        self.last_role = last_role


class Decision:
    """决策记录"""
    def __init__(self, session_id, timestamp, output, outcome,
                 stage=None, role=None, input_hash='', input_preview='', latency_ms=None, **kwargs):
        self.session_id = session_id
        self.timestamp = timestamp
        self.stage = stage
        self.role = role
        self.input_hash = input_hash
        self.input_preview = input_preview
        self.output = output
        self.outcome = outcome
        self.latency_ms = latency_ms


class ResumeContext:
    """恢复上下文"""
    def __init__(self, last_session_id=None, last_stage=None, last_summary=None,
                 total_sessions=0, total_commands=0, recent_decisions=None,
                 learned_patterns=None, stage_distribution=None, **kwargs):
        self.last_session_id = last_session_id
        self.last_stage = last_stage
        self.last_summary = last_summary
        self.total_sessions = total_sessions
        self.total_commands = total_commands
        self.recent_decisions = recent_decisions or []
        self.learned_patterns = learned_patterns or []
        self.stage_distribution = stage_distribution or {}


class MemoryManager:
    """任务记忆管理器"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    def _ensure_db(self):
        """确保数据库和目录存在"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.db_path.exists():
            self._init_db()

    def _init_db(self):
        """初始化数据库"""
        schema_path = SCHEMA_PATH
        if not schema_path.exists():
            # 内联 schema（备用）
            schema = self._get_inline_schema()
        else:
            schema = schema_path.read_text()

        with self._connect() as conn:
            conn.executescript(schema)

    def _get_inline_schema(self) -> str:
        """内联 schema（当文件不存在时使用）"""
        return """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            target TEXT NOT NULL,
            project_path TEXT,
            start_time INTEGER NOT NULL,
            end_time INTEGER,
            status TEXT DEFAULT 'active',
            summary TEXT,
            total_commands INTEGER DEFAULT 0,
            total_waits INTEGER DEFAULT 0,
            last_stage TEXT,
            last_role TEXT,
            created_at INTEGER DEFAULT (strftime('%s', 'now'))
        );

        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            stage TEXT,
            role TEXT,
            input_hash TEXT,
            input_preview TEXT,
            output TEXT NOT NULL,
            outcome TEXT,
            latency_ms INTEGER,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS stage_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            entered_at INTEGER NOT NULL,
            exited_at INTEGER,
            duration_s INTEGER,
            commands_sent INTEGER DEFAULT 0,
            waits INTEGER DEFAULT 0,
            role_used TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS error_patterns (
            pattern_hash TEXT PRIMARY KEY,
            error_signature TEXT NOT NULL,
            error_preview TEXT,
            occurrences INTEGER DEFAULT 1,
            first_seen INTEGER,
            last_seen INTEGER,
            successful_fixes TEXT,
            failed_fixes TEXT,
            project_path TEXT
        );

        CREATE TABLE IF NOT EXISTS project_progress (
            project_path TEXT PRIMARY KEY,
            last_session_id TEXT,
            total_sessions INTEGER DEFAULT 0,
            total_commands INTEGER DEFAULT 0,
            total_time_s INTEGER DEFAULT 0,
            stage_distribution TEXT,
            milestones TEXT,
            learned_patterns TEXT,
            last_updated INTEGER,
            FOREIGN KEY (last_session_id) REFERENCES sessions(session_id)
        );

        CREATE INDEX IF NOT EXISTS idx_decisions_session ON decisions(session_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_path);
        """

    @contextmanager
    def _connect(self):
        """数据库连接上下文"""
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ==================== Session 管理 ====================

    def start_session(self, target: str, project_path: Optional[str] = None) -> str:
        """开始新会话，返回 session_id"""
        session_id = str(uuid.uuid4())[:8]
        now = int(time.time())

        with self._connect() as conn:
            conn.execute("""
                INSERT INTO sessions (session_id, target, project_path, start_time, status)
                VALUES (?, ?, ?, ?, 'active')
            """, (session_id, target, project_path, now))

            # 更新项目进度
            if project_path:
                conn.execute("""
                    INSERT INTO project_progress (project_path, last_session_id, total_sessions, last_updated)
                    VALUES (?, ?, 1, ?)
                    ON CONFLICT(project_path) DO UPDATE SET
                        last_session_id = excluded.last_session_id,
                        total_sessions = total_sessions + 1,
                        last_updated = excluded.last_updated
                """, (project_path, session_id, now))

        return session_id

    def end_session(self, session_id: str, status: str, summary: Optional[str] = None):
        """结束会话"""
        now = int(time.time())

        with self._connect() as conn:
            # 获取会话信息
            row = conn.execute(
                "SELECT start_time, project_path FROM sessions WHERE session_id = ?",
                (session_id,)
            ).fetchone()

            if not row:
                raise ValueError(f"Session not found: {session_id}")

            start_time = row['start_time']
            project_path = row['project_path']
            duration = now - start_time

            # 关闭未结束的阶段
            conn.execute("""
                UPDATE stage_timeline
                SET exited_at = ?, duration_s = ? - entered_at
                WHERE session_id = ? AND exited_at IS NULL
            """, (now, now, session_id))

            # 更新会话
            conn.execute("""
                UPDATE sessions
                SET end_time = ?, status = ?, summary = ?
                WHERE session_id = ?
            """, (now, status, summary, session_id))

            # 更新项目进度
            if project_path:
                conn.execute("""
                    UPDATE project_progress
                    SET total_time_s = total_time_s + ?,
                        last_updated = ?
                    WHERE project_path = ?
                """, (duration, now, project_path))

    def get_session(self, session_id: str) -> Optional[Session]:
        """获取会话信息"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,)
            ).fetchone()

            if row:
                return Session(**dict(row))
        return None

    def list_sessions(self, status: str = 'all', limit: int = 20) -> List[Dict]:
        """列出会话"""
        with self._connect() as conn:
            if status == 'all':
                rows = conn.execute("""
                    SELECT session_id, target, project_path, start_time, end_time,
                           status, total_commands, total_waits, last_stage
                    FROM sessions ORDER BY start_time DESC LIMIT ?
                """, (limit,)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT session_id, target, project_path, start_time, end_time,
                           status, total_commands, total_waits, last_stage
                    FROM sessions WHERE status = ? ORDER BY start_time DESC LIMIT ?
                """, (status, limit)).fetchall()

            return [dict(row) for row in rows]

    # ==================== 决策记录 ====================

    def record_decision(
        self,
        session_id: str,
        stage: Optional[str],
        role: Optional[str],
        output: str,
        outcome: str,
        input_text: Optional[str] = None,
        latency_ms: Optional[int] = None
    ):
        """记录一次决策"""
        now = int(time.time())
        input_hash = hashlib.sha256((input_text or "").encode()).hexdigest()[:16]
        input_preview = (input_text or "")[:200]

        with self._connect() as conn:
            # 插入决策
            conn.execute("""
                INSERT INTO decisions
                (session_id, timestamp, stage, role, input_hash, input_preview, output, outcome, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, now, stage, role, input_hash, input_preview, output, outcome, latency_ms))

            # 更新会话统计
            is_wait = outcome == 'wait' or output.upper() == 'WAIT'
            if is_wait:
                conn.execute("""
                    UPDATE sessions
                    SET total_waits = total_waits + 1, last_stage = ?, last_role = ?
                    WHERE session_id = ?
                """, (stage, role, session_id))
            else:
                conn.execute("""
                    UPDATE sessions
                    SET total_commands = total_commands + 1, last_stage = ?, last_role = ?
                    WHERE session_id = ?
                """, (stage, role, session_id))

            # 更新阶段时间线
            self._update_stage_timeline(conn, session_id, stage, role, is_wait, now)

    def _update_stage_timeline(
        self,
        conn,
        session_id: str,
        stage: Optional[str],
        role: Optional[str],
        is_wait: bool,
        now: int
    ):
        """更新阶段时间线"""
        if not stage:
            return

        # 检查当前阶段
        current = conn.execute("""
            SELECT id, stage FROM stage_timeline
            WHERE session_id = ? AND exited_at IS NULL
            ORDER BY entered_at DESC LIMIT 1
        """, (session_id,)).fetchone()

        if current and current['stage'] == stage:
            # 同一阶段，更新计数
            if is_wait:
                conn.execute("""
                    UPDATE stage_timeline SET waits = waits + 1 WHERE id = ?
                """, (current['id'],))
            else:
                conn.execute("""
                    UPDATE stage_timeline SET commands_sent = commands_sent + 1 WHERE id = ?
                """, (current['id'],))
        else:
            # 新阶段，关闭旧阶段
            if current:
                conn.execute("""
                    UPDATE stage_timeline
                    SET exited_at = ?, duration_s = ? - entered_at
                    WHERE id = ?
                """, (now, now, current['id']))

            # 创建新阶段
            conn.execute("""
                INSERT INTO stage_timeline
                (session_id, stage, entered_at, role_used, commands_sent, waits)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (session_id, stage, now, role, 0 if is_wait else 1, 1 if is_wait else 0))

    def get_recent_decisions(self, session_id: str, limit: int = 10) -> List[Dict]:
        """获取最近决策"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT timestamp, stage, role, output, outcome
                FROM decisions
                WHERE session_id = ?
                ORDER BY timestamp DESC LIMIT ?
            """, (session_id, limit)).fetchall()

            return [dict(row) for row in rows]

    # ==================== 错误模式学习 ====================

    def record_error(self, error_signature: str, error_preview: str, project_path: Optional[str] = None):
        """记录错误"""
        now = int(time.time())
        pattern_hash = hashlib.sha256(error_signature.encode()).hexdigest()[:16]

        with self._connect() as conn:
            conn.execute("""
                INSERT INTO error_patterns
                (pattern_hash, error_signature, error_preview, first_seen, last_seen, project_path)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pattern_hash) DO UPDATE SET
                    occurrences = occurrences + 1,
                    last_seen = excluded.last_seen
            """, (pattern_hash, error_signature, error_preview, now, now, project_path))

    def record_fix_outcome(self, error_signature: str, command: str, success: bool):
        """记录修复结果"""
        pattern_hash = hashlib.sha256(error_signature.encode()).hexdigest()[:16]

        with self._connect() as conn:
            row = conn.execute(
                "SELECT successful_fixes, failed_fixes FROM error_patterns WHERE pattern_hash = ?",
                (pattern_hash,)
            ).fetchone()

            if not row:
                return

            if success:
                fixes = json.loads(row['successful_fixes'] or '[]')
                # 更新或添加
                for fix in fixes:
                    if fix['command'] == command:
                        fix['success_count'] = fix.get('success_count', 0) + 1
                        break
                else:
                    fixes.append({'command': command, 'success_count': 1})

                conn.execute("""
                    UPDATE error_patterns SET successful_fixes = ? WHERE pattern_hash = ?
                """, (json.dumps(fixes), pattern_hash))
            else:
                fixes = json.loads(row['failed_fixes'] or '[]')
                if command not in fixes:
                    fixes.append(command)
                conn.execute("""
                    UPDATE error_patterns SET failed_fixes = ? WHERE pattern_hash = ?
                """, (json.dumps(fixes), pattern_hash))

    def get_fix_suggestions(self, error_signature: str) -> List[Dict]:
        """获取历史成功修复建议"""
        pattern_hash = hashlib.sha256(error_signature.encode()).hexdigest()[:16]

        with self._connect() as conn:
            row = conn.execute(
                "SELECT successful_fixes FROM error_patterns WHERE pattern_hash = ?",
                (pattern_hash,)
            ).fetchone()

            if row and row['successful_fixes']:
                fixes = json.loads(row['successful_fixes'])
                return sorted(fixes, key=lambda x: x.get('success_count', 0), reverse=True)

        return []

    # ==================== 进度恢复 ====================

    def get_resume_context(self, project_path: str) -> Optional[Dict]:
        """获取恢复上下文"""
        with self._connect() as conn:
            # 项目进度
            progress = conn.execute(
                "SELECT * FROM project_progress WHERE project_path = ?",
                (project_path,)
            ).fetchone()

            if not progress:
                return None

            # 最近会话
            last_session = None
            if progress['last_session_id']:
                last_session = conn.execute(
                    "SELECT * FROM sessions WHERE session_id = ?",
                    (progress['last_session_id'],)
                ).fetchone()

            # 最近决策
            recent_decisions = []
            if progress['last_session_id']:
                rows = conn.execute("""
                    SELECT stage, role, output, outcome
                    FROM decisions
                    WHERE session_id = ?
                    ORDER BY timestamp DESC LIMIT 5
                """, (progress['last_session_id'],)).fetchall()
                recent_decisions = [dict(row) for row in rows]

            # 阶段分布
            stage_dist = json.loads(progress['stage_distribution'] or '{}')

            # 学习到的模式
            patterns = []
            rows = conn.execute("""
                SELECT error_signature, occurrences, successful_fixes
                FROM error_patterns
                WHERE project_path = ? AND successful_fixes IS NOT NULL
                ORDER BY occurrences DESC LIMIT 5
            """, (project_path,)).fetchall()
            for row in rows:
                fixes = json.loads(row['successful_fixes'] or '[]')
                if fixes:
                    patterns.append({
                        'error': row['error_signature'][:100],
                        'occurrences': row['occurrences'],
                        'best_fix': fixes[0]['command'] if fixes else None
                    })

            return {
                'last_session_id': progress['last_session_id'],
                'last_stage': last_session['last_stage'] if last_session else None,
                'last_summary': last_session['summary'] if last_session else None,
                'last_status': last_session['status'] if last_session else None,
                'total_sessions': progress['total_sessions'],
                'total_commands': progress['total_commands'],
                'total_time_s': progress['total_time_s'],
                'recent_decisions': recent_decisions,
                'learned_patterns': patterns,
                'stage_distribution': stage_dist
            }

    # ==================== 统计分析 ====================

    def get_stats(self, project_path: Optional[str] = None) -> Dict:
        """获取统计信息"""
        with self._connect() as conn:
            if project_path:
                # 项目统计
                sessions = conn.execute("""
                    SELECT COUNT(*) as count,
                           SUM(total_commands) as commands,
                           SUM(total_waits) as waits,
                           SUM(COALESCE(end_time, strftime('%s', 'now')) - start_time) as total_time
                    FROM sessions WHERE project_path = ?
                """, (project_path,)).fetchone()

                stages = conn.execute("""
                    SELECT stage, SUM(duration_s) as duration, SUM(commands_sent) as commands
                    FROM stage_timeline st
                    JOIN sessions s ON st.session_id = s.session_id
                    WHERE s.project_path = ?
                    GROUP BY stage ORDER BY duration DESC
                """, (project_path,)).fetchall()

                return {
                    'project_path': project_path,
                    'total_sessions': sessions['count'],
                    'total_commands': sessions['commands'] or 0,
                    'total_waits': sessions['waits'] or 0,
                    'total_time_s': sessions['total_time'] or 0,
                    'action_rate': round(100 * (sessions['commands'] or 0) /
                                        max(1, (sessions['commands'] or 0) + (sessions['waits'] or 0)), 1),
                    'stages': [dict(row) for row in stages]
                }
            else:
                # 全局统计
                total = conn.execute("""
                    SELECT COUNT(*) as sessions,
                           SUM(total_commands) as commands,
                           SUM(total_waits) as waits
                    FROM sessions
                """).fetchone()

                active = conn.execute(
                    "SELECT COUNT(*) as count FROM sessions WHERE status = 'active'"
                ).fetchone()

                return {
                    'total_sessions': total['sessions'],
                    'active_sessions': active['count'],
                    'total_commands': total['commands'] or 0,
                    'total_waits': total['waits'] or 0,
                    'action_rate': round(100 * (total['commands'] or 0) /
                                        max(1, (total['commands'] or 0) + (total['waits'] or 0)), 1)
                }

    # ==================== 导出 ====================

    def export_session(self, session_id: str, format: str = 'json') -> str:
        """导出会话数据"""
        with self._connect() as conn:
            session = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,)
            ).fetchone()

            if not session:
                raise ValueError(f"Session not found: {session_id}")

            decisions = conn.execute(
                "SELECT * FROM decisions WHERE session_id = ? ORDER BY timestamp",
                (session_id,)
            ).fetchall()

            stages = conn.execute(
                "SELECT * FROM stage_timeline WHERE session_id = ? ORDER BY entered_at",
                (session_id,)
            ).fetchall()

            data = {
                'session': dict(session),
                'decisions': [dict(d) for d in decisions],
                'stage_timeline': [dict(s) for s in stages]
            }

            if format == 'json':
                return json.dumps(data, indent=2, ensure_ascii=False)
            elif format == 'csv':
                # 简单 CSV 导出（决策表）
                lines = ['timestamp,stage,role,output,outcome']
                for d in decisions:
                    lines.append(f"{d['timestamp']},{d['stage']},{d['role']},{d['output']},{d['outcome']}")
                return '\n'.join(lines)
            else:
                raise ValueError(f"Unknown format: {format}")

    # ==================== 清理 ====================

    def clean(self, days: int = 30):
        """清理旧数据"""
        cutoff = int(time.time()) - days * 86400

        with self._connect() as conn:
            # 删除旧的已完成会话
            result = conn.execute("""
                DELETE FROM sessions
                WHERE status IN ('completed', 'failed')
                AND end_time < ?
            """, (cutoff,))

            deleted = result.rowcount

            # 清理孤立的错误模式
            conn.execute("""
                DELETE FROM error_patterns WHERE last_seen < ?
            """, (cutoff,))

            return deleted


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Memory Manager',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # start-session
    p_start = subparsers.add_parser('start-session', help='Start a new session')
    p_start.add_argument('target', help='tmux target (e.g., 5:node.0)')
    p_start.add_argument('project_path', nargs='?', help='Project path')

    # end-session
    p_end = subparsers.add_parser('end-session', help='End a session')
    p_end.add_argument('session_id', help='Session ID')
    p_end.add_argument('status', choices=['completed', 'failed', 'paused'], help='Final status')
    p_end.add_argument('summary', nargs='?', help='Session summary')

    # record
    p_record = subparsers.add_parser('record', help='Record a decision')
    p_record.add_argument('session_id', help='Session ID')
    p_record.add_argument('stage', help='Current stage')
    p_record.add_argument('role', help='Role used')
    p_record.add_argument('output', help='LLM output')
    p_record.add_argument('outcome', choices=['success', 'wait', 'error', 'ignored', 'blocked'], help='Outcome')
    p_record.add_argument('--latency', type=int, help='Response latency in ms')

    # resume
    p_resume = subparsers.add_parser('resume', help='Get resume context for a project')
    p_resume.add_argument('project_path', help='Project path')

    # stats
    p_stats = subparsers.add_parser('stats', help='Show statistics')
    p_stats.add_argument('project_path', nargs='?', help='Project path (optional)')

    # list
    p_list = subparsers.add_parser('list', help='List sessions')
    p_list.add_argument('--status', choices=['active', 'completed', 'failed', 'all'], default='all')
    p_list.add_argument('--limit', type=int, default=20)

    # export
    p_export = subparsers.add_parser('export', help='Export session data')
    p_export.add_argument('session_id', help='Session ID')
    p_export.add_argument('--format', choices=['json', 'csv'], default='json')

    # clean
    p_clean = subparsers.add_parser('clean', help='Clean old data')
    p_clean.add_argument('--days', type=int, default=30, help='Keep data from last N days')

    # recent-decisions (用于注入 LLM 上下文)
    p_recent = subparsers.add_parser('recent-decisions', help='Get recent decisions for LLM context')
    p_recent.add_argument('session_id', help='Session ID')
    p_recent.add_argument('limit', type=int, nargs='?', default=5, help='Number of decisions')

    args = parser.parse_args(argv)
    mm = MemoryManager()

    try:
        if args.command == 'start-session':
            session_id = mm.start_session(args.target, args.project_path)
            print(session_id)

        elif args.command == 'end-session':
            mm.end_session(args.session_id, args.status, args.summary)
            print(f"Session {args.session_id} ended with status: {args.status}")

        elif args.command == 'record':
            mm.record_decision(
                args.session_id,
                args.stage,
                args.role,
                args.output,
                args.outcome,
                latency_ms=args.latency
            )

        elif args.command == 'resume':
            ctx = mm.get_resume_context(args.project_path)
            if ctx:
                print(json.dumps(ctx, indent=2, ensure_ascii=False))
            else:
                print("{}")

        elif args.command == 'stats':
            stats = mm.get_stats(args.project_path)
            print(json.dumps(stats, indent=2, ensure_ascii=False))

        elif args.command == 'list':
            sessions = mm.list_sessions(args.status, args.limit)
            for s in sessions:
                status_icon = {'active': '🟢', 'completed': '✅', 'failed': '❌', 'paused': '⏸️'}.get(s['status'], '❓')
                uptime = ''
                if s['status'] == 'active':
                    uptime = f" ({int(time.time()) - s['start_time']}s)"
                print(f"{status_icon} {s['session_id']} | {s['target']} | {s['last_stage'] or '-'} | "
                      f"cmds:{s['total_commands']} waits:{s['total_waits']}{uptime}")

        elif args.command == 'export':
            data = mm.export_session(args.session_id, args.format)
            print(data)

        elif args.command == 'clean':
            deleted = mm.clean(args.days)
            print(f"Cleaned {deleted} old sessions")

        elif args.command == 'recent-decisions':
            decisions = mm.get_recent_decisions(args.session_id, args.limit)
            if decisions:
                for d in decisions:
                    outcome_icon = {'success': '✓', 'wait': '⏸', 'error': '✗', 'ignored': '⊘', 'blocked': '🚫'}.get(d.get('outcome', ''), '?')
                    output_preview = (d.get('output', '') or '')[:40]
                    print(f"  {outcome_icon} [{d.get('stage', '?')}] {output_preview}")

        else:
            parser.print_help()
            return 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
