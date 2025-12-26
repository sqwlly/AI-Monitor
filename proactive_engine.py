#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Proactive Engine
主动干预引擎 - 识别时机并主动进行干预

功能：
1. 干预时机识别（方向偏离/效率低下/风险升级/机会窗口）
2. 干预策略选择（温和提醒/替代方案/强制纠偏/暂停等待）
3. 干预执行（命令生成/时机把控/效果监控）
4. 干预反馈学习（有效性/时机/强度评估）

Usage:
    python3 proactive_engine.py analyze <session_id>
    python3 proactive_engine.py should-intervene <session_id> [--context <json>]
    python3 proactive_engine.py intervene <session_id> <intervention_type>
    python3 proactive_engine.py record-outcome <intervention_id> <outcome>
    python3 proactive_engine.py stats [--session <session_id>]
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


class InterventionType(Enum):
    """Type of intervention"""
    GENTLE_REMINDER = "gentle_reminder"      # Soft nudge
    SUGGESTION = "suggestion"                # Suggest alternative
    CORRECTION = "correction"                # Direct correction
    ESCALATION = "escalation"                # Escalate to user
    PAUSE = "pause"                          # Pause and wait
    NONE = "none"                            # No intervention


class InterventionTrigger(Enum):
    """What triggered the intervention"""
    DEVIATION = "deviation"              # Off-track from goal
    INEFFICIENCY = "inefficiency"        # Slow progress
    RISK = "risk"                        # Detected risk
    OPPORTUNITY = "opportunity"          # Optimization opportunity
    STUCK = "stuck"                      # Progress stalled
    ERROR_LOOP = "error_loop"            # Repeated errors
    USER_REQUEST = "user_request"        # User asked for help
    SCHEDULED = "scheduled"              # Regular check-in


class InterventionUrgency(Enum):
    """Urgency level"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Intervention:
    """Intervention record"""
    intervention_id: str = ""
    session_id: str = ""
    intervention_type: InterventionType = InterventionType.NONE
    trigger: InterventionTrigger = InterventionTrigger.DEVIATION
    urgency: InterventionUrgency = InterventionUrgency.MEDIUM
    message: str = ""
    action: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    outcome: Optional[str] = None          # success/ignored/rejected/counterproductive
    executed_at: Optional[int] = None
    outcome_recorded_at: Optional[int] = None
    created_at: int = 0

    def to_dict(self) -> Dict:
        return {
            "intervention_id": self.intervention_id,
            "session_id": self.session_id,
            "intervention_type": self.intervention_type.value,
            "trigger": self.trigger.value,
            "urgency": self.urgency.value,
            "message": self.message,
            "action": self.action,
            "context": self.context,
            "outcome": self.outcome,
            "executed_at": self.executed_at,
            "outcome_recorded_at": self.outcome_recorded_at,
            "created_at": self.created_at,
        }


@dataclass
class InterventionDecision:
    """Decision about whether to intervene"""
    should_intervene: bool
    intervention_type: InterventionType
    trigger: InterventionTrigger
    urgency: InterventionUrgency
    confidence: float
    reasoning: str
    suggested_message: str = ""
    suggested_action: str = ""

    def to_dict(self) -> Dict:
        return {
            "should_intervene": self.should_intervene,
            "intervention_type": self.intervention_type.value,
            "trigger": self.trigger.value,
            "urgency": self.urgency.value,
            "confidence": round(self.confidence, 3),
            "reasoning": self.reasoning,
            "suggested_message": self.suggested_message,
            "suggested_action": self.suggested_action,
        }


@dataclass
class SessionState:
    """Current session state for analysis"""
    session_id: str
    current_stage: str = "unknown"
    idle_duration: int = 0
    error_count: int = 0
    loop_count: int = 0
    goal_progress: float = 0.0
    last_activity: str = ""
    recent_outputs: List[str] = field(default_factory=list)
    goal_context: Optional[str] = None


class ProactiveEngine:
    """Proactive intervention engine"""

    # Intervention thresholds
    THRESHOLDS = {
        "idle_warning": 60,           # Seconds before first warning
        "idle_critical": 180,         # Seconds before escalation
        "error_loop_count": 3,        # Same error count for loop detection
        "deviation_threshold": 0.5,   # Goal deviation threshold
        "min_intervention_gap": 30,   # Minimum seconds between interventions
    }

    # Message templates
    MESSAGE_TEMPLATES = {
        InterventionType.GENTLE_REMINDER: [
            "Looks like there's been a pause. Need any assistance?",
            "Just checking in - everything going okay?",
            "Still working on {goal}? Let me know if you need help.",
        ],
        InterventionType.SUGGESTION: [
            "Consider trying: {suggestion}",
            "An alternative approach might be: {suggestion}",
            "Have you tried: {suggestion}?",
        ],
        InterventionType.CORRECTION: [
            "The current approach may not work. Try: {action}",
            "Detected issue: {issue}. Recommended fix: {action}",
            "Course correction needed: {action}",
        ],
        InterventionType.ESCALATION: [
            "This needs your attention: {issue}",
            "Unable to proceed automatically. Manual review needed for: {issue}",
            "Critical: {issue}. Please review.",
        ],
        InterventionType.PAUSE: [
            "Pausing for review. Multiple issues detected.",
            "Waiting for guidance before proceeding.",
            "Halted to prevent potential issues.",
        ],
    }

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    def _ensure_db(self):
        """Ensure database exists with proper schema"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS interventions (
                    intervention_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    intervention_type TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    urgency TEXT NOT NULL,
                    message TEXT,
                    action TEXT,
                    context TEXT,
                    outcome TEXT,
                    executed_at INTEGER,
                    outcome_recorded_at INTEGER,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS intervention_stats (
                    stat_id TEXT PRIMARY KEY,
                    intervention_type TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    total_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    ignored_count INTEGER DEFAULT 0,
                    rejected_count INTEGER DEFAULT 0,
                    counterproductive_count INTEGER DEFAULT 0,
                    avg_response_time REAL DEFAULT 0,
                    last_updated INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_interventions_session
                    ON interventions(session_id);
                CREATE INDEX IF NOT EXISTS idx_interventions_type
                    ON interventions(intervention_type);
                CREATE INDEX IF NOT EXISTS idx_interventions_outcome
                    ON interventions(outcome);
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

    def _row_to_intervention(self, row: sqlite3.Row) -> Intervention:
        """Convert database row to Intervention object"""
        return Intervention(
            intervention_id=row["intervention_id"],
            session_id=row["session_id"],
            intervention_type=InterventionType(row["intervention_type"]),
            trigger=InterventionTrigger(row["trigger"]),
            urgency=InterventionUrgency(row["urgency"]),
            message=row["message"] or "",
            action=row["action"] or "",
            context=json.loads(row["context"] or "{}"),
            outcome=row["outcome"],
            executed_at=row["executed_at"],
            outcome_recorded_at=row["outcome_recorded_at"],
            created_at=row["created_at"] or 0,
        )

    # ==================== Intervention Decision ====================

    def should_intervene(
        self,
        session_id: str,
        context: Optional[Dict[str, Any]] = None
    ) -> InterventionDecision:
        """Decide whether to intervene in current session"""
        context = context or {}

        # Build session state
        state = self._build_session_state(session_id, context)

        # Check for recent intervention (avoid over-intervening)
        if self._has_recent_intervention(session_id):
            return InterventionDecision(
                should_intervene=False,
                intervention_type=InterventionType.NONE,
                trigger=InterventionTrigger.SCHEDULED,
                urgency=InterventionUrgency.LOW,
                confidence=0.9,
                reasoning="Recent intervention exists, waiting before next action",
            )

        # Check various triggers
        triggers = [
            self._check_deviation(state),
            self._check_inefficiency(state),
            self._check_risk(state),
            self._check_stuck(state),
            self._check_error_loop(state),
            self._check_opportunity(state),
        ]

        # Find highest priority trigger
        triggers = [t for t in triggers if t.should_intervene]
        if not triggers:
            return InterventionDecision(
                should_intervene=False,
                intervention_type=InterventionType.NONE,
                trigger=InterventionTrigger.SCHEDULED,
                urgency=InterventionUrgency.LOW,
                confidence=0.8,
                reasoning="No intervention needed - session progressing normally",
            )

        # Sort by urgency and confidence
        urgency_order = {
            InterventionUrgency.CRITICAL: 4,
            InterventionUrgency.HIGH: 3,
            InterventionUrgency.MEDIUM: 2,
            InterventionUrgency.LOW: 1,
        }
        triggers.sort(
            key=lambda t: (urgency_order[t.urgency], t.confidence),
            reverse=True
        )

        return triggers[0]

    def _build_session_state(
        self,
        session_id: str,
        context: Dict[str, Any]
    ) -> SessionState:
        """Build current session state from context and database"""
        state = SessionState(session_id=session_id)

        # Apply context values
        state.current_stage = context.get("stage", "unknown")
        state.idle_duration = context.get("idle_duration", 0)
        state.last_activity = context.get("last_activity", "")
        state.goal_context = context.get("goal_context")

        # Get recent outputs from context
        if "recent_output" in context:
            state.recent_outputs = [context["recent_output"]]

        # Check for errors in recent output
        if state.recent_outputs:
            for output in state.recent_outputs:
                output_lower = output.lower()
                if any(kw in output_lower for kw in ["error", "exception", "failed"]):
                    state.error_count += 1

        # Get historical data
        with self._get_conn() as conn:
            # Count recent errors in decisions
            try:
                errors = conn.execute("""
                    SELECT COUNT(*) as count FROM decisions
                    WHERE session_id = ?
                      AND (input_preview LIKE '%error%'
                           OR input_preview LIKE '%Error%'
                           OR input_preview LIKE '%failed%')
                      AND timestamp > ?
                """, (session_id, int(time.time()) - 300)).fetchone()
                state.error_count = max(state.error_count, errors["count"] if errors else 0)
            except sqlite3.OperationalError:
                pass

            # Check for loops (repeated outputs)
            try:
                recent = conn.execute("""
                    SELECT input_hash, COUNT(*) as count
                    FROM decisions
                    WHERE session_id = ?
                      AND timestamp > ?
                    GROUP BY input_hash
                    HAVING count >= 2
                """, (session_id, int(time.time()) - 600)).fetchall()
                state.loop_count = len(recent)
            except sqlite3.OperationalError:
                pass

        return state

    def _has_recent_intervention(self, session_id: str) -> bool:
        """Check if there was a recent intervention"""
        min_gap = self.THRESHOLDS["min_intervention_gap"]
        cutoff = int(time.time()) - min_gap

        with self._get_conn() as conn:
            recent = conn.execute("""
                SELECT COUNT(*) as count FROM interventions
                WHERE session_id = ? AND created_at > ?
            """, (session_id, cutoff)).fetchone()

            return recent["count"] > 0 if recent else False

    def _check_deviation(self, state: SessionState) -> InterventionDecision:
        """Check for goal deviation"""
        # Without explicit goal tracking, use heuristics
        if not state.goal_context:
            return InterventionDecision(
                should_intervene=False,
                intervention_type=InterventionType.NONE,
                trigger=InterventionTrigger.DEVIATION,
                urgency=InterventionUrgency.LOW,
                confidence=0.3,
                reasoning="No goal context to check deviation",
            )

        # Check if recent activity relates to goal
        # This is a simplified check - real implementation would use NLP
        deviation_keywords = ["unrelated", "tangent", "wrong direction"]
        has_deviation = any(
            kw in output.lower()
            for output in state.recent_outputs
            for kw in deviation_keywords
        )

        if has_deviation:
            return InterventionDecision(
                should_intervene=True,
                intervention_type=InterventionType.CORRECTION,
                trigger=InterventionTrigger.DEVIATION,
                urgency=InterventionUrgency.MEDIUM,
                confidence=0.6,
                reasoning="Activity may be deviating from goal",
                suggested_message="Looks like we may be off track. Consider refocusing on the main goal.",
                suggested_action="review_goal",
            )

        return InterventionDecision(
            should_intervene=False,
            intervention_type=InterventionType.NONE,
            trigger=InterventionTrigger.DEVIATION,
            urgency=InterventionUrgency.LOW,
            confidence=0.7,
            reasoning="Activity appears aligned with goal",
        )

    def _check_inefficiency(self, state: SessionState) -> InterventionDecision:
        """Check for inefficient progress"""
        idle_warning = self.THRESHOLDS["idle_warning"]
        idle_critical = self.THRESHOLDS["idle_critical"]

        if state.idle_duration >= idle_critical:
            return InterventionDecision(
                should_intervene=True,
                intervention_type=InterventionType.ESCALATION,
                trigger=InterventionTrigger.INEFFICIENCY,
                urgency=InterventionUrgency.HIGH,
                confidence=0.85,
                reasoning=f"Idle for {state.idle_duration}s exceeds critical threshold",
                suggested_message="Extended inactivity detected. Do you need assistance?",
                suggested_action="prompt_user",
            )
        elif state.idle_duration >= idle_warning:
            return InterventionDecision(
                should_intervene=True,
                intervention_type=InterventionType.GENTLE_REMINDER,
                trigger=InterventionTrigger.INEFFICIENCY,
                urgency=InterventionUrgency.LOW,
                confidence=0.65,
                reasoning=f"Idle for {state.idle_duration}s exceeds warning threshold",
                suggested_message="Just checking in - everything okay?",
            )

        return InterventionDecision(
            should_intervene=False,
            intervention_type=InterventionType.NONE,
            trigger=InterventionTrigger.INEFFICIENCY,
            urgency=InterventionUrgency.LOW,
            confidence=0.8,
            reasoning="Activity level is acceptable",
        )

    def _check_risk(self, state: SessionState) -> InterventionDecision:
        """Check for risk indicators"""
        risk_keywords = [
            "delete", "remove", "drop", "rm -rf", "force push",
            "production", "main branch", "master branch",
        ]

        high_risk = False
        risk_details = []

        for output in state.recent_outputs:
            output_lower = output.lower()
            for kw in risk_keywords:
                if kw in output_lower:
                    high_risk = True
                    risk_details.append(kw)

        if high_risk:
            return InterventionDecision(
                should_intervene=True,
                intervention_type=InterventionType.PAUSE,
                trigger=InterventionTrigger.RISK,
                urgency=InterventionUrgency.CRITICAL,
                confidence=0.9,
                reasoning=f"Detected risky operations: {', '.join(risk_details)}",
                suggested_message=f"⚠️ Potentially risky operation detected: {risk_details[0]}. Please confirm.",
                suggested_action="require_confirmation",
            )

        return InterventionDecision(
            should_intervene=False,
            intervention_type=InterventionType.NONE,
            trigger=InterventionTrigger.RISK,
            urgency=InterventionUrgency.LOW,
            confidence=0.8,
            reasoning="No immediate risks detected",
        )

    def _check_stuck(self, state: SessionState) -> InterventionDecision:
        """Check if session is stuck"""
        if state.current_stage == "stuck" or state.loop_count >= 2:
            severity = InterventionUrgency.HIGH if state.loop_count >= 3 else InterventionUrgency.MEDIUM
            return InterventionDecision(
                should_intervene=True,
                intervention_type=InterventionType.SUGGESTION,
                trigger=InterventionTrigger.STUCK,
                urgency=severity,
                confidence=0.75,
                reasoning=f"Session appears stuck (stage: {state.current_stage}, loops: {state.loop_count})",
                suggested_message="Looks like we're stuck. Try a different approach?",
                suggested_action="suggest_alternative",
            )

        return InterventionDecision(
            should_intervene=False,
            intervention_type=InterventionType.NONE,
            trigger=InterventionTrigger.STUCK,
            urgency=InterventionUrgency.LOW,
            confidence=0.7,
            reasoning="Session progressing normally",
        )

    def _check_error_loop(self, state: SessionState) -> InterventionDecision:
        """Check for repeated errors"""
        error_threshold = self.THRESHOLDS["error_loop_count"]

        if state.error_count >= error_threshold:
            return InterventionDecision(
                should_intervene=True,
                intervention_type=InterventionType.CORRECTION,
                trigger=InterventionTrigger.ERROR_LOOP,
                urgency=InterventionUrgency.HIGH,
                confidence=0.85,
                reasoning=f"Detected {state.error_count} errors, exceeds threshold of {error_threshold}",
                suggested_message="Multiple errors detected. Let's try a different approach.",
                suggested_action="analyze_error_pattern",
            )

        return InterventionDecision(
            should_intervene=False,
            intervention_type=InterventionType.NONE,
            trigger=InterventionTrigger.ERROR_LOOP,
            urgency=InterventionUrgency.LOW,
            confidence=0.8,
            reasoning="Error count within acceptable range",
        )

    def _check_opportunity(self, state: SessionState) -> InterventionDecision:
        """Check for optimization opportunities"""
        opportunity_keywords = [
            "could be faster", "optimization", "refactor",
            "better way", "consider using",
        ]

        has_opportunity = any(
            kw in output.lower()
            for output in state.recent_outputs
            for kw in opportunity_keywords
        )

        if has_opportunity:
            return InterventionDecision(
                should_intervene=True,
                intervention_type=InterventionType.SUGGESTION,
                trigger=InterventionTrigger.OPPORTUNITY,
                urgency=InterventionUrgency.LOW,
                confidence=0.5,
                reasoning="Potential optimization opportunity detected",
                suggested_message="There might be a better way to do this. Want to explore options?",
            )

        return InterventionDecision(
            should_intervene=False,
            intervention_type=InterventionType.NONE,
            trigger=InterventionTrigger.OPPORTUNITY,
            urgency=InterventionUrgency.LOW,
            confidence=0.6,
            reasoning="No obvious optimization opportunities",
        )

    # ==================== Intervention Execution ====================

    def create_intervention(
        self,
        session_id: str,
        intervention_type: InterventionType,
        trigger: InterventionTrigger,
        urgency: InterventionUrgency,
        message: str,
        action: str = "",
        context: Optional[Dict[str, Any]] = None
    ) -> Intervention:
        """Create and record an intervention"""
        intervention = Intervention(
            intervention_id=str(uuid.uuid4())[:8],
            session_id=session_id,
            intervention_type=intervention_type,
            trigger=trigger,
            urgency=urgency,
            message=message,
            action=action,
            context=context or {},
            created_at=int(time.time()),
        )

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO interventions (
                    intervention_id, session_id, intervention_type,
                    trigger, urgency, message, action, context, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                intervention.intervention_id,
                intervention.session_id,
                intervention.intervention_type.value,
                intervention.trigger.value,
                intervention.urgency.value,
                intervention.message,
                intervention.action,
                json.dumps(intervention.context),
                intervention.created_at,
            ))

        return intervention

    def execute_intervention(
        self,
        intervention_id: str
    ) -> Dict[str, Any]:
        """Mark intervention as executed"""
        now = int(time.time())

        with self._get_conn() as conn:
            conn.execute("""
                UPDATE interventions SET executed_at = ?
                WHERE intervention_id = ?
            """, (now, intervention_id))

            row = conn.execute("""
                SELECT * FROM interventions WHERE intervention_id = ?
            """, (intervention_id,)).fetchone()

            if row:
                intervention = self._row_to_intervention(row)
                return {
                    "intervention_id": intervention_id,
                    "type": intervention.intervention_type.value,
                    "message": intervention.message,
                    "action": intervention.action,
                    "executed_at": now,
                }

        return {"error": "Intervention not found"}

    def generate_intervention_message(
        self,
        intervention_type: InterventionType,
        context: Dict[str, Any]
    ) -> str:
        """Generate intervention message from template"""
        templates = self.MESSAGE_TEMPLATES.get(intervention_type, [])
        if not templates:
            return "Intervention needed."

        # Select template (could be random or context-based)
        template = templates[0]

        # Fill in template variables
        message = template
        for key, value in context.items():
            placeholder = "{" + key + "}"
            if placeholder in message:
                message = message.replace(placeholder, str(value))

        # Clean up unfilled placeholders
        message = re.sub(r'\{[^}]+\}', '', message).strip()

        return message

    # ==================== Outcome Recording ====================

    def record_outcome(
        self,
        intervention_id: str,
        outcome: str  # success/ignored/rejected/counterproductive
    ):
        """Record the outcome of an intervention"""
        now = int(time.time())

        with self._get_conn() as conn:
            # Update intervention record
            conn.execute("""
                UPDATE interventions SET
                    outcome = ?,
                    outcome_recorded_at = ?
                WHERE intervention_id = ?
            """, (outcome, now, intervention_id))

            # Update statistics
            intervention = conn.execute("""
                SELECT * FROM interventions WHERE intervention_id = ?
            """, (intervention_id,)).fetchone()

            if intervention:
                self._update_stats(
                    conn,
                    intervention["intervention_type"],
                    intervention["trigger"],
                    outcome
                )

    def _update_stats(
        self,
        conn: sqlite3.Connection,
        intervention_type: str,
        trigger: str,
        outcome: str
    ):
        """Update intervention statistics"""
        stat_id = f"{intervention_type}_{trigger}"
        now = int(time.time())

        # Check if stat exists
        existing = conn.execute("""
            SELECT * FROM intervention_stats WHERE stat_id = ?
        """, (stat_id,)).fetchone()

        outcome_field = f"{outcome}_count"

        if existing:
            # Update existing stat
            conn.execute(f"""
                UPDATE intervention_stats SET
                    total_count = total_count + 1,
                    {outcome_field} = {outcome_field} + 1,
                    last_updated = ?
                WHERE stat_id = ?
            """, (now, stat_id))
        else:
            # Create new stat
            counts = {
                "success_count": 1 if outcome == "success" else 0,
                "ignored_count": 1 if outcome == "ignored" else 0,
                "rejected_count": 1 if outcome == "rejected" else 0,
                "counterproductive_count": 1 if outcome == "counterproductive" else 0,
            }
            conn.execute("""
                INSERT INTO intervention_stats (
                    stat_id, intervention_type, trigger, total_count,
                    success_count, ignored_count, rejected_count,
                    counterproductive_count, last_updated
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
            """, (
                stat_id, intervention_type, trigger,
                counts["success_count"], counts["ignored_count"],
                counts["rejected_count"], counts["counterproductive_count"],
                now
            ))

    # ==================== Analysis & Reporting ====================

    def analyze_session(self, session_id: str) -> Dict[str, Any]:
        """Analyze intervention history for a session"""
        with self._get_conn() as conn:
            interventions = conn.execute("""
                SELECT * FROM interventions
                WHERE session_id = ?
                ORDER BY created_at DESC
            """, (session_id,)).fetchall()

            total = len(interventions)
            by_type = defaultdict(int)
            by_outcome = defaultdict(int)
            by_trigger = defaultdict(int)

            for row in interventions:
                by_type[row["intervention_type"]] += 1
                by_trigger[row["trigger"]] += 1
                if row["outcome"]:
                    by_outcome[row["outcome"]] += 1

            success_rate = by_outcome.get("success", 0) / max(1, sum(by_outcome.values()))

            return {
                "session_id": session_id,
                "total_interventions": total,
                "by_type": dict(by_type),
                "by_trigger": dict(by_trigger),
                "by_outcome": dict(by_outcome),
                "success_rate": round(success_rate, 3),
                "recent": [
                    self._row_to_intervention(row).to_dict()
                    for row in interventions[:5]
                ],
            }

    def get_stats(
        self,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get intervention statistics"""
        with self._get_conn() as conn:
            where_clause = ""
            params = []

            if session_id:
                where_clause = "WHERE session_id = ?"
                params.append(session_id)

            # Overall counts
            total = conn.execute(f"""
                SELECT COUNT(*) as count FROM interventions {where_clause}
            """, params).fetchone()["count"]

            by_outcome = conn.execute(f"""
                SELECT outcome, COUNT(*) as count
                FROM interventions {where_clause}
                    {"AND" if where_clause else "WHERE"} outcome IS NOT NULL
                GROUP BY outcome
            """, params).fetchall()

            by_type = conn.execute(f"""
                SELECT intervention_type, COUNT(*) as count
                FROM interventions {where_clause}
                GROUP BY intervention_type
            """, params).fetchall()

            # Effectiveness by type
            effectiveness = conn.execute("""
                SELECT intervention_type, trigger,
                       total_count, success_count,
                       ROUND(1.0 * success_count / total_count, 3) as success_rate
                FROM intervention_stats
                WHERE total_count >= 5
                ORDER BY success_rate DESC
            """).fetchall()

            return {
                "total_interventions": total,
                "by_outcome": {row["outcome"]: row["count"] for row in by_outcome},
                "by_type": {row["intervention_type"]: row["count"] for row in by_type},
                "effectiveness": [
                    {
                        "type": row["intervention_type"],
                        "trigger": row["trigger"],
                        "total": row["total_count"],
                        "successes": row["success_count"],
                        "success_rate": row["success_rate"],
                    }
                    for row in effectiveness
                ],
            }

    def get_recommendations(self) -> List[Dict[str, Any]]:
        """Get recommendations for improving interventions"""
        recommendations = []

        with self._get_conn() as conn:
            stats = conn.execute("""
                SELECT * FROM intervention_stats
                WHERE total_count >= 10
            """).fetchall()

            for row in stats:
                success_rate = row["success_count"] / row["total_count"]
                rejected_rate = row["rejected_count"] / row["total_count"]

                if success_rate < 0.3:
                    recommendations.append({
                        "type": row["intervention_type"],
                        "trigger": row["trigger"],
                        "issue": "Low success rate",
                        "suggestion": "Consider adjusting timing or message content",
                        "success_rate": round(success_rate, 3),
                    })

                if rejected_rate > 0.3:
                    recommendations.append({
                        "type": row["intervention_type"],
                        "trigger": row["trigger"],
                        "issue": "High rejection rate",
                        "suggestion": "Intervention may be too aggressive or poorly timed",
                        "rejection_rate": round(rejected_rate, 3),
                    })

        return recommendations


# ==================== CLI Interface ====================

def main():
    parser = argparse.ArgumentParser(
        description="Claude Monitor Proactive Engine"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze session")
    analyze_parser.add_argument("session_id", help="Session ID")

    # should-intervene command
    intervene_parser = subparsers.add_parser(
        "should-intervene", help="Check if intervention needed"
    )
    intervene_parser.add_argument("session_id", help="Session ID")
    intervene_parser.add_argument("--context", help="Context JSON")

    # intervene command
    execute_parser = subparsers.add_parser("intervene", help="Create intervention")
    execute_parser.add_argument("session_id", help="Session ID")
    execute_parser.add_argument(
        "intervention_type",
        choices=[t.value for t in InterventionType],
        help="Intervention type"
    )
    execute_parser.add_argument("--message", help="Intervention message")

    # record-outcome command
    outcome_parser = subparsers.add_parser(
        "record-outcome", help="Record intervention outcome"
    )
    outcome_parser.add_argument("intervention_id", help="Intervention ID")
    outcome_parser.add_argument(
        "outcome",
        choices=["success", "ignored", "rejected", "counterproductive"],
        help="Outcome"
    )

    # stats command
    stats_parser = subparsers.add_parser("stats", help="Show statistics")
    stats_parser.add_argument("--session", help="Session ID filter")

    # recommendations command
    subparsers.add_parser("recommendations", help="Get improvement recommendations")

    # check command (快捷命令：检查是否需要干预并输出简洁建议)
    check_parser = subparsers.add_parser("check", help="Quick check for intervention")
    check_parser.add_argument("session_id", help="Session ID")
    check_parser.add_argument("output_text", help="Current output text")
    check_parser.add_argument("--stage", default="unknown", help="Current stage")

    args = parser.parse_args()
    engine = ProactiveEngine()

    if args.command == "analyze":
        result = engine.analyze_session(args.session_id)
        print(json.dumps(result, indent=2))

    elif args.command == "should-intervene":
        context = json.loads(args.context) if args.context else {}
        decision = engine.should_intervene(args.session_id, context)
        print(json.dumps(decision.to_dict(), indent=2))

    elif args.command == "intervene":
        intervention = engine.create_intervention(
            session_id=args.session_id,
            intervention_type=InterventionType(args.intervention_type),
            trigger=InterventionTrigger.USER_REQUEST,
            urgency=InterventionUrgency.MEDIUM,
            message=args.message or "Intervention requested",
        )
        print(json.dumps(intervention.to_dict(), indent=2))

    elif args.command == "record-outcome":
        engine.record_outcome(args.intervention_id, args.outcome)
        print(f"Recorded outcome: {args.outcome}")

    elif args.command == "stats":
        stats = engine.get_stats(args.session)
        print(json.dumps(stats, indent=2))

    elif args.command == "recommendations":
        recommendations = engine.get_recommendations()
        print(json.dumps(recommendations, indent=2))

    elif args.command == "check":
        # 快速检查是否需要干预
        context = {
            "output": args.output_text,
            "stage": args.stage,
        }

        # 简单启发式检测
        output_lower = args.output_text.lower()
        suggestions = []

        # 检测卡住/循环
        if "error" in output_lower or "failed" in output_lower or "exception" in output_lower:
            suggestions.append("[proactive] 检测到错误，建议诊断问题根因")

        if "timeout" in output_lower or "timed out" in output_lower:
            suggestions.append("[proactive] 检测到超时，建议检查网络或增加超时时间")

        if "permission denied" in output_lower or "access denied" in output_lower:
            suggestions.append("[proactive] 检测到权限问题，建议检查文件/目录权限")

        if "not found" in output_lower and ("command" in output_lower or "file" in output_lower):
            suggestions.append("[proactive] 检测到资源缺失，建议检查路径或安装依赖")

        # 输出建议（每行一条，供 shell 注入）
        if suggestions:
            print("\n".join(suggestions[:2]))  # 最多2条建议

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
