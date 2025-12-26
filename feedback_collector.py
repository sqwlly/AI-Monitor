#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Feedback Collector
反馈收集器 - 收集隐式和显式反馈用于学习

功能：
1. 隐式反馈（命令执行/输出变化/后续行为）
2. 显式反馈（用户中断/覆盖/评价/修正）
3. 反馈归因（决策归因/模式归因/权重计算）
4. 反馈应用（置信度更新/策略调整/学习报告）

Usage:
    python3 feedback_collector.py record <session_id> <decision_id> <feedback_type> [data]
    python3 feedback_collector.py analyze <session_id>
    python3 feedback_collector.py apply <feedback_id>
    python3 feedback_collector.py report [--session <session_id>] [--days 7]
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
from typing import Any, Dict, List, Optional, Tuple

# Database path
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


class FeedbackType(Enum):
    """Feedback type classification"""
    # Implicit feedback (observed from behavior)
    COMMAND_EXECUTED = "command_executed"      # Command was executed
    COMMAND_IGNORED = "command_ignored"        # Command was not executed
    OUTPUT_IMPROVED = "output_improved"        # Output showed improvement
    OUTPUT_WORSENED = "output_worsened"        # Output showed problems
    PROGRESS_MADE = "progress_made"            # Progress toward goal
    STUCK_CONTINUED = "stuck_continued"        # Remained stuck after action

    # Explicit feedback (direct user action)
    USER_INTERRUPT = "user_interrupt"          # User interrupted action
    USER_OVERRIDE = "user_override"            # User overrode suggestion
    USER_CORRECTION = "user_correction"        # User corrected action
    USER_APPROVAL = "user_approval"            # User approved action
    USER_REJECTION = "user_rejection"          # User rejected suggestion


class FeedbackSentiment(Enum):
    """Feedback sentiment"""
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


@dataclass
class Feedback:
    """Feedback record"""
    feedback_id: str = ""
    session_id: str = ""
    decision_id: Optional[str] = None
    pattern_id: Optional[str] = None
    feedback_type: FeedbackType = FeedbackType.COMMAND_EXECUTED
    sentiment: FeedbackSentiment = FeedbackSentiment.NEUTRAL
    weight: float = 1.0                        # Feedback importance weight
    data: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    applied: bool = False
    created_at: int = 0

    def to_dict(self) -> Dict:
        return {
            "feedback_id": self.feedback_id,
            "session_id": self.session_id,
            "decision_id": self.decision_id,
            "pattern_id": self.pattern_id,
            "feedback_type": self.feedback_type.value,
            "sentiment": self.sentiment.value,
            "weight": self.weight,
            "data": self.data,
            "context": self.context,
            "applied": self.applied,
            "created_at": self.created_at,
        }


@dataclass
class FeedbackSummary:
    """Aggregated feedback summary"""
    total_feedback: int = 0
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0
    by_type: Dict[str, int] = field(default_factory=dict)
    decision_scores: Dict[str, float] = field(default_factory=dict)
    pattern_scores: Dict[str, float] = field(default_factory=dict)
    improvement_areas: List[str] = field(default_factory=list)
    strength_areas: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "total_feedback": self.total_feedback,
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "neutral_count": self.neutral_count,
            "positive_ratio": self._positive_ratio,
            "by_type": self.by_type,
            "top_decisions": dict(sorted(
                self.decision_scores.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]),
            "improvement_areas": self.improvement_areas,
            "strength_areas": self.strength_areas,
        }

    @property
    def _positive_ratio(self) -> float:
        if self.total_feedback == 0:
            return 0.5
        return self.positive_count / self.total_feedback


class FeedbackCollector:
    """Feedback collection and analysis engine"""

    # Sentiment mapping for feedback types
    TYPE_SENTIMENTS = {
        FeedbackType.COMMAND_EXECUTED: FeedbackSentiment.POSITIVE,
        FeedbackType.COMMAND_IGNORED: FeedbackSentiment.NEGATIVE,
        FeedbackType.OUTPUT_IMPROVED: FeedbackSentiment.POSITIVE,
        FeedbackType.OUTPUT_WORSENED: FeedbackSentiment.NEGATIVE,
        FeedbackType.PROGRESS_MADE: FeedbackSentiment.POSITIVE,
        FeedbackType.STUCK_CONTINUED: FeedbackSentiment.NEGATIVE,
        FeedbackType.USER_INTERRUPT: FeedbackSentiment.NEGATIVE,
        FeedbackType.USER_OVERRIDE: FeedbackSentiment.NEGATIVE,
        FeedbackType.USER_CORRECTION: FeedbackSentiment.NEUTRAL,
        FeedbackType.USER_APPROVAL: FeedbackSentiment.POSITIVE,
        FeedbackType.USER_REJECTION: FeedbackSentiment.NEGATIVE,
    }

    # Weight multipliers for different feedback types
    TYPE_WEIGHTS = {
        FeedbackType.COMMAND_EXECUTED: 0.8,
        FeedbackType.COMMAND_IGNORED: 1.0,
        FeedbackType.OUTPUT_IMPROVED: 1.2,
        FeedbackType.OUTPUT_WORSENED: 1.5,
        FeedbackType.PROGRESS_MADE: 1.0,
        FeedbackType.STUCK_CONTINUED: 1.2,
        FeedbackType.USER_INTERRUPT: 1.5,
        FeedbackType.USER_OVERRIDE: 1.3,
        FeedbackType.USER_CORRECTION: 1.0,
        FeedbackType.USER_APPROVAL: 1.5,
        FeedbackType.USER_REJECTION: 1.8,
    }

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    def _ensure_db(self):
        """Ensure database exists with proper schema"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    decision_id TEXT,
                    pattern_id TEXT,
                    feedback_type TEXT NOT NULL,
                    sentiment TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    data TEXT,
                    context TEXT,
                    applied INTEGER DEFAULT 0,
                    created_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_feedback_session
                    ON feedback(session_id);
                CREATE INDEX IF NOT EXISTS idx_feedback_decision
                    ON feedback(decision_id);
                CREATE INDEX IF NOT EXISTS idx_feedback_pattern
                    ON feedback(pattern_id);
                CREATE INDEX IF NOT EXISTS idx_feedback_type
                    ON feedback(feedback_type);
                CREATE INDEX IF NOT EXISTS idx_feedback_sentiment
                    ON feedback(sentiment);
                CREATE INDEX IF NOT EXISTS idx_feedback_applied
                    ON feedback(applied);
            """)

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

    def _row_to_feedback(self, row: sqlite3.Row) -> Feedback:
        """Convert database row to Feedback object"""
        return Feedback(
            feedback_id=row["feedback_id"],
            session_id=row["session_id"],
            decision_id=row["decision_id"],
            pattern_id=row["pattern_id"],
            feedback_type=FeedbackType(row["feedback_type"]),
            sentiment=FeedbackSentiment(row["sentiment"]),
            weight=row["weight"] or 1.0,
            data=json.loads(row["data"] or "{}"),
            context=json.loads(row["context"] or "{}"),
            applied=bool(row["applied"]),
            created_at=row["created_at"] or 0,
        )

    # ==================== Feedback Recording ====================

    def record(
        self,
        session_id: str,
        feedback_type: FeedbackType,
        decision_id: Optional[str] = None,
        pattern_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> Feedback:
        """Record a feedback event"""
        feedback = Feedback(
            feedback_id=str(uuid.uuid4())[:8],
            session_id=session_id,
            decision_id=decision_id,
            pattern_id=pattern_id,
            feedback_type=feedback_type,
            sentiment=self.TYPE_SENTIMENTS.get(
                feedback_type, FeedbackSentiment.NEUTRAL
            ),
            weight=self.TYPE_WEIGHTS.get(feedback_type, 1.0),
            data=data or {},
            context=context or {},
            created_at=int(time.time()),
        )

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO feedback (
                    feedback_id, session_id, decision_id, pattern_id,
                    feedback_type, sentiment, weight, data, context,
                    applied, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                feedback.feedback_id,
                feedback.session_id,
                feedback.decision_id,
                feedback.pattern_id,
                feedback.feedback_type.value,
                feedback.sentiment.value,
                feedback.weight,
                json.dumps(feedback.data),
                json.dumps(feedback.context),
                0,
                feedback.created_at,
            ))

        return feedback

    def record_implicit(
        self,
        session_id: str,
        decision_id: str,
        command_suggested: str,
        command_executed: Optional[str],
        output_before: str,
        output_after: str
    ) -> List[Feedback]:
        """Record implicit feedback from observed behavior"""
        feedbacks = []

        # Check command execution
        if command_executed:
            if command_suggested.lower() in command_executed.lower():
                feedback = self.record(
                    session_id=session_id,
                    feedback_type=FeedbackType.COMMAND_EXECUTED,
                    decision_id=decision_id,
                    data={
                        "suggested": command_suggested,
                        "executed": command_executed,
                    }
                )
                feedbacks.append(feedback)
            else:
                feedback = self.record(
                    session_id=session_id,
                    feedback_type=FeedbackType.USER_OVERRIDE,
                    decision_id=decision_id,
                    data={
                        "suggested": command_suggested,
                        "executed": command_executed,
                    }
                )
                feedbacks.append(feedback)
        else:
            feedback = self.record(
                session_id=session_id,
                feedback_type=FeedbackType.COMMAND_IGNORED,
                decision_id=decision_id,
                data={"suggested": command_suggested}
            )
            feedbacks.append(feedback)

        # Check output change
        output_feedback = self._analyze_output_change(
            session_id, decision_id, output_before, output_after
        )
        if output_feedback:
            feedbacks.append(output_feedback)

        return feedbacks

    def _analyze_output_change(
        self,
        session_id: str,
        decision_id: str,
        before: str,
        after: str
    ) -> Optional[Feedback]:
        """Analyze output change to determine feedback type"""
        # Positive indicators
        positive_patterns = [
            r"success", r"passed", r"completed", r"done",
            r"✓", r"✔", r"ok", r"build succeeded",
        ]

        # Negative indicators
        negative_patterns = [
            r"error", r"failed", r"exception", r"traceback",
            r"✗", r"✘", r"denied", r"not found",
        ]

        before_lower = before.lower()
        after_lower = after.lower()

        # Check for improvement
        before_negative = any(re.search(p, before_lower) for p in negative_patterns)
        after_positive = any(re.search(p, after_lower) for p in positive_patterns)
        after_negative = any(re.search(p, after_lower) for p in negative_patterns)

        if before_negative and after_positive and not after_negative:
            return self.record(
                session_id=session_id,
                feedback_type=FeedbackType.OUTPUT_IMPROVED,
                decision_id=decision_id,
                data={"before_preview": before[:200], "after_preview": after[:200]}
            )
        elif after_negative and not before_negative:
            return self.record(
                session_id=session_id,
                feedback_type=FeedbackType.OUTPUT_WORSENED,
                decision_id=decision_id,
                data={"before_preview": before[:200], "after_preview": after[:200]}
            )

        return None

    def record_explicit(
        self,
        session_id: str,
        feedback_type: FeedbackType,
        decision_id: Optional[str] = None,
        reason: str = "",
        correction: str = ""
    ) -> Feedback:
        """Record explicit user feedback"""
        data = {}
        if reason:
            data["reason"] = reason
        if correction:
            data["correction"] = correction

        return self.record(
            session_id=session_id,
            feedback_type=feedback_type,
            decision_id=decision_id,
            data=data
        )

    # ==================== Feedback Analysis ====================

    def analyze_session(self, session_id: str) -> FeedbackSummary:
        """Analyze all feedback for a session"""
        summary = FeedbackSummary()

        with self._get_conn() as conn:
            feedbacks = conn.execute("""
                SELECT * FROM feedback
                WHERE session_id = ?
                ORDER BY created_at ASC
            """, (session_id,)).fetchall()

            for row in feedbacks:
                feedback = self._row_to_feedback(row)
                self._update_summary(summary, feedback)

            # Identify improvement and strength areas
            self._identify_areas(summary)

        return summary

    def analyze_pattern(self, pattern_id: str) -> FeedbackSummary:
        """Analyze all feedback for a pattern"""
        summary = FeedbackSummary()

        with self._get_conn() as conn:
            feedbacks = conn.execute("""
                SELECT * FROM feedback
                WHERE pattern_id = ?
                ORDER BY created_at ASC
            """, (pattern_id,)).fetchall()

            for row in feedbacks:
                feedback = self._row_to_feedback(row)
                self._update_summary(summary, feedback)

        return summary

    def _update_summary(self, summary: FeedbackSummary, feedback: Feedback):
        """Update summary with a feedback entry"""
        summary.total_feedback += 1

        # Count by sentiment
        if feedback.sentiment == FeedbackSentiment.POSITIVE:
            summary.positive_count += 1
        elif feedback.sentiment == FeedbackSentiment.NEGATIVE:
            summary.negative_count += 1
        else:
            summary.neutral_count += 1

        # Count by type
        type_key = feedback.feedback_type.value
        summary.by_type[type_key] = summary.by_type.get(type_key, 0) + 1

        # Update decision scores
        if feedback.decision_id:
            current = summary.decision_scores.get(feedback.decision_id, 0.5)
            delta = feedback.weight * (
                0.1 if feedback.sentiment == FeedbackSentiment.POSITIVE else
                -0.1 if feedback.sentiment == FeedbackSentiment.NEGATIVE else 0
            )
            summary.decision_scores[feedback.decision_id] = max(0, min(1, current + delta))

        # Update pattern scores
        if feedback.pattern_id:
            current = summary.pattern_scores.get(feedback.pattern_id, 0.5)
            delta = feedback.weight * (
                0.1 if feedback.sentiment == FeedbackSentiment.POSITIVE else
                -0.1 if feedback.sentiment == FeedbackSentiment.NEGATIVE else 0
            )
            summary.pattern_scores[feedback.pattern_id] = max(0, min(1, current + delta))

    def _identify_areas(self, summary: FeedbackSummary):
        """Identify improvement and strength areas from feedback patterns"""
        # Improvement areas (high negative feedback)
        for feedback_type, count in summary.by_type.items():
            if count >= 3:
                sentiment = self.TYPE_SENTIMENTS.get(
                    FeedbackType(feedback_type), FeedbackSentiment.NEUTRAL
                )
                if sentiment == FeedbackSentiment.NEGATIVE:
                    summary.improvement_areas.append(feedback_type)
                elif sentiment == FeedbackSentiment.POSITIVE:
                    summary.strength_areas.append(feedback_type)

    # ==================== Feedback Application ====================

    def apply_to_patterns(self, session_id: str) -> Dict[str, Any]:
        """Apply session feedback to update pattern confidence"""
        results = {
            "patterns_updated": 0,
            "updates": []
        }

        with self._get_conn() as conn:
            # Get unapplied feedback with pattern associations
            feedbacks = conn.execute("""
                SELECT * FROM feedback
                WHERE session_id = ? AND pattern_id IS NOT NULL AND applied = 0
            """, (session_id,)).fetchall()

            for row in feedbacks:
                feedback = self._row_to_feedback(row)

                # Calculate confidence delta
                if feedback.sentiment == FeedbackSentiment.POSITIVE:
                    success_delta = 1
                    failure_delta = 0
                elif feedback.sentiment == FeedbackSentiment.NEGATIVE:
                    success_delta = 0
                    failure_delta = 1
                else:
                    continue

                # Update pattern statistics
                conn.execute("""
                    UPDATE learned_patterns SET
                        success_count = success_count + ?,
                        failure_count = failure_count + ?,
                        confidence = (success_count + ? + 1.0) /
                                     (success_count + ? + failure_count + ? + 2.0),
                        last_used_at = ?
                    WHERE pattern_id = ?
                """, (
                    success_delta, failure_delta,
                    success_delta, success_delta, failure_delta,
                    int(time.time()),
                    feedback.pattern_id
                ))

                # Mark feedback as applied
                conn.execute("""
                    UPDATE feedback SET applied = 1 WHERE feedback_id = ?
                """, (feedback.feedback_id,))

                results["patterns_updated"] += 1
                results["updates"].append({
                    "pattern_id": feedback.pattern_id,
                    "feedback_type": feedback.feedback_type.value,
                    "sentiment": feedback.sentiment.value,
                })

        return results

    def get_pending_feedback(self, limit: int = 50) -> List[Feedback]:
        """Get unapplied feedback"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM feedback
                WHERE applied = 0
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()

            return [self._row_to_feedback(row) for row in rows]

    # ==================== Reporting ====================

    def generate_report(
        self,
        session_id: Optional[str] = None,
        days: int = 7
    ) -> Dict[str, Any]:
        """Generate feedback report"""
        cutoff_time = int(time.time()) - (days * 86400)

        with self._get_conn() as conn:
            where_clause = "WHERE created_at >= ?"
            params = [cutoff_time]

            if session_id:
                where_clause += " AND session_id = ?"
                params.append(session_id)

            # Overall stats
            total = conn.execute(f"""
                SELECT COUNT(*) as count FROM feedback {where_clause}
            """, params).fetchone()["count"]

            by_sentiment = conn.execute(f"""
                SELECT sentiment, COUNT(*) as count
                FROM feedback {where_clause}
                GROUP BY sentiment
            """, params).fetchall()

            by_type = conn.execute(f"""
                SELECT feedback_type, COUNT(*) as count
                FROM feedback {where_clause}
                GROUP BY feedback_type
                ORDER BY count DESC
            """, params).fetchall()

            # Daily breakdown
            daily = conn.execute(f"""
                SELECT date(created_at, 'unixepoch') as day,
                       sentiment, COUNT(*) as count
                FROM feedback {where_clause}
                GROUP BY day, sentiment
                ORDER BY day DESC
            """, params).fetchall()

            # Build daily breakdown
            daily_breakdown = defaultdict(lambda: {"positive": 0, "negative": 0, "neutral": 0})
            for row in daily:
                daily_breakdown[row["day"]][row["sentiment"]] = row["count"]

            # Top improved decisions
            improved = conn.execute(f"""
                SELECT decision_id, COUNT(*) as positive_count
                FROM feedback {where_clause}
                    AND sentiment = 'positive' AND decision_id IS NOT NULL
                GROUP BY decision_id
                ORDER BY positive_count DESC
                LIMIT 5
            """, params).fetchall()

            # Most problematic decisions
            problematic = conn.execute(f"""
                SELECT decision_id, COUNT(*) as negative_count
                FROM feedback {where_clause}
                    AND sentiment = 'negative' AND decision_id IS NOT NULL
                GROUP BY decision_id
                ORDER BY negative_count DESC
                LIMIT 5
            """, params).fetchall()

            return {
                "period_days": days,
                "session_id": session_id,
                "total_feedback": total,
                "by_sentiment": {row["sentiment"]: row["count"] for row in by_sentiment},
                "by_type": {row["feedback_type"]: row["count"] for row in by_type},
                "daily_breakdown": dict(daily_breakdown),
                "top_improved_decisions": [
                    {"decision_id": row["decision_id"], "positive_count": row["positive_count"]}
                    for row in improved
                ],
                "most_problematic_decisions": [
                    {"decision_id": row["decision_id"], "negative_count": row["negative_count"]}
                    for row in problematic
                ],
                "positive_ratio": (
                    sum(1 for row in by_sentiment if row["sentiment"] == "positive") / max(1, total)
                ) if total else 0,
            }

    def get_learning_suggestions(self, session_id: str) -> List[Dict[str, Any]]:
        """Get suggestions for improvement based on feedback"""
        suggestions = []
        summary = self.analyze_session(session_id)

        # Check for high command ignore rate
        ignored = summary.by_type.get("command_ignored", 0)
        executed = summary.by_type.get("command_executed", 0)
        if ignored > executed and ignored >= 3:
            suggestions.append({
                "type": "command_relevance",
                "severity": "high",
                "message": "Many commands were ignored. Consider improving command relevance.",
                "data": {"ignored": ignored, "executed": executed},
            })

        # Check for output worsening
        worsened = summary.by_type.get("output_worsened", 0)
        improved = summary.by_type.get("output_improved", 0)
        if worsened > improved and worsened >= 2:
            suggestions.append({
                "type": "intervention_quality",
                "severity": "high",
                "message": "Interventions are causing output to worsen. Review intervention strategy.",
                "data": {"worsened": worsened, "improved": improved},
            })

        # Check for user overrides
        overrides = summary.by_type.get("user_override", 0)
        if overrides >= 3:
            suggestions.append({
                "type": "user_alignment",
                "severity": "medium",
                "message": "Multiple user overrides detected. Consider adjusting to user preferences.",
                "data": {"overrides": overrides},
            })

        # Check for stuck patterns
        stuck = summary.by_type.get("stuck_continued", 0)
        if stuck >= 2:
            suggestions.append({
                "type": "stuck_resolution",
                "severity": "medium",
                "message": "Stuck situations not being resolved. Consider different approaches.",
                "data": {"stuck_count": stuck},
            })

        return suggestions


# ==================== CLI Interface ====================

def main():
    parser = argparse.ArgumentParser(
        description="Claude Monitor Feedback Collector"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # record command
    record_parser = subparsers.add_parser("record", help="Record feedback")
    record_parser.add_argument("session_id", help="Session ID")
    record_parser.add_argument("decision_id", help="Decision ID")
    record_parser.add_argument(
        "feedback_type",
        choices=[t.value for t in FeedbackType],
        help="Feedback type"
    )
    record_parser.add_argument("--data", help="JSON data", default="{}")

    # collect command (快捷命令：收集隐式反馈)
    collect_parser = subparsers.add_parser("collect", help="Collect implicit feedback")
    collect_parser.add_argument("session_id", help="Session ID")
    collect_parser.add_argument("signal_type", help="Signal type (e.g., command_sent, error_occurred)")
    collect_parser.add_argument("context", help="Context JSON")

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze session feedback")
    analyze_parser.add_argument("session_id", help="Session ID")

    # apply command
    apply_parser = subparsers.add_parser("apply", help="Apply feedback to patterns")
    apply_parser.add_argument("session_id", help="Session ID")

    # report command
    report_parser = subparsers.add_parser("report", help="Generate feedback report")
    report_parser.add_argument("--session", help="Session ID filter")
    report_parser.add_argument("--days", type=int, default=7, help="Days to include")

    # suggestions command
    suggestions_parser = subparsers.add_parser(
        "suggestions", help="Get learning suggestions"
    )
    suggestions_parser.add_argument("session_id", help="Session ID")

    args = parser.parse_args()
    collector = FeedbackCollector()

    if args.command == "record":
        data = json.loads(args.data)
        feedback = collector.record(
            session_id=args.session_id,
            feedback_type=FeedbackType(args.feedback_type),
            decision_id=args.decision_id,
            data=data
        )
        print(json.dumps(feedback.to_dict(), indent=2))

    elif args.command == "collect":
        # 根据信号类型推断反馈类型
        context = json.loads(args.context) if args.context else {}
        signal = args.signal_type.lower()

        feedback_type = FeedbackType.BEHAVIORAL
        if "error" in signal or "fail" in signal:
            feedback_type = FeedbackType.PERFORMANCE
        elif "success" in signal or "complete" in signal:
            feedback_type = FeedbackType.PERFORMANCE

        feedback = collector.record(
            session_id=args.session_id,
            feedback_type=feedback_type,
            decision_id=context.get("command", signal)[:50],
            data={
                "signal": signal,
                "context": context,
            }
        )
        # 静默成功，不输出

    elif args.command == "analyze":
        summary = collector.analyze_session(args.session_id)
        print(json.dumps(summary.to_dict(), indent=2))

    elif args.command == "apply":
        results = collector.apply_to_patterns(args.session_id)
        print(json.dumps(results, indent=2))

    elif args.command == "report":
        report = collector.generate_report(
            session_id=args.session,
            days=args.days
        )
        print(json.dumps(report, indent=2))

    elif args.command == "suggestions":
        suggestions = collector.get_learning_suggestions(args.session_id)
        print(json.dumps(suggestions, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
