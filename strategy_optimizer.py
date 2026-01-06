#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Strategy Optimizer
策略优化器 - 评估和优化决策策略

功能：
1. 策略评估（成功率/效率/风险评估）
2. 策略调整（参数调整/组合优化/新策略生成）
3. A/B测试（多策略并行/效果对比/最优选择）
4. 策略退化检测（有效性监控/环境变化/重训练触发）

Usage:
    python3 strategy_optimizer.py evaluate <strategy_id>
    python3 strategy_optimizer.py adjust <strategy_id> [--feedback <feedback_data>]
    python3 strategy_optimizer.py compare <strategy_a> <strategy_b>
    python3 strategy_optimizer.py recommend [--context <context_json>]
    python3 strategy_optimizer.py decay-check [--threshold 0.3]
"""

import argparse
import hashlib
import json
import math
import os
import random
import sqlite3
import sys
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from compat_dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Database path
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))

# Import intelligent engine
try:
    from intelligent_engine import IntelligentEngine, Event
    INTELLIGENT_ENGINE_AVAILABLE = True
except ImportError:
    INTELLIGENT_ENGINE_AVAILABLE = False


class StrategyType(Enum):
    """Strategy type classification"""
    WAIT = "wait"                      # Wait for completion
    NUDGE = "nudge"                    # Gentle reminder
    COMMAND = "command"                # Direct command
    ASK = "ask"                        # Ask for clarification
    NOTIFY = "notify"                  # Send notification
    ESCALATE = "escalate"              # Escalate to user
    COMPOSITE = "composite"            # Combination of strategies


class StrategyStatus(Enum):
    """Strategy lifecycle status"""
    ACTIVE = "active"                  # Currently in use
    TESTING = "testing"                # Being A/B tested
    DEPRECATED = "deprecated"          # No longer recommended
    RETIRED = "retired"                # Removed from use


@dataclass
class Strategy:
    """Decision strategy"""
    strategy_id: str = ""
    strategy_type: StrategyType = StrategyType.WAIT
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    trigger_conditions: Dict[str, Any] = field(default_factory=dict)
    action_template: str = ""
    status: StrategyStatus = StrategyStatus.ACTIVE

    # Performance metrics
    total_uses: int = 0
    success_count: int = 0
    failure_count: int = 0
    avg_time_to_resolution: float = 0.0
    avg_user_satisfaction: float = 0.5

    # Confidence and versioning
    confidence: float = 0.5
    version: int = 1
    parent_id: Optional[str] = None
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> Dict:
        return {
            "strategy_id": self.strategy_id,
            "strategy_type": self.strategy_type.value,
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "trigger_conditions": self.trigger_conditions,
            "action_template": self.action_template,
            "status": self.status.value,
            "total_uses": self.total_uses,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": self.success_rate,
            "avg_time_to_resolution": self.avg_time_to_resolution,
            "avg_user_satisfaction": self.avg_user_satisfaction,
            "confidence": self.confidence,
            "version": self.version,
            "parent_id": self.parent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @property
    def success_rate(self) -> float:
        if self.total_uses == 0:
            return 0.5
        return self.success_count / self.total_uses


@dataclass
class StrategyEvaluation:
    """Strategy evaluation result"""
    strategy_id: str
    success_rate: float
    efficiency_score: float          # Based on time to resolution
    risk_score: float                # Based on failure impact
    overall_score: float
    sample_size: int
    confidence_interval: Tuple[float, float]
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "strategy_id": self.strategy_id,
            "success_rate": round(self.success_rate, 3),
            "efficiency_score": round(self.efficiency_score, 3),
            "risk_score": round(self.risk_score, 3),
            "overall_score": round(self.overall_score, 3),
            "sample_size": self.sample_size,
            "confidence_interval": [round(x, 3) for x in self.confidence_interval],
            "recommendations": self.recommendations,
        }


@dataclass
class ABTestResult:
    """A/B test comparison result"""
    strategy_a_id: str
    strategy_b_id: str
    winner: Optional[str]
    a_score: float
    b_score: float
    difference: float
    statistical_significance: float
    sample_size_a: int
    sample_size_b: int
    recommendation: str

    def to_dict(self) -> Dict:
        return {
            "strategy_a_id": self.strategy_a_id,
            "strategy_b_id": self.strategy_b_id,
            "winner": self.winner,
            "a_score": round(self.a_score, 3),
            "b_score": round(self.b_score, 3),
            "difference": round(self.difference, 3),
            "statistical_significance": round(self.statistical_significance, 3),
            "sample_size_a": self.sample_size_a,
            "sample_size_b": self.sample_size_b,
            "recommendation": self.recommendation,
        }


class StrategyOptimizer:
    """Strategy optimization engine"""

    # Default strategies
    DEFAULT_STRATEGIES = [
        {
            "strategy_type": "wait",
            "name": "Patient Wait",
            "description": "Wait for natural completion without intervention",
            "parameters": {"max_wait_seconds": 300, "check_interval": 5},
            "trigger_conditions": {"stage": "working", "has_progress": True},
            "action_template": "WAIT",
        },
        {
            "strategy_type": "nudge",
            "name": "Gentle Reminder",
            "description": "Send a gentle reminder about the current task",
            "parameters": {"message_tone": "friendly", "include_suggestion": True},
            "trigger_conditions": {"idle_seconds": 60, "stage": "stuck"},
            "action_template": "Consider checking {current_issue}",
        },
        {
            "strategy_type": "command",
            "name": "Direct Command",
            "description": "Suggest a specific command to execute",
            "parameters": {"auto_execute": False, "require_confirmation": True},
            "trigger_conditions": {"error_detected": True, "fix_known": True},
            "action_template": "Try: {suggested_command}",
        },
        {
            "strategy_type": "ask",
            "name": "Clarification Request",
            "description": "Ask user for more information",
            "parameters": {"question_style": "specific"},
            "trigger_conditions": {"ambiguous_intent": True},
            "action_template": "Could you clarify: {question}",
        },
        {
            "strategy_type": "escalate",
            "name": "User Escalation",
            "description": "Escalate to user for manual intervention",
            "parameters": {"urgency": "medium", "include_context": True},
            "trigger_conditions": {"stuck_duration": 300, "attempts": 3},
            "action_template": "Needs attention: {issue_summary}",
        },
    ]

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    def _ensure_db(self):
        """Ensure database exists with proper schema"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS strategies (
                    strategy_id TEXT PRIMARY KEY,
                    strategy_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    parameters TEXT,
                    trigger_conditions TEXT,
                    action_template TEXT,
                    status TEXT DEFAULT 'active',
                    total_uses INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    avg_time_to_resolution REAL DEFAULT 0,
                    avg_user_satisfaction REAL DEFAULT 0.5,
                    confidence REAL DEFAULT 0.5,
                    version INTEGER DEFAULT 1,
                    parent_id TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_usage (
                    usage_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    session_id TEXT,
                    context TEXT,
                    outcome TEXT,
                    time_to_resolution INTEGER,
                    user_satisfaction REAL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE TABLE IF NOT EXISTS ab_tests (
                    test_id TEXT PRIMARY KEY,
                    strategy_a_id TEXT NOT NULL,
                    strategy_b_id TEXT NOT NULL,
                    status TEXT DEFAULT 'running',
                    allocation_ratio REAL DEFAULT 0.5,
                    min_sample_size INTEGER DEFAULT 30,
                    a_uses INTEGER DEFAULT 0,
                    b_uses INTEGER DEFAULT 0,
                    a_successes INTEGER DEFAULT 0,
                    b_successes INTEGER DEFAULT 0,
                    winner TEXT,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    FOREIGN KEY (strategy_a_id) REFERENCES strategies(strategy_id),
                    FOREIGN KEY (strategy_b_id) REFERENCES strategies(strategy_id)
                );

                CREATE INDEX IF NOT EXISTS idx_strategies_type
                    ON strategies(strategy_type);
                CREATE INDEX IF NOT EXISTS idx_strategies_status
                    ON strategies(status);
                CREATE INDEX IF NOT EXISTS idx_usage_strategy
                    ON strategy_usage(strategy_id);
                CREATE INDEX IF NOT EXISTS idx_usage_session
                    ON strategy_usage(session_id);
            """)

            # Initialize default strategies if empty
            count = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
            if count == 0:
                self._initialize_default_strategies(conn)

    def _initialize_default_strategies(self, conn: sqlite3.Connection):
        """Initialize default strategies"""
        now = int(time.time())
        for strategy_data in self.DEFAULT_STRATEGIES:
            strategy_id = str(uuid.uuid4())[:8]
            conn.execute("""
                INSERT INTO strategies (
                    strategy_id, strategy_type, name, description,
                    parameters, trigger_conditions, action_template,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """, (
                strategy_id,
                strategy_data["strategy_type"],
                strategy_data["name"],
                strategy_data["description"],
                json.dumps(strategy_data["parameters"]),
                json.dumps(strategy_data["trigger_conditions"]),
                strategy_data["action_template"],
                now, now
            ))

    @contextmanager
    def _get_conn(self):
        """Get database connection"""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _row_to_strategy(self, row: sqlite3.Row) -> Strategy:
        """Convert database row to Strategy object"""
        return Strategy(
            strategy_id=row["strategy_id"],
            strategy_type=StrategyType(row["strategy_type"]),
            name=row["name"],
            description=row["description"] or "",
            parameters=json.loads(row["parameters"] or "{}"),
            trigger_conditions=json.loads(row["trigger_conditions"] or "{}"),
            action_template=row["action_template"] or "",
            status=StrategyStatus(row["status"] or "active"),
            total_uses=row["total_uses"] or 0,
            success_count=row["success_count"] or 0,
            failure_count=row["failure_count"] or 0,
            avg_time_to_resolution=row["avg_time_to_resolution"] or 0,
            avg_user_satisfaction=row["avg_user_satisfaction"] or 0.5,
            confidence=row["confidence"] or 0.5,
            version=row["version"] or 1,
            parent_id=row["parent_id"],
            created_at=row["created_at"] or 0,
            updated_at=row["updated_at"] or 0,
        )

    # ==================== Strategy Evaluation ====================

    def evaluate(self, strategy_id: str) -> StrategyEvaluation:
        """Evaluate a strategy's performance"""
        with self._get_conn() as conn:
            strategy = conn.execute(
                "SELECT * FROM strategies WHERE strategy_id = ?",
                (strategy_id,)
            ).fetchone()

            if not strategy:
                raise ValueError(f"Strategy {strategy_id} not found")

            strategy_obj = self._row_to_strategy(strategy)

            # Calculate success rate
            success_rate = strategy_obj.success_rate

            # Calculate efficiency score (based on time to resolution)
            efficiency_score = self._calculate_efficiency(strategy_obj)

            # Calculate risk score (based on failure patterns)
            risk_score = self._calculate_risk(strategy_obj)

            # Calculate overall score (weighted combination)
            overall_score = (
                success_rate * 0.4 +
                efficiency_score * 0.3 +
                (1 - risk_score) * 0.2 +
                strategy_obj.avg_user_satisfaction * 0.1
            )

            # Calculate confidence interval
            ci = self._wilson_confidence_interval(
                strategy_obj.success_count,
                strategy_obj.total_uses
            )

            # Generate recommendations
            recommendations = self._generate_recommendations(
                strategy_obj, success_rate, efficiency_score, risk_score
            )

            return StrategyEvaluation(
                strategy_id=strategy_id,
                success_rate=success_rate,
                efficiency_score=efficiency_score,
                risk_score=risk_score,
                overall_score=overall_score,
                sample_size=strategy_obj.total_uses,
                confidence_interval=ci,
                recommendations=recommendations,
            )

    def _calculate_efficiency(self, strategy: Strategy) -> float:
        """Calculate efficiency score based on time to resolution"""
        if strategy.avg_time_to_resolution <= 0:
            return 0.5

        # Lower time = higher efficiency
        # Normalize: 30s = 1.0, 300s = 0.5, 600s+ = 0.2
        time_seconds = strategy.avg_time_to_resolution
        if time_seconds <= 30:
            return 1.0
        elif time_seconds <= 300:
            return 1.0 - (time_seconds - 30) / 540
        else:
            return max(0.2, 0.5 - (time_seconds - 300) / 600)

    def _calculate_risk(self, strategy: Strategy) -> float:
        """Calculate risk score based on failure patterns"""
        if strategy.total_uses == 0:
            return 0.5

        failure_rate = strategy.failure_count / strategy.total_uses

        # Check for recent failure spikes
        # (Would need time-series data for more accurate assessment)

        return failure_rate

    def _wilson_confidence_interval(
        self,
        successes: int,
        total: int,
        confidence: float = 0.95
    ) -> Tuple[float, float]:
        """Calculate Wilson score confidence interval"""
        if total <= 0:
            return (0.0, 1.0)

        z = 1.96 if confidence == 0.95 else 1.645  # 95% or 90%
        p = successes / total
        n = total

        denominator = 1 + z * z / n
        center = (p + z * z / (2 * n)) / denominator
        margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator

        return (max(0, center - margin), min(1, center + margin))

    def _generate_recommendations(
        self,
        strategy: Strategy,
        success_rate: float,
        efficiency_score: float,
        risk_score: float
    ) -> List[str]:
        """Generate recommendations for strategy improvement"""
        recommendations = []

        if success_rate < 0.5 and strategy.total_uses >= 10:
            recommendations.append(
                "Low success rate - consider adjusting trigger conditions"
            )

        if efficiency_score < 0.4 and strategy.total_uses >= 5:
            recommendations.append(
                "Slow resolution - consider more direct intervention"
            )

        if risk_score > 0.3 and strategy.failure_count >= 5:
            recommendations.append(
                "High failure rate - review error patterns and adjust parameters"
            )

        if strategy.avg_user_satisfaction < 0.4:
            recommendations.append(
                "Low user satisfaction - consider softer approach or better timing"
            )

        if strategy.total_uses < 10:
            recommendations.append(
                "Insufficient data - more usage needed for reliable evaluation"
            )

        if not recommendations:
            recommendations.append("Strategy performing well - no changes needed")

        return recommendations

    # ==================== Strategy Adjustment ====================

    def adjust(
        self,
        strategy_id: str,
        feedback: Optional[Dict[str, Any]] = None
    ) -> Strategy:
        """Adjust strategy parameters based on feedback"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM strategies WHERE strategy_id = ?",
                (strategy_id,)
            ).fetchone()

            if not row:
                raise ValueError(f"Strategy {strategy_id} not found")

            strategy = self._row_to_strategy(row)
            params = strategy.parameters.copy()
            modified = False

            # Apply feedback-based adjustments
            if feedback:
                # Adjust timing parameters
                if feedback.get("too_slow"):
                    if "max_wait_seconds" in params:
                        params["max_wait_seconds"] = max(30, params["max_wait_seconds"] * 0.8)
                        modified = True
                elif feedback.get("too_fast"):
                    if "max_wait_seconds" in params:
                        params["max_wait_seconds"] = min(600, params["max_wait_seconds"] * 1.2)
                        modified = True

                # Adjust intervention style
                if feedback.get("too_aggressive"):
                    params["message_tone"] = "gentle"
                    params["require_confirmation"] = True
                    modified = True
                elif feedback.get("too_passive"):
                    params["message_tone"] = "direct"
                    params["auto_execute"] = True
                    modified = True

            # Auto-adjust based on performance
            if strategy.total_uses >= 20:
                if strategy.success_rate < 0.4:
                    # Strategy needs significant adjustment
                    if strategy.strategy_type == StrategyType.WAIT:
                        params["max_wait_seconds"] = max(
                            60, params.get("max_wait_seconds", 300) * 0.7
                        )
                        modified = True
                elif strategy.success_rate > 0.8:
                    # Strategy is working well, slightly relax
                    if "max_wait_seconds" in params:
                        params["max_wait_seconds"] = min(
                            600, params["max_wait_seconds"] * 1.1
                        )
                        modified = True

            if modified:
                # Create new version of strategy
                new_strategy_id = str(uuid.uuid4())[:8]
                now = int(time.time())

                conn.execute("""
                    INSERT INTO strategies (
                        strategy_id, strategy_type, name, description,
                        parameters, trigger_conditions, action_template,
                        status, confidence, version, parent_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'testing', ?, ?, ?, ?, ?)
                """, (
                    new_strategy_id,
                    strategy.strategy_type.value,
                    f"{strategy.name} v{strategy.version + 1}",
                    strategy.description,
                    json.dumps(params),
                    json.dumps(strategy.trigger_conditions),
                    strategy.action_template,
                    0.5,  # Reset confidence for new version
                    strategy.version + 1,
                    strategy_id,
                    now, now
                ))

                return Strategy(
                    strategy_id=new_strategy_id,
                    strategy_type=strategy.strategy_type,
                    name=f"{strategy.name} v{strategy.version + 1}",
                    parameters=params,
                    version=strategy.version + 1,
                    parent_id=strategy_id,
                    status=StrategyStatus.TESTING,
                    created_at=now,
                    updated_at=now,
                )

            return strategy

    # ==================== A/B Testing ====================

    def start_ab_test(
        self,
        strategy_a_id: str,
        strategy_b_id: str,
        min_sample_size: int = 30
    ) -> str:
        """Start A/B test between two strategies"""
        test_id = str(uuid.uuid4())[:8]

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO ab_tests (
                    test_id, strategy_a_id, strategy_b_id,
                    status, min_sample_size, created_at
                ) VALUES (?, ?, ?, 'running', ?, ?)
            """, (test_id, strategy_a_id, strategy_b_id, min_sample_size, int(time.time())))

        return test_id

    def record_ab_outcome(
        self,
        test_id: str,
        variant: str,  # 'a' or 'b'
        success: bool
    ):
        """Record outcome for A/B test variant"""
        with self._get_conn() as conn:
            if variant.lower() == 'a':
                conn.execute("""
                    UPDATE ab_tests SET
                        a_uses = a_uses + 1,
                        a_successes = a_successes + ?
                    WHERE test_id = ?
                """, (1 if success else 0, test_id))
            else:
                conn.execute("""
                    UPDATE ab_tests SET
                        b_uses = b_uses + 1,
                        b_successes = b_successes + ?
                    WHERE test_id = ?
                """, (1 if success else 0, test_id))

            # Check if test is complete
            test = conn.execute(
                "SELECT * FROM ab_tests WHERE test_id = ?",
                (test_id,)
            ).fetchone()

            if test and test["a_uses"] >= test["min_sample_size"] and \
               test["b_uses"] >= test["min_sample_size"]:
                self._complete_ab_test(conn, test_id)

    def _complete_ab_test(self, conn: sqlite3.Connection, test_id: str):
        """Complete an A/B test and determine winner"""
        test = conn.execute(
            "SELECT * FROM ab_tests WHERE test_id = ?",
            (test_id,)
        ).fetchone()

        if not test:
            return

        # Calculate success rates
        a_rate = test["a_successes"] / max(1, test["a_uses"])
        b_rate = test["b_successes"] / max(1, test["b_uses"])

        # Simple significance test (could use proper statistical test)
        difference = abs(a_rate - b_rate)
        winner = None

        if difference > 0.1:  # Require 10% difference for winner
            winner = test["strategy_a_id"] if a_rate > b_rate else test["strategy_b_id"]
            loser = test["strategy_b_id"] if a_rate > b_rate else test["strategy_a_id"]

            # Update strategy statuses
            conn.execute(
                "UPDATE strategies SET status = 'active' WHERE strategy_id = ?",
                (winner,)
            )
            conn.execute(
                "UPDATE strategies SET status = 'deprecated' WHERE strategy_id = ?",
                (loser,)
            )

        conn.execute("""
            UPDATE ab_tests SET
                status = 'completed',
                winner = ?,
                completed_at = ?
            WHERE test_id = ?
        """, (winner, int(time.time()), test_id))

    def compare(self, strategy_a_id: str, strategy_b_id: str) -> ABTestResult:
        """Compare two strategies based on historical data"""
        with self._get_conn() as conn:
            a = conn.execute(
                "SELECT * FROM strategies WHERE strategy_id = ?",
                (strategy_a_id,)
            ).fetchone()
            b = conn.execute(
                "SELECT * FROM strategies WHERE strategy_id = ?",
                (strategy_b_id,)
            ).fetchone()

            if not a or not b:
                raise ValueError("Strategy not found")

            a_strategy = self._row_to_strategy(a)
            b_strategy = self._row_to_strategy(b)

            a_score = a_strategy.success_rate
            b_score = b_strategy.success_rate
            difference = a_score - b_score

            # Calculate statistical significance (simplified)
            min_uses = min(a_strategy.total_uses, b_strategy.total_uses)
            significance = min(1.0, abs(difference) * math.sqrt(min_uses / 10))

            # Determine winner
            if significance > 0.7 and abs(difference) > 0.1:
                winner = strategy_a_id if difference > 0 else strategy_b_id
                recommendation = f"Use {winner} - significantly better performance"
            elif min_uses < 20:
                winner = None
                recommendation = "Insufficient data - continue testing"
            else:
                winner = None
                recommendation = "No significant difference - either strategy works"

            return ABTestResult(
                strategy_a_id=strategy_a_id,
                strategy_b_id=strategy_b_id,
                winner=winner,
                a_score=a_score,
                b_score=b_score,
                difference=difference,
                statistical_significance=significance,
                sample_size_a=a_strategy.total_uses,
                sample_size_b=b_strategy.total_uses,
                recommendation=recommendation,
            )

    # ==================== Strategy Selection ====================

    def recommend(
        self,
        context: Optional[Dict[str, Any]] = None
    ) -> List[Strategy]:
        """Recommend strategies for current context"""
        context = context or {}

        with self._get_conn() as conn:
            strategies = conn.execute("""
                SELECT * FROM strategies
                WHERE status = 'active'
                ORDER BY confidence DESC, success_count DESC
            """).fetchall()

            scored = []
            for row in strategies:
                strategy = self._row_to_strategy(row)
                score = self._score_for_context(strategy, context)
                scored.append((strategy, score))

            # Sort by score and return top 3
            scored.sort(key=lambda x: x[1], reverse=True)
            return [s[0] for s, _ in scored[:3]]

    def _score_for_context(
        self,
        strategy: Strategy,
        context: Dict[str, Any]
    ) -> float:
        """Score a strategy for a given context"""
        score = strategy.confidence

        # Check trigger condition matches
        conditions = strategy.trigger_conditions
        match_count = 0
        total_conditions = len(conditions)

        for key, expected in conditions.items():
            if key in context:
                if context[key] == expected:
                    match_count += 1
                elif isinstance(expected, bool) and context[key]:
                    match_count += 0.5

        if total_conditions > 0:
            condition_score = match_count / total_conditions
            score *= (0.5 + 0.5 * condition_score)

        return score

    def get_strategy_for_situation(
        self,
        situation: str
    ) -> Optional[Strategy]:
        """Get best strategy for a situation description"""
        # Map situations to strategy types
        situation_lower = situation.lower()

        situation_map = {
            StrategyType.WAIT: ["working", "progress", "running", "executing"],
            StrategyType.NUDGE: ["stuck", "idle", "waiting", "slow"],
            StrategyType.COMMAND: ["error", "failed", "fix", "solve"],
            StrategyType.ASK: ["unclear", "ambiguous", "question", "clarify"],
            StrategyType.ESCALATE: ["blocked", "urgent", "critical", "manual"],
        }

        best_type = StrategyType.WAIT
        for strategy_type, keywords in situation_map.items():
            if any(kw in situation_lower for kw in keywords):
                best_type = strategy_type
                break

        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM strategies
                WHERE strategy_type = ? AND status = 'active'
                ORDER BY confidence DESC
                LIMIT 1
            """, (best_type.value,)).fetchone()

            if row:
                return self._row_to_strategy(row)

        return None

    # ==================== Decay Detection ====================

    def check_decay(self, threshold: float = 0.3) -> List[Dict[str, Any]]:
        """Check for strategies showing performance decay"""
        decaying = []

        with self._get_conn() as conn:
            strategies = conn.execute("""
                SELECT * FROM strategies
                WHERE status = 'active' AND total_uses >= 20
            """).fetchall()

            for row in strategies:
                strategy = self._row_to_strategy(row)

                # Get recent usage
                recent = conn.execute("""
                    SELECT outcome, COUNT(*) as count
                    FROM strategy_usage
                    WHERE strategy_id = ?
                      AND created_at > ?
                    GROUP BY outcome
                """, (strategy.strategy_id, int(time.time()) - 604800)).fetchall()

                if not recent:
                    continue

                recent_success = sum(
                    r["count"] for r in recent if r["outcome"] == "success"
                )
                recent_total = sum(r["count"] for r in recent)

                if recent_total < 5:
                    continue

                recent_rate = recent_success / recent_total
                overall_rate = strategy.success_rate

                # Check for decay
                if overall_rate - recent_rate > threshold:
                    decaying.append({
                        "strategy_id": strategy.strategy_id,
                        "name": strategy.name,
                        "overall_rate": round(overall_rate, 3),
                        "recent_rate": round(recent_rate, 3),
                        "decay": round(overall_rate - recent_rate, 3),
                        "recommendation": "Consider retraining or adjusting parameters",
                    })

        return decaying

    def record_usage(
        self,
        strategy_id: str,
        session_id: str,
        outcome: str,
        time_to_resolution: Optional[int] = None,
        user_satisfaction: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        """Record strategy usage for analysis"""
        usage_id = str(uuid.uuid4())[:8]

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO strategy_usage (
                    usage_id, strategy_id, session_id, context,
                    outcome, time_to_resolution, user_satisfaction, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                usage_id,
                strategy_id,
                session_id,
                json.dumps(context or {}),
                outcome,
                time_to_resolution,
                user_satisfaction,
                int(time.time())
            ))

            # Update strategy statistics
            is_success = outcome in ["success", "resolved", "completed"]
            conn.execute("""
                UPDATE strategies SET
                    total_uses = total_uses + 1,
                    success_count = success_count + ?,
                    failure_count = failure_count + ?,
                    updated_at = ?
                WHERE strategy_id = ?
            """, (
                1 if is_success else 0,
                0 if is_success else 1,
                int(time.time()),
                strategy_id
            ))


# ==================== CLI Interface ====================

def _parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Claude Monitor Strategy Optimizer"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # evaluate command
    eval_parser = subparsers.add_parser("evaluate", help="Evaluate strategy")
    eval_parser.add_argument("strategy_id", help="Strategy ID")

    # adjust command
    adjust_parser = subparsers.add_parser("adjust", help="Adjust strategy")
    adjust_parser.add_argument("strategy_id", help="Strategy ID")
    adjust_parser.add_argument("--feedback", help="Feedback JSON")

    # compare command
    compare_parser = subparsers.add_parser("compare", help="Compare strategies")
    compare_parser.add_argument("strategy_a", help="Strategy A ID")
    compare_parser.add_argument("strategy_b", help="Strategy B ID")

    # recommend command
    recommend_parser = subparsers.add_parser("recommend", help="Get recommendations")
    recommend_parser.add_argument("--context", help="Context JSON")

    # decay-check command
    decay_parser = subparsers.add_parser("decay-check", help="Check for decay")
    decay_parser.add_argument(
        "--threshold", type=float, default=0.3,
        help="Decay threshold"
    )

    # list command
    subparsers.add_parser("list", help="List all strategies")

    # suggest command (快捷命令：根据阶段建议策略)
    suggest_parser = subparsers.add_parser("suggest", help="Suggest strategy for stage")
    suggest_parser.add_argument("stage", help="Current stage (e.g., testing, fixing)")

    # record command (快捷命令：记录策略使用结果)
    record_parser = subparsers.add_parser("record", help="Record strategy usage")
    record_parser.add_argument("stage", help="Stage when strategy was used")
    record_parser.add_argument("action", help="Action taken")
    record_parser.add_argument("outcome", choices=["success", "failure", "wait"], help="Outcome")

    # intelligent command (智能引擎推荐)
    intel_parser = subparsers.add_parser("intelligent", help="Get intelligent recommendation")
    intel_parser.add_argument("--stage", default="unknown", help="Current stage")
    intel_parser.add_argument("--events", help="Events JSON array")
    intel_parser.add_argument("--window-size", type=int, default=20, help="Pattern detection window")
    intel_parser.add_argument("--memory-size", type=int, default=50, help="Memory capacity")
    intel_parser.add_argument("--aggressiveness", type=float, default=0.5, help="Initial aggressiveness (0.0-1.0)")

    # intelligent-status command (智能引擎状态)
    subparsers.add_parser("intelligent-status", help="Show intelligent engine status")

    return parser.parse_args()


def _cmd_evaluate(optimizer, args):
    """Handle evaluate command"""
    evaluation = optimizer.evaluate(args.strategy_id)
    print(json.dumps(evaluation.to_dict(), indent=2))


def _cmd_adjust(optimizer, args):
    """Handle adjust command"""
    feedback = json.loads(args.feedback) if args.feedback else None
    strategy = optimizer.adjust(args.strategy_id, feedback)
    print(json.dumps(strategy.to_dict(), indent=2))


def _cmd_compare(optimizer, args):
    """Handle compare command"""
    result = optimizer.compare(args.strategy_a, args.strategy_b)
    print(json.dumps(result.to_dict(), indent=2))


def _cmd_recommend(optimizer, args):
    """Handle recommend command"""
    context = json.loads(args.context) if args.context else {}
    strategies = optimizer.recommend(context)
    print(json.dumps([s.to_dict() for s in strategies], indent=2))


def _cmd_decay_check(optimizer, args):
    """Handle decay-check command"""
    decaying = optimizer.check_decay(args.threshold)
    print(json.dumps(decaying, indent=2))


def _cmd_list(optimizer, args):
    """Handle list command"""
    with optimizer._get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM strategies ORDER BY status, confidence DESC"
        ).fetchall()
        strategies = [optimizer._row_to_strategy(r).to_dict() for r in rows]
        print(json.dumps(strategies, indent=2))


def _cmd_suggest(optimizer, args):
    """Handle suggest command"""
    # 根据阶段查找最佳策略
    context = {"stage": args.stage}
    strategies = optimizer.recommend(context)
    if strategies:
        best = strategies[0]
        # 输出简洁建议供 shell 使用
        print(f"推荐策略: {best.name} (confidence={best.confidence:.2f})")
    # 无输出表示无建议


def _cmd_record(optimizer, args):
    """Handle record command"""
    # 查找或创建对应阶段的策略
    strategy_id = f"stage_{args.stage}"
    with optimizer._get_conn() as conn:
        existing = conn.execute(
            "SELECT strategy_id FROM strategies WHERE strategy_id = ?",
            (strategy_id,)
        ).fetchone()

        if not existing:
            # 创建新策略
            conn.execute("""
                INSERT INTO strategies (
                    strategy_id, name, description, context_tags, status,
                    confidence, total_uses, success_count, failure_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'active', 0.5, 0, 0, 0, ?, ?)
            """, (
                strategy_id,
                f"Stage {args.stage} strategy",
                f"Auto-created strategy for {args.stage} stage",
                json.dumps([args.stage]),
                int(time.time()),
                int(time.time())
            ))

    # 记录使用结果
    optimizer.record_usage(strategy_id, args.action, args.outcome)


def _cmd_intelligent(optimizer, args):
    """Handle intelligent command - use intelligent engine for recommendations"""
    if not INTELLIGENT_ENGINE_AVAILABLE:
        print(json.dumps({
            "error": "Intelligent engine not available",
            "message": "Install intelligent_engine.py to use this feature"
        }, indent=2))
        return

    # Create or load intelligent engine
    engine = IntelligentEngine(
        pattern_window_size=getattr(args, 'window_size', 20),
        memory_max_items=getattr(args, 'memory_size', 50),
        initial_aggressiveness=getattr(args, 'aggressiveness', 0.5)
    )

    # Add events if provided
    if hasattr(args, 'events') and args.events:
        try:
            events = json.loads(args.events)
            for event_data in events:
                engine.add_event(
                    event_type=event_data.get('type', 'output'),
                    content=event_data.get('content', ''),
                    metadata=event_data.get('metadata', {})
                )
        except json.JSONDecodeError:
            pass

    # Get recommendation
    stage = getattr(args, 'stage', 'unknown')
    recommendation = engine.analyze_and_recommend(stage)

    print(json.dumps(recommendation, indent=2))


def _cmd_intelligent_status(optimizer, args):
    """Handle intelligent-status command - show intelligent engine status"""
    if not INTELLIGENT_ENGINE_AVAILABLE:
        print(json.dumps({
            "error": "Intelligent engine not available"
        }, indent=2))
        return

    # Create engine and get status
    engine = IntelligentEngine()
    status = engine.get_status()

    print(json.dumps(status, indent=2))


def main():
    """Main entry point"""
    args = _parse_args()
    optimizer = StrategyOptimizer()

    if args.command == "evaluate":
        _cmd_evaluate(optimizer, args)
    elif args.command == "adjust":
        _cmd_adjust(optimizer, args)
    elif args.command == "compare":
        _cmd_compare(optimizer, args)
    elif args.command == "recommend":
        _cmd_recommend(optimizer, args)
    elif args.command == "decay-check":
        _cmd_decay_check(optimizer, args)
    elif args.command == "list":
        _cmd_list(optimizer, args)
    elif args.command == "suggest":
        _cmd_suggest(optimizer, args)
    elif args.command == "record":
        _cmd_record(optimizer, args)
    elif args.command == "intelligent":
        _cmd_intelligent(optimizer, args)
    elif args.command == "intelligent-status":
        _cmd_intelligent_status(optimizer, args)
    else:
        _parse_args().parse_args(["--help"])


if __name__ == "__main__":
    main()
