#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Progress Monitor
进度监控器 - 追踪任务完成进度

功能：
1. 完成度检测（基于输出模式、文件变化）
2. 偏离检测（当前活动是否与目标相关）
3. 阻塞识别（长时间无进展、错误循环）
4. 进度报告生成

Usage:
    python3 progress_monitor.py update <session_id> <text>
    python3 progress_monitor.py status <session_id>
    python3 progress_monitor.py report <session_id>
    python3 progress_monitor.py summary <session_id>
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path

# 数据库路径
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))

# 进度信号
PROGRESS_SIGNALS = {
    # 正向信号（表示进展）
    "positive": {
        "test_pass": {
            "patterns": [r"test.*pass", r"tests.*pass", r"PASSED", r"✓.*test", r"OK.*test"],
            "weight": 0.15,
            "message": "测试通过",
        },
        "build_success": {
            "patterns": [r"build.*success", r"compiled", r"built", r"webpack.*done", r"bundle.*complete"],
            "weight": 0.1,
            "message": "构建成功",
        },
        "file_created": {
            "patterns": [r"created.*file", r"wrote.*file", r"saved", r"generated"],
            "weight": 0.05,
            "message": "文件创建",
        },
        "function_implemented": {
            "patterns": [r"implemented", r"added.*function", r"created.*class", r"def.*:", r"function.*\{"],
            "weight": 0.1,
            "message": "功能实现",
        },
        "commit_made": {
            "patterns": [r"commit.*created", r"\[.*\].*commit", r"committed"],
            "weight": 0.05,
            "message": "代码提交",
        },
        "deploy_success": {
            "patterns": [r"deploy.*success", r"published", r"released", r"shipped"],
            "weight": 0.2,
            "message": "部署成功",
        },
    },
    # 负向信号（表示问题）
    "negative": {
        "test_fail": {
            "patterns": [r"test.*fail", r"FAILED", r"✗.*test", r"ERROR.*test"],
            "weight": -0.1,
            "message": "测试失败",
        },
        "build_fail": {
            "patterns": [r"build.*fail", r"compilation.*error", r"webpack.*error"],
            "weight": -0.1,
            "message": "构建失败",
        },
        "error": {
            "patterns": [r"Error:", r"Exception:", r"Traceback"],
            "weight": -0.05,
            "message": "发生错误",
        },
        "stuck": {
            "patterns": [r"waiting", r"blocked", r"stuck", r"卡住"],
            "weight": -0.05,
            "message": "任务阻塞",
        },
    },
    # 中性信号（提供信息）
    "neutral": {
        "progress": {
            "patterns": [r"(\d+)%", r"(\d+)/(\d+)", r"step.*(\d+)"],
            "weight": 0,
            "message": "进度更新",
        },
        "thinking": {
            "patterns": [r"thinking", r"analyzing", r"processing", r"⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏"],
            "weight": 0,
            "message": "正在处理",
        },
    },
}

# 阶段进度权重（各阶段在整体任务中的占比）
STAGE_WEIGHTS = {
    "planning": 0.1,
    "coding": 0.4,
    "testing": 0.2,
    "fixing": 0.1,
    "refining": 0.1,
    "reviewing": 0.05,
    "documenting": 0.03,
    "release": 0.02,
}

# 阻塞阈值
BLOCK_THRESHOLDS = {
    "no_progress_seconds": 300,  # 5分钟无进展
    "same_error_count": 3,  # 同一错误3次
    "same_output_count": 4,  # 相同输出4次
}


class ProgressState:
    """进度状态"""

    def __init__(self, session_id=None, current_stage="unknown",
                 overall_progress=0.0, stage_progress=0.0,
                 last_signal=None, last_signal_time=0,
                 signals_history=None, blocked=False, block_reason=None,
                 started_at=None, updated_at=None, **kwargs):
        self.session_id = session_id
        self.current_stage = current_stage
        self.overall_progress = overall_progress  # 0.0 - 1.0
        self.stage_progress = stage_progress  # 当前阶段的进度
        self.last_signal = last_signal
        self.last_signal_time = last_signal_time
        self.signals_history = signals_history or []  # 最近的信号历史
        self.blocked = blocked
        self.block_reason = block_reason
        self.started_at = started_at or int(time.time())
        self.updated_at = updated_at or int(time.time())

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "current_stage": self.current_stage,
            "overall_progress": round(self.overall_progress, 2),
            "stage_progress": round(self.stage_progress, 2),
            "last_signal": self.last_signal,
            "last_signal_time": self.last_signal_time,
            "signals_history": self.signals_history[-10:],
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "duration_seconds": self.updated_at - self.started_at,
        }

    def to_context_string(self):
        """生成用于 LLM 上下文的字符串"""
        progress_bar = self._make_progress_bar(self.overall_progress)
        stage_name = {
            "planning": "规划",
            "coding": "编码",
            "testing": "测试",
            "fixing": "修复",
            "refining": "优化",
            "reviewing": "审查",
            "documenting": "文档",
            "release": "发布",
            "done": "完成",
        }.get(self.current_stage, self.current_stage)

        result = f"[progress] {progress_bar} {self.overall_progress:.0%} | 阶段: {stage_name}"

        if self.blocked:
            result += f" | ⚠️ 阻塞: {self.block_reason}"
        elif self.last_signal:
            result += f" | 最近: {self.last_signal}"

        # 添加耗时
        duration = self.updated_at - self.started_at
        if duration > 60:
            minutes = duration // 60
            result += f" | 耗时: {minutes}分钟"

        return result

    def _make_progress_bar(self, progress, width=10):
        """生成进度条"""
        filled = int(progress * width)
        empty = width - filled
        return f"[{'=' * filled}{' ' * empty}]"


class ProgressMonitor:
    """进度监控器"""

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
                CREATE TABLE IF NOT EXISTS progress_state (
                    session_id TEXT PRIMARY KEY,
                    current_stage TEXT DEFAULT 'unknown',
                    overall_progress REAL DEFAULT 0,
                    stage_progress REAL DEFAULT 0,
                    last_signal TEXT,
                    last_signal_time INTEGER DEFAULT 0,
                    signals_history TEXT DEFAULT '[]',
                    blocked INTEGER DEFAULT 0,
                    block_reason TEXT,
                    started_at INTEGER,
                    updated_at INTEGER
                )
            """)

    def update(self, session_id, text, stage=None):
        """更新进度状态"""
        state = self._get_state(session_id)

        # 更新阶段
        if stage and stage != "unknown":
            if state.current_stage != stage:
                # 阶段切换，重置阶段进度
                state.stage_progress = 0.0
            state.current_stage = stage

        # 检测信号
        signals = self._detect_signals(text)

        # 更新进度
        for signal in signals:
            self._apply_signal(state, signal)

        # 检测阻塞
        self._check_blocking(state, text)

        # 计算整体进度
        self._calculate_overall_progress(state)

        # 保存状态
        state.updated_at = int(time.time())
        self._save_state(state)

        return state

    def _get_state(self, session_id):
        """获取或创建状态"""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM progress_state WHERE session_id = ?
            """, (session_id,)).fetchone()

            if row:
                return ProgressState(
                    session_id=row['session_id'],
                    current_stage=row['current_stage'],
                    overall_progress=row['overall_progress'],
                    stage_progress=row['stage_progress'],
                    last_signal=row['last_signal'],
                    last_signal_time=row['last_signal_time'],
                    signals_history=json.loads(row['signals_history'] or '[]'),
                    blocked=bool(row['blocked']),
                    block_reason=row['block_reason'],
                    started_at=row['started_at'],
                    updated_at=row['updated_at'],
                )

        return ProgressState(session_id=session_id)

    def _detect_signals(self, text):
        """检测进度信号"""
        signals = []
        text_lower = text.lower()

        for signal_type, signal_configs in PROGRESS_SIGNALS.items():
            for signal_name, config in signal_configs.items():
                for pattern in config["patterns"]:
                    if re.search(pattern, text_lower, re.IGNORECASE):
                        signals.append({
                            "type": signal_type,
                            "name": signal_name,
                            "weight": config["weight"],
                            "message": config["message"],
                            "time": int(time.time()),
                        })
                        break  # 每个信号只匹配一次

        return signals

    def _apply_signal(self, state, signal):
        """应用信号到状态"""
        # 更新最后信号
        state.last_signal = signal["message"]
        state.last_signal_time = signal["time"]

        # 更新阶段进度
        if signal["type"] == "positive":
            state.stage_progress = min(1.0, state.stage_progress + signal["weight"])
            state.blocked = False
            state.block_reason = None
        elif signal["type"] == "negative":
            state.stage_progress = max(0.0, state.stage_progress + signal["weight"])

        # 记录历史
        state.signals_history.append({
            "signal": signal["name"],
            "message": signal["message"],
            "time": signal["time"],
        })

        # 保留最近20条
        if len(state.signals_history) > 20:
            state.signals_history = state.signals_history[-20:]

    def _check_blocking(self, state, text):
        """检测阻塞状态"""
        now = int(time.time())

        # 检查长时间无进展
        if state.last_signal_time > 0:
            idle_time = now - state.last_signal_time
            if idle_time > BLOCK_THRESHOLDS["no_progress_seconds"]:
                state.blocked = True
                state.block_reason = f"无进展 {idle_time // 60} 分钟"
                return

        # 检查重复信号（可能卡在循环）
        if len(state.signals_history) >= 4:
            recent = state.signals_history[-4:]
            if all(s["signal"] == recent[0]["signal"] for s in recent):
                if recent[0]["signal"] in ["error", "test_fail"]:
                    state.blocked = True
                    state.block_reason = f"重复 {recent[0]['message']}"
                    return

        # 检查是否有错误关键词
        if re.search(r'(blocked|stuck|waiting.*input|需要.*确认)', text, re.IGNORECASE):
            state.blocked = True
            state.block_reason = "等待外部输入"

    def _calculate_overall_progress(self, state):
        """计算整体进度"""
        # 基于阶段权重计算
        stage_weight = STAGE_WEIGHTS.get(state.current_stage, 0.1)

        # 已完成阶段的累计进度
        completed_progress = 0.0
        stage_order = ["planning", "coding", "testing", "fixing", "refining", "reviewing", "documenting", "release"]

        current_index = -1
        if state.current_stage in stage_order:
            current_index = stage_order.index(state.current_stage)

        for i, stage in enumerate(stage_order):
            if i < current_index:
                completed_progress += STAGE_WEIGHTS.get(stage, 0.1)

        # 当前阶段的进度贡献
        current_contribution = stage_weight * state.stage_progress

        state.overall_progress = min(1.0, completed_progress + current_contribution)

        # 特殊情况：如果阶段是 done，直接设为 100%
        if state.current_stage == "done":
            state.overall_progress = 1.0

    def _save_state(self, state):
        """保存状态"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO progress_state
                (session_id, current_stage, overall_progress, stage_progress,
                 last_signal, last_signal_time, signals_history, blocked,
                 block_reason, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                state.session_id,
                state.current_stage,
                state.overall_progress,
                state.stage_progress,
                state.last_signal,
                state.last_signal_time,
                json.dumps(state.signals_history, ensure_ascii=False),
                1 if state.blocked else 0,
                state.block_reason,
                state.started_at,
                state.updated_at,
            ))

    def get_status(self, session_id):
        """获取当前状态"""
        return self._get_state(session_id)

    def get_summary(self, session_id):
        """获取进度摘要（用于 LLM 上下文）"""
        state = self._get_state(session_id)
        if state.overall_progress == 0 and not state.last_signal:
            return ""
        return state.to_context_string()

    def generate_report(self, session_id):
        """生成详细报告"""
        state = self._get_state(session_id)

        report = {
            "session_id": session_id,
            "summary": {
                "overall_progress": f"{state.overall_progress:.0%}",
                "current_stage": state.current_stage,
                "blocked": state.blocked,
                "block_reason": state.block_reason,
            },
            "timeline": [],
            "statistics": {
                "positive_signals": 0,
                "negative_signals": 0,
                "duration_minutes": (state.updated_at - state.started_at) // 60,
            },
        }

        # 统计信号
        for signal in state.signals_history:
            signal_name = signal.get("signal", "")
            if signal_name in [s for configs in PROGRESS_SIGNALS.get("positive", {}).values() for s in [configs.get("name", "")]]:
                report["statistics"]["positive_signals"] += 1
            elif signal_name in [s for configs in PROGRESS_SIGNALS.get("negative", {}).values() for s in [configs.get("name", "")]]:
                report["statistics"]["negative_signals"] += 1

            # 添加到时间线
            report["timeline"].append({
                "time": signal.get("time"),
                "event": signal.get("message"),
            })

        return report

    def reset(self, session_id):
        """重置进度"""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM progress_state WHERE session_id = ?", (session_id,))


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Progress Monitor',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # update
    p_update = subparsers.add_parser('update', help='Update progress from text')
    p_update.add_argument('session_id', help='Session ID')
    p_update.add_argument('text', nargs='?', help='Text to analyze (or read from stdin)')
    p_update.add_argument('--stage', help='Current stage')

    # status
    p_status = subparsers.add_parser('status', help='Get current status')
    p_status.add_argument('session_id', help='Session ID')

    # report
    p_report = subparsers.add_parser('report', help='Generate detailed report')
    p_report.add_argument('session_id', help='Session ID')

    # summary
    p_summary = subparsers.add_parser('summary', help='Get progress summary for LLM context')
    p_summary.add_argument('session_id', help='Session ID')

    # reset
    p_reset = subparsers.add_parser('reset', help='Reset progress')
    p_reset.add_argument('session_id', help='Session ID')

    args = parser.parse_args(argv)
    pm = ProgressMonitor()

    try:
        if args.command == 'update':
            text = args.text
            if not text:
                text = sys.stdin.read()

            state = pm.update(args.session_id, text, args.stage)
            print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False))

        elif args.command == 'status':
            state = pm.get_status(args.session_id)
            print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False))

        elif args.command == 'report':
            report = pm.generate_report(args.session_id)
            print(json.dumps(report, indent=2, ensure_ascii=False))

        elif args.command == 'summary':
            summary = pm.get_summary(args.session_id)
            if summary:
                print(summary)

        elif args.command == 'reset':
            pm.reset(args.session_id)
            print(f"Progress for {args.session_id} has been reset")

        else:
            parser.print_help()
            return 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
