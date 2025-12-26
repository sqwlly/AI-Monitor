#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Quality Assessor
自我评估系统 - 监工AI评估主力AI的输出质量

Usage:
    python3 quality_assessor.py assess --session <session_id> [--history JSON]
    python3 quality_assessor.py add-round --session <session_id> --stage <stage> --output <output> --outcome <outcome>
    python3 quality_assessor.py status --session <session_id>
    python3 quality_assessor.py config show

Issue Types:
    stuck        - 连续多轮WAIT，无进展
    loop         - 输出完全重复，疑似死循环
    error_repeat - 同一错误反复出现
    goal_drift   - 偏离原始目标
    low_quality  - 输出质量低下

Actions:
    continue     - 正常继续
    switch_role  - 自动切换角色
    pause        - 暂停并记录
    alert_human  - 触发人工通知
"""

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

try:
    from typing import Optional, List, Dict, Any
except ImportError:
    pass

# 默认配置
DEFAULT_CONFIG_DIR = Path.home() / ".tmux-monitor" / "config"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "assessment.json"
DEFAULT_STATE_DIR = Path.home() / ".tmux-monitor" / "assessment"

# 环境变量
CONFIG_PATH = Path(os.environ.get("AI_MONITOR_ASSESSMENT_CONFIG", str(DEFAULT_CONFIG_PATH)))
ASSESSMENT_ENABLED = os.environ.get("AI_MONITOR_ASSESSMENT_ENABLED", "0") == "1"
ASSESSMENT_INTERVAL = int(os.environ.get("AI_MONITOR_ASSESSMENT_INTERVAL", "5"))


class Issue:
    """评估发现的问题"""
    def __init__(self, issue_type, severity, description, evidence=None):
        self.type = issue_type
        self.severity = severity  # low/medium/high/critical
        self.description = description
        self.evidence = evidence or ""

    def to_dict(self):
        return {
            "type": self.type,
            "severity": self.severity,
            "description": self.description,
            "evidence": self.evidence
        }


class Recommendation:
    """评估建议"""
    def __init__(self, action, reason, suggested_role=None):
        self.action = action  # continue/switch_role/pause/alert_human
        self.suggested_role = suggested_role
        self.reason = reason

    def to_dict(self):
        return {
            "action": self.action,
            "suggested_role": self.suggested_role,
            "reason": self.reason
        }


class AssessmentResult:
    """评估结果"""
    def __init__(self, scores=None, issues=None, recommendation=None):
        self.timestamp = int(time.time())
        self.scores = scores or {}
        self.issues = issues or []
        self.recommendation = recommendation

    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "scores": self.scores,
            "issues": [i.to_dict() for i in self.issues],
            "recommendation": self.recommendation.to_dict() if self.recommendation else None
        }


class QualityAssessor:
    """质量评估器"""

    def __init__(self, config_path=None):
        self.config_path = config_path or CONFIG_PATH
        self.config = self._load_config()
        self.history = []  # 最近N轮的记录
        self.max_history = self.config.get('max_history', 20)

    def _load_config(self):
        """加载配置"""
        if self.config_path.exists():
            try:
                with open(str(self.config_path), 'r') as f:
                    return json.load(f)
            except Exception:
                pass

        return self._get_default_config()

    def _get_default_config(self):
        """获取默认配置"""
        return {
            "enabled": True,
            "max_history": 20,
            "thresholds": {
                "stuck_rounds": 3,           # 连续WAIT多少轮算卡住
                "same_output_count": 4,      # 相同输出多少次算死循环
                "error_repeat_count": 3,     # 同一错误多少次算重复错误
                "max_idle_minutes": 5        # 最大空闲分钟数
            },
            "role_suggestions": {
                "stuck_in_coding": "senior-engineer",
                "stuck_in_testing": "test-manager",
                "stuck_in_planning": "architect",
                "error_repeat": "senior-engineer",
                "default": "monitor"
            }
        }

    def add_round(self, stage, role, output, outcome, input_preview=None):
        """添加一轮记录"""
        record = {
            "stage": stage,
            "role": role,
            "output": output,
            "outcome": outcome,
            "input_preview": input_preview,
            "output_hash": hashlib.sha256(output.encode()).hexdigest()[:16],
            "timestamp": int(time.time())
        }
        self.history.append(record)

        # 保持历史记录在限制内
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def load_history(self, history_data):
        """加载历史记录（JSON格式）"""
        if isinstance(history_data, str):
            history_data = json.loads(history_data)

        for record in history_data:
            if 'output_hash' not in record:
                record['output_hash'] = hashlib.sha256(
                    record.get('output', '').encode()
                ).hexdigest()[:16]
            if 'timestamp' not in record:
                record['timestamp'] = int(time.time())

        self.history = history_data[-self.max_history:]

    def assess(self, goal=None):
        """执行评估，返回 AssessmentResult"""
        issues = []
        scores = {
            "progress": 1.0,
            "efficiency": 1.0,
            "stability": 1.0
        }

        # 1. 检测卡住（连续WAIT）
        stuck_issue = self._check_stuck()
        if stuck_issue:
            issues.append(stuck_issue)
            scores["progress"] -= 0.3

        # 2. 检测死循环（输出完全重复）
        loop_issue = self._check_loop()
        if loop_issue:
            issues.append(loop_issue)
            scores["stability"] -= 0.5

        # 3. 检测重复错误
        error_issue = self._check_error_patterns()
        if error_issue:
            issues.append(error_issue)
            scores["efficiency"] -= 0.3

        # 4. 计算效率分数（action vs wait 比例）
        efficiency = self._calculate_efficiency()
        scores["efficiency"] = min(scores["efficiency"], efficiency)

        # 5. 生成建议
        recommendation = self._generate_recommendation(issues, scores)

        return AssessmentResult(
            scores=scores,
            issues=issues,
            recommendation=recommendation
        )

    def _check_stuck(self):
        """检测是否卡住（连续WAIT）"""
        threshold = self.config.get('thresholds', {}).get('stuck_rounds', 3)

        if len(self.history) < threshold:
            return None

        recent = self.history[-threshold:]
        wait_count = sum(1 for r in recent if r.get('outcome') == 'wait' or
                        r.get('output', '').upper() == 'WAIT')

        if wait_count >= threshold:
            current_stage = recent[-1].get('stage', 'unknown')
            return Issue(
                issue_type="stuck",
                severity="high",
                description="连续{}轮返回WAIT，当前阶段: {}".format(threshold, current_stage),
                evidence=json.dumps([r.get('output', '')[:50] for r in recent], ensure_ascii=False)
            )

        return None

    def _check_loop(self):
        """检测死循环（输出完全重复）"""
        threshold = self.config.get('thresholds', {}).get('same_output_count', 4)

        if len(self.history) < threshold:
            return None

        recent = self.history[-threshold:]
        hashes = [r.get('output_hash') for r in recent]

        # 检查是否全部相同
        if len(set(hashes)) == 1:
            return Issue(
                issue_type="loop",
                severity="critical",
                description="输出完全重复{}次，疑似死循环".format(threshold),
                evidence=recent[-1].get('output', '')[:100]
            )

        # 检查是否有循环模式 (A-B-A-B)
        if len(set(hashes)) <= 2 and len(hashes) >= 4:
            # 检查交替模式
            pattern_a = hashes[::2]  # 偶数位置
            pattern_b = hashes[1::2]  # 奇数位置
            if len(set(pattern_a)) == 1 and len(set(pattern_b)) == 1:
                return Issue(
                    issue_type="loop",
                    severity="high",
                    description="检测到交替循环模式",
                    evidence="Pattern: {} <-> {}".format(
                        recent[0].get('output', '')[:30],
                        recent[1].get('output', '')[:30]
                    )
                )

        return None

    def _check_error_patterns(self):
        """检测重复错误"""
        threshold = self.config.get('thresholds', {}).get('error_repeat_count', 3)

        if len(self.history) < threshold:
            return None

        # 统计错误输出
        error_outputs = []
        for r in self.history[-10:]:  # 检查最近10轮
            outcome = r.get('outcome', '')
            output = r.get('output', '').lower()

            # 检测错误相关输出
            if outcome == 'error' or any(kw in output for kw in ['error', 'failed', 'exception', 'traceback']):
                error_outputs.append(r.get('output_hash'))

        if not error_outputs:
            return None

        # 统计重复
        counter = Counter(error_outputs)
        most_common = counter.most_common(1)

        if most_common and most_common[0][1] >= threshold:
            # 找到重复的错误
            repeated_hash = most_common[0][0]
            for r in reversed(self.history):
                if r.get('output_hash') == repeated_hash:
                    return Issue(
                        issue_type="error_repeat",
                        severity="high",
                        description="同一错误重复出现{}次".format(most_common[0][1]),
                        evidence=r.get('output', '')[:100]
                    )

        return None

    def _calculate_efficiency(self):
        """计算效率分数（action vs wait 比例）"""
        if not self.history:
            return 1.0

        recent = self.history[-10:]  # 最近10轮
        action_count = sum(1 for r in recent if r.get('outcome') != 'wait' and
                          r.get('output', '').upper() != 'WAIT')
        total = len(recent)

        if total == 0:
            return 1.0

        return round(action_count / total, 2)

    def _generate_recommendation(self, issues, scores):
        """生成建议"""
        role_suggestions = self.config.get('role_suggestions', {})

        # 严重问题 -> 通知人类
        if any(i.severity == "critical" for i in issues):
            return Recommendation(
                action="alert_human",
                reason="发现严重问题（死循环或其他critical级别），需要人工介入"
            )

        # 卡住 -> 尝试切换角色
        stuck_issues = [i for i in issues if i.type == "stuck"]
        if stuck_issues:
            # 根据当前阶段推荐角色
            current_stage = "unknown"
            if self.history:
                current_stage = self.history[-1].get('stage', 'unknown')

            suggested_role = role_suggestions.get(
                'stuck_in_{}'.format(current_stage),
                role_suggestions.get('default', 'senior-engineer')
            )

            return Recommendation(
                action="switch_role",
                suggested_role=suggested_role,
                reason="连续卡住，尝试切换到 {} 角色".format(suggested_role)
            )

        # 重复错误 -> 切换角色
        error_issues = [i for i in issues if i.type == "error_repeat"]
        if error_issues:
            suggested_role = role_suggestions.get('error_repeat', 'senior-engineer')
            return Recommendation(
                action="switch_role",
                suggested_role=suggested_role,
                reason="重复错误，尝试切换到 {} 角色".format(suggested_role)
            )

        # 效率太低 -> 警告
        if scores.get('efficiency', 1.0) < 0.3:
            return Recommendation(
                action="alert_human",
                reason="效率过低（action rate < 30%），可能需要人工检查"
            )

        # 一切正常
        return Recommendation(
            action="continue",
            reason="状态正常"
        )

    def get_status(self):
        """获取当前状态摘要"""
        if not self.history:
            return {
                "rounds": 0,
                "last_stage": None,
                "last_output": None,
                "action_rate": 0,
                "issues_detected": []
            }

        recent = self.history[-10:]
        action_count = sum(1 for r in recent if r.get('outcome') != 'wait')

        return {
            "rounds": len(self.history),
            "last_stage": self.history[-1].get('stage'),
            "last_output": self.history[-1].get('output', '')[:50],
            "last_role": self.history[-1].get('role'),
            "action_rate": round(100 * action_count / len(recent), 1) if recent else 0,
            "recent_outputs": [r.get('output', '')[:30] for r in self.history[-5:]]
        }

    def save_config(self):
        """保存配置"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.config_path), 'w') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)


# ==================== 状态持久化 ====================

class AssessmentState:
    """评估状态持久化"""

    def __init__(self, state_dir=None):
        self.state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _get_state_file(self, session_id):
        return self.state_dir / "{}.json".format(session_id)

    def save(self, session_id, assessor):
        """保存评估器状态"""
        state = {
            "session_id": session_id,
            "history": assessor.history,
            "updated_at": int(time.time())
        }
        with open(str(self._get_state_file(session_id)), 'w') as f:
            json.dump(state, f, ensure_ascii=False)

    def load(self, session_id, assessor):
        """加载评估器状态"""
        state_file = self._get_state_file(session_id)
        if state_file.exists():
            with open(str(state_file), 'r') as f:
                state = json.load(f)
                assessor.history = state.get('history', [])
                return True
        return False

    def delete(self, session_id):
        """删除状态"""
        state_file = self._get_state_file(session_id)
        if state_file.exists():
            state_file.unlink()


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Quality Assessor',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # assess
    p_assess = subparsers.add_parser('assess', help='Run assessment')
    p_assess.add_argument('--session', required=True, help='Session ID')
    p_assess.add_argument('--history', help='History JSON (or load from state)')

    # add-round
    p_add = subparsers.add_parser('add-round', help='Add a round to history')
    p_add.add_argument('--session', required=True, help='Session ID')
    p_add.add_argument('--stage', required=True, help='Current stage')
    p_add.add_argument('--role', default='monitor', help='Role used')
    p_add.add_argument('--output', required=True, help='LLM output')
    p_add.add_argument('--outcome', required=True, help='Outcome')

    # status
    p_status = subparsers.add_parser('status', help='Get assessor status')
    p_status.add_argument('--session', required=True, help='Session ID')

    # config
    p_config = subparsers.add_parser('config', help='Manage configuration')
    config_sub = p_config.add_subparsers(dest='config_cmd')
    config_sub.add_parser('show', help='Show current config')
    config_sub.add_parser('init', help='Create default config file')

    args = parser.parse_args(argv)

    assessor = QualityAssessor()
    state = AssessmentState()

    try:
        if args.command == 'assess':
            # 加载历史
            if args.history:
                assessor.load_history(args.history)
            else:
                state.load(args.session, assessor)

            # 执行评估
            result = assessor.assess()
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

        elif args.command == 'add-round':
            # 加载现有状态
            state.load(args.session, assessor)

            # 添加记录
            assessor.add_round(
                stage=args.stage,
                role=args.role,
                output=args.output,
                outcome=args.outcome
            )

            # 保存状态
            state.save(args.session, assessor)
            print("Round added. Total: {} rounds".format(len(assessor.history)))

        elif args.command == 'status':
            state.load(args.session, assessor)
            status = assessor.get_status()
            print(json.dumps(status, indent=2, ensure_ascii=False))

        elif args.command == 'config':
            if args.config_cmd == 'show':
                print(json.dumps(assessor.config, indent=2, ensure_ascii=False))
            elif args.config_cmd == 'init':
                assessor.config = assessor._get_default_config()
                assessor.save_config()
                print("Default config created at: {}".format(assessor.config_path))
            else:
                p_config.print_help()

        else:
            parser.print_help()
            return 1

    except Exception as e:
        print("Error: {}".format(e), file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
