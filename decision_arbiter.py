#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Decision Arbiter
决策仲裁器 - 整合多源建议并做出最终决策

功能：
1. 多源建议整合（模式匹配/LLM/规划/人类指示）
2. 冲突解决（优先级排序/置信度加权/安全性优先）
3. 最终决策（决策确定/解释生成/执行）
4. 决策审计（日志/追溯/改进建议）

Usage:
    python3 decision_arbiter.py arbitrate <session_id> [--suggestions <json>]
    python3 decision_arbiter.py explain <decision_id>
    python3 decision_arbiter.py audit <session_id> [--limit 20]
    python3 decision_arbiter.py override <decision_id> <new_action>
"""

import argparse
import json
import os
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


class SuggestionSource(Enum):
    """Source of a suggestion"""
    PATTERN = "pattern"              # Pattern learner
    LLM = "llm"                      # LLM model
    PLANNER = "planner"              # Plan generator
    HUMAN = "human"                  # Human user
    PROACTIVE = "proactive"          # Proactive engine
    STRATEGY = "strategy"            # Strategy optimizer
    HEURISTIC = "heuristic"          # Rule-based heuristics


class ActionType(Enum):
    """Type of action to take"""
    WAIT = "wait"
    NUDGE = "nudge"
    COMMAND = "command"
    ASK = "ask"
    NOTIFY = "notify"
    ESCALATE = "escalate"
    ABORT = "abort"


class ConflictType(Enum):
    """Type of conflict between suggestions"""
    NONE = "none"
    ACTION_MISMATCH = "action_mismatch"
    TIMING_CONFLICT = "timing_conflict"
    SAFETY_OVERRIDE = "safety_override"
    CONFIDENCE_GAP = "confidence_gap"


@dataclass
class Suggestion:
    """A suggestion from a source"""
    source: SuggestionSource
    action_type: ActionType
    action_content: str = ""
    confidence: float = 0.5
    priority: int = 0                  # Higher = more important
    safety_score: float = 1.0          # 1.0 = safe, 0.0 = dangerous
    reasoning: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "source": self.source.value,
            "action_type": self.action_type.value,
            "action_content": self.action_content,
            "confidence": round(self.confidence, 3),
            "priority": self.priority,
            "safety_score": round(self.safety_score, 3),
            "reasoning": self.reasoning,
            "metadata": self.metadata,
        }


@dataclass
class Decision:
    """Final arbitrated decision"""
    decision_id: str = ""
    session_id: str = ""
    action_type: ActionType = ActionType.WAIT
    action_content: str = ""
    confidence: float = 0.5
    explanation: str = ""
    contributing_sources: List[str] = field(default_factory=list)
    conflicts_resolved: List[str] = field(default_factory=list)
    safety_verified: bool = True
    overridden: bool = False
    override_reason: str = ""
    created_at: int = 0
    executed_at: Optional[int] = None
    outcome: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "decision_id": self.decision_id,
            "session_id": self.session_id,
            "action_type": self.action_type.value,
            "action_content": self.action_content,
            "confidence": round(self.confidence, 3),
            "explanation": self.explanation,
            "contributing_sources": self.contributing_sources,
            "conflicts_resolved": self.conflicts_resolved,
            "safety_verified": self.safety_verified,
            "overridden": self.overridden,
            "override_reason": self.override_reason,
            "created_at": self.created_at,
            "executed_at": self.executed_at,
            "outcome": self.outcome,
        }


@dataclass
class ArbitrationResult:
    """Result of arbitration process"""
    decision: Decision
    suggestions_received: int
    conflicts_detected: List[Dict[str, Any]]
    selection_reasoning: str

    def to_dict(self) -> Dict:
        return {
            "decision": self.decision.to_dict(),
            "suggestions_received": self.suggestions_received,
            "conflicts_detected": self.conflicts_detected,
            "selection_reasoning": self.selection_reasoning,
        }


class DecisionArbiter:
    """Decision arbitration engine"""

    # Source priority weights (higher = more trusted)
    SOURCE_PRIORITIES = {
        SuggestionSource.HUMAN: 100,       # Human always highest
        SuggestionSource.LLM: 80,          # LLM suggestions trusted
        SuggestionSource.PROACTIVE: 70,    # Proactive engine
        SuggestionSource.STRATEGY: 60,     # Strategy optimizer
        SuggestionSource.PLANNER: 50,      # Plan generator
        SuggestionSource.PATTERN: 40,      # Pattern learner
        SuggestionSource.HEURISTIC: 30,    # Rule-based
    }

    # Safety-critical action types that require extra verification
    SAFETY_CRITICAL_ACTIONS = {
        ActionType.COMMAND,
        ActionType.ABORT,
        ActionType.ESCALATE,
    }

    # Dangerous patterns to flag
    DANGEROUS_PATTERNS = [
        "rm -rf", "delete", "drop", "force push",
        "sudo", "production", "master", "main branch",
    ]

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    def _ensure_db(self):
        """Ensure database exists with proper schema"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS arbiter_decisions (
                    decision_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_content TEXT,
                    confidence REAL DEFAULT 0.5,
                    explanation TEXT,
                    contributing_sources TEXT,
                    conflicts_resolved TEXT,
                    safety_verified INTEGER DEFAULT 1,
                    overridden INTEGER DEFAULT 0,
                    override_reason TEXT,
                    created_at INTEGER NOT NULL,
                    executed_at INTEGER,
                    outcome TEXT
                );

                CREATE TABLE IF NOT EXISTS decision_audit (
                    audit_id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_data TEXT,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (decision_id) REFERENCES arbiter_decisions(decision_id)
                );

                CREATE INDEX IF NOT EXISTS idx_decisions_session
                    ON arbiter_decisions(session_id);
                CREATE INDEX IF NOT EXISTS idx_decisions_action
                    ON arbiter_decisions(action_type);
                CREATE INDEX IF NOT EXISTS idx_audit_decision
                    ON decision_audit(decision_id);
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

    def _row_to_decision(self, row: sqlite3.Row) -> Decision:
        """Convert database row to Decision object"""
        return Decision(
            decision_id=row["decision_id"],
            session_id=row["session_id"],
            action_type=ActionType(row["action_type"]),
            action_content=row["action_content"] or "",
            confidence=row["confidence"] or 0.5,
            explanation=row["explanation"] or "",
            contributing_sources=json.loads(row["contributing_sources"] or "[]"),
            conflicts_resolved=json.loads(row["conflicts_resolved"] or "[]"),
            safety_verified=bool(row["safety_verified"]),
            overridden=bool(row["overridden"]),
            override_reason=row["override_reason"] or "",
            created_at=row["created_at"] or 0,
            executed_at=row["executed_at"],
            outcome=row["outcome"],
        )

    # ==================== Arbitration ====================

    def arbitrate(
        self,
        session_id: str,
        suggestions: List[Suggestion]
    ) -> ArbitrationResult:
        """Arbitrate among multiple suggestions to produce final decision"""
        if not suggestions:
            # Default to wait if no suggestions
            decision = self._create_default_decision(session_id)
            return ArbitrationResult(
                decision=decision,
                suggestions_received=0,
                conflicts_detected=[],
                selection_reasoning="No suggestions provided, defaulting to wait",
            )

        # Step 1: Detect conflicts
        conflicts = self._detect_conflicts(suggestions)

        # Step 2: Resolve conflicts and rank suggestions
        ranked = self._resolve_and_rank(suggestions, conflicts)

        # Step 3: Verify safety
        safe_ranked = self._verify_safety(ranked)

        if not safe_ranked:
            # All suggestions failed safety check
            decision = self._create_safe_fallback(session_id, ranked)
            return ArbitrationResult(
                decision=decision,
                suggestions_received=len(suggestions),
                conflicts_detected=[c.to_dict() if hasattr(c, 'to_dict') else c for c in conflicts],
                selection_reasoning="All suggestions failed safety verification, using safe fallback",
            )

        # Step 4: Select best suggestion
        best = safe_ranked[0]
        contributing = [s.source.value for s in safe_ranked[:3]]

        # Step 5: Generate explanation
        explanation = self._generate_explanation(best, suggestions, conflicts)

        # Step 6: Create and save decision
        decision = Decision(
            decision_id=str(uuid.uuid4())[:8],
            session_id=session_id,
            action_type=best.action_type,
            action_content=best.action_content,
            confidence=best.confidence,
            explanation=explanation,
            contributing_sources=contributing,
            conflicts_resolved=[c["type"] for c in conflicts],
            safety_verified=True,
            created_at=int(time.time()),
        )

        self._save_decision(decision)
        self._log_audit(decision.decision_id, "created", {
            "suggestions_count": len(suggestions),
            "conflicts_count": len(conflicts),
        })

        return ArbitrationResult(
            decision=decision,
            suggestions_received=len(suggestions),
            conflicts_detected=conflicts,
            selection_reasoning=self._generate_selection_reasoning(best, safe_ranked),
        )

    def _detect_conflicts(
        self,
        suggestions: List[Suggestion]
    ) -> List[Dict[str, Any]]:
        """Detect conflicts between suggestions"""
        conflicts = []

        # Group by action type
        by_action = defaultdict(list)
        for s in suggestions:
            by_action[s.action_type].append(s)

        # Check for action mismatches
        if len(by_action) > 1:
            action_types = list(by_action.keys())
            # Check for contradictory actions
            if ActionType.WAIT in action_types and ActionType.COMMAND in action_types:
                conflicts.append({
                    "type": ConflictType.ACTION_MISMATCH.value,
                    "details": "Conflict between WAIT and COMMAND suggestions",
                    "sources": [
                        s.source.value for s in suggestions
                        if s.action_type in [ActionType.WAIT, ActionType.COMMAND]
                    ],
                })

        # Check for significant confidence gaps
        if len(suggestions) >= 2:
            sorted_by_conf = sorted(suggestions, key=lambda s: s.confidence, reverse=True)
            if sorted_by_conf[0].confidence - sorted_by_conf[1].confidence > 0.4:
                conflicts.append({
                    "type": ConflictType.CONFIDENCE_GAP.value,
                    "details": f"Large confidence gap: {sorted_by_conf[0].confidence:.2f} vs {sorted_by_conf[1].confidence:.2f}",
                    "sources": [sorted_by_conf[0].source.value, sorted_by_conf[1].source.value],
                })

        # Check for safety concerns
        for s in suggestions:
            if s.safety_score < 0.5:
                conflicts.append({
                    "type": ConflictType.SAFETY_OVERRIDE.value,
                    "details": f"Safety concern from {s.source.value}: score {s.safety_score:.2f}",
                    "sources": [s.source.value],
                })

        return conflicts

    def _resolve_and_rank(
        self,
        suggestions: List[Suggestion],
        conflicts: List[Dict[str, Any]]
    ) -> List[Suggestion]:
        """Resolve conflicts and rank suggestions"""
        scored = []

        for s in suggestions:
            # Base score from source priority
            source_weight = self.SOURCE_PRIORITIES.get(s.source, 50) / 100

            # Combine with confidence
            combined_confidence = (
                s.confidence * 0.5 +
                source_weight * 0.3 +
                s.safety_score * 0.2
            )

            # Apply priority boost
            priority_boost = s.priority * 0.05

            final_score = min(1.0, combined_confidence + priority_boost)

            scored.append((s, final_score))

        # Sort by final score
        scored.sort(key=lambda x: x[1], reverse=True)

        # Update confidence to reflect final score
        result = []
        for s, score in scored:
            s.confidence = score
            result.append(s)

        return result

    def _verify_safety(
        self,
        suggestions: List[Suggestion]
    ) -> List[Suggestion]:
        """Verify safety of suggestions and filter dangerous ones"""
        safe = []

        for s in suggestions:
            if self._is_safe(s):
                safe.append(s)

        return safe

    def _is_safe(self, suggestion: Suggestion) -> bool:
        """Check if a suggestion is safe to execute"""
        # Check explicit safety score
        if suggestion.safety_score < 0.3:
            return False

        # Check for dangerous patterns in action content
        content_lower = suggestion.action_content.lower()
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in content_lower:
                # Dangerous pattern found - require explicit safety flag
                if suggestion.safety_score < 0.8:
                    return False

        # Safety-critical actions need higher confidence
        if suggestion.action_type in self.SAFETY_CRITICAL_ACTIONS:
            if suggestion.confidence < 0.6:
                return False

        return True

    def _generate_explanation(
        self,
        selected: Suggestion,
        all_suggestions: List[Suggestion],
        conflicts: List[Dict[str, Any]]
    ) -> str:
        """Generate human-readable explanation for decision"""
        parts = []

        # Selected action
        parts.append(f"Selected {selected.action_type.value} from {selected.source.value}")
        parts.append(f"(confidence: {selected.confidence:.0%})")

        # Contributing factors
        if selected.reasoning:
            parts.append(f"Reason: {selected.reasoning}")

        # Conflict resolution
        if conflicts:
            conflict_types = [c["type"] for c in conflicts]
            parts.append(f"Resolved conflicts: {', '.join(conflict_types)}")

        # Alternative sources considered
        other_sources = [s.source.value for s in all_suggestions if s != selected][:2]
        if other_sources:
            parts.append(f"Also considered: {', '.join(other_sources)}")

        return ". ".join(parts)

    def _generate_selection_reasoning(
        self,
        selected: Suggestion,
        ranked: List[Suggestion]
    ) -> str:
        """Generate reasoning for why this suggestion was selected"""
        reasons = []

        # Source authority
        source_priority = self.SOURCE_PRIORITIES.get(selected.source, 50)
        if source_priority >= 80:
            reasons.append(f"High-authority source ({selected.source.value})")

        # Confidence level
        if selected.confidence >= 0.8:
            reasons.append("High confidence score")
        elif selected.confidence >= 0.6:
            reasons.append("Moderate confidence score")

        # Safety
        if selected.safety_score >= 0.9:
            reasons.append("Verified safe")

        # Comparison with alternatives
        if len(ranked) > 1:
            margin = selected.confidence - ranked[1].confidence
            if margin > 0.2:
                reasons.append(f"Clear winner (margin: {margin:.0%})")

        return "; ".join(reasons) if reasons else "Default selection"

    def _create_default_decision(self, session_id: str) -> Decision:
        """Create default wait decision"""
        decision = Decision(
            decision_id=str(uuid.uuid4())[:8],
            session_id=session_id,
            action_type=ActionType.WAIT,
            action_content="",
            confidence=0.5,
            explanation="No suggestions provided, defaulting to wait",
            safety_verified=True,
            created_at=int(time.time()),
        )
        self._save_decision(decision)
        return decision

    def _create_safe_fallback(
        self,
        session_id: str,
        rejected: List[Suggestion]
    ) -> Decision:
        """Create safe fallback decision when all suggestions fail safety"""
        decision = Decision(
            decision_id=str(uuid.uuid4())[:8],
            session_id=session_id,
            action_type=ActionType.ESCALATE,
            action_content="Safety verification failed - manual review required",
            confidence=0.9,
            explanation=f"Rejected {len(rejected)} unsafe suggestions, escalating for review",
            safety_verified=True,
            created_at=int(time.time()),
        )
        self._save_decision(decision)
        return decision

    def _save_decision(self, decision: Decision):
        """Save decision to database"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO arbiter_decisions (
                    decision_id, session_id, action_type, action_content,
                    confidence, explanation, contributing_sources,
                    conflicts_resolved, safety_verified, overridden,
                    override_reason, created_at, executed_at, outcome
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision.decision_id,
                decision.session_id,
                decision.action_type.value,
                decision.action_content,
                decision.confidence,
                decision.explanation,
                json.dumps(decision.contributing_sources),
                json.dumps(decision.conflicts_resolved),
                1 if decision.safety_verified else 0,
                1 if decision.overridden else 0,
                decision.override_reason,
                decision.created_at,
                decision.executed_at,
                decision.outcome,
            ))

    # ==================== Quick Arbitration ====================

    def quick_arbitrate(
        self,
        session_id: str,
        llm_suggestion: Optional[Dict[str, Any]] = None,
        pattern_suggestion: Optional[Dict[str, Any]] = None,
        proactive_suggestion: Optional[Dict[str, Any]] = None
    ) -> Decision:
        """Quick arbitration with common suggestion formats"""
        suggestions = []

        if llm_suggestion:
            suggestions.append(Suggestion(
                source=SuggestionSource.LLM,
                action_type=ActionType(llm_suggestion.get("action_type", "wait")),
                action_content=llm_suggestion.get("content", ""),
                confidence=llm_suggestion.get("confidence", 0.7),
                reasoning=llm_suggestion.get("reasoning", ""),
            ))

        if pattern_suggestion:
            suggestions.append(Suggestion(
                source=SuggestionSource.PATTERN,
                action_type=ActionType(pattern_suggestion.get("action_type", "wait")),
                action_content=pattern_suggestion.get("content", ""),
                confidence=pattern_suggestion.get("confidence", 0.5),
                reasoning=pattern_suggestion.get("reasoning", ""),
            ))

        if proactive_suggestion:
            suggestions.append(Suggestion(
                source=SuggestionSource.PROACTIVE,
                action_type=ActionType(proactive_suggestion.get("action_type", "nudge")),
                action_content=proactive_suggestion.get("content", ""),
                confidence=proactive_suggestion.get("confidence", 0.6),
                reasoning=proactive_suggestion.get("reasoning", ""),
            ))

        result = self.arbitrate(session_id, suggestions)
        return result.decision

    # ==================== Decision Override ====================

    def override(
        self,
        decision_id: str,
        new_action_type: ActionType,
        new_content: str,
        reason: str
    ) -> Decision:
        """Override a decision with a new action"""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM arbiter_decisions WHERE decision_id = ?
            """, (decision_id,)).fetchone()

            if not row:
                raise ValueError(f"Decision {decision_id} not found")

            # Create override entry
            conn.execute("""
                UPDATE arbiter_decisions SET
                    action_type = ?,
                    action_content = ?,
                    overridden = 1,
                    override_reason = ?
                WHERE decision_id = ?
            """, (new_action_type.value, new_content, reason, decision_id))

            self._log_audit(decision_id, "overridden", {
                "original_action": row["action_type"],
                "new_action": new_action_type.value,
                "reason": reason,
            })

            # Return updated decision
            updated = conn.execute("""
                SELECT * FROM arbiter_decisions WHERE decision_id = ?
            """, (decision_id,)).fetchone()

            return self._row_to_decision(updated)

    # ==================== Audit & Explanation ====================

    def explain(self, decision_id: str) -> Dict[str, Any]:
        """Get detailed explanation of a decision"""
        with self._get_conn() as conn:
            decision = conn.execute("""
                SELECT * FROM arbiter_decisions WHERE decision_id = ?
            """, (decision_id,)).fetchone()

            if not decision:
                return {"error": "Decision not found"}

            audit_log = conn.execute("""
                SELECT * FROM decision_audit
                WHERE decision_id = ?
                ORDER BY created_at ASC
            """, (decision_id,)).fetchall()

            d = self._row_to_decision(decision)

            return {
                "decision": d.to_dict(),
                "detailed_explanation": {
                    "action": f"Take action: {d.action_type.value}",
                    "content": d.action_content or "(no specific content)",
                    "sources": f"Based on: {', '.join(d.contributing_sources)}",
                    "conflicts": f"Resolved: {', '.join(d.conflicts_resolved)}" if d.conflicts_resolved else "No conflicts",
                    "safety": "Verified safe" if d.safety_verified else "Safety concerns present",
                    "override": f"Overridden: {d.override_reason}" if d.overridden else None,
                },
                "audit_trail": [
                    {
                        "event": row["event_type"],
                        "data": json.loads(row["event_data"] or "{}"),
                        "time": row["created_at"],
                    }
                    for row in audit_log
                ],
            }

    def audit(
        self,
        session_id: str,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get audit trail for session decisions"""
        with self._get_conn() as conn:
            decisions = conn.execute("""
                SELECT * FROM arbiter_decisions
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (session_id, limit)).fetchall()

            result = []
            for row in decisions:
                d = self._row_to_decision(row)
                result.append({
                    "decision_id": d.decision_id,
                    "action": d.action_type.value,
                    "content_preview": d.action_content[:50] if d.action_content else "",
                    "confidence": d.confidence,
                    "sources": d.contributing_sources,
                    "overridden": d.overridden,
                    "outcome": d.outcome,
                    "created_at": d.created_at,
                })

            return result

    def _log_audit(
        self,
        decision_id: str,
        event_type: str,
        event_data: Dict[str, Any]
    ):
        """Log an audit event"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO decision_audit (
                    audit_id, decision_id, event_type, event_data, created_at
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                str(uuid.uuid4())[:8],
                decision_id,
                event_type,
                json.dumps(event_data),
                int(time.time())
            ))

    def record_outcome(
        self,
        decision_id: str,
        outcome: str  # success/failure/ignored
    ):
        """Record the outcome of a decision"""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE arbiter_decisions SET
                    outcome = ?,
                    executed_at = ?
                WHERE decision_id = ?
            """, (outcome, int(time.time()), decision_id))

            self._log_audit(decision_id, "outcome_recorded", {
                "outcome": outcome,
            })

    # ==================== Statistics ====================

    def get_stats(
        self,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get decision statistics"""
        with self._get_conn() as conn:
            where_clause = ""
            params = []

            if session_id:
                where_clause = "WHERE session_id = ?"
                params.append(session_id)

            total = conn.execute(f"""
                SELECT COUNT(*) as count FROM arbiter_decisions {where_clause}
            """, params).fetchone()["count"]

            by_action = conn.execute(f"""
                SELECT action_type, COUNT(*) as count
                FROM arbiter_decisions {where_clause}
                GROUP BY action_type
            """, params).fetchall()

            by_outcome = conn.execute(f"""
                SELECT outcome, COUNT(*) as count
                FROM arbiter_decisions {where_clause}
                    {"AND" if where_clause else "WHERE"} outcome IS NOT NULL
                GROUP BY outcome
            """, params).fetchall()

            overrides = conn.execute(f"""
                SELECT COUNT(*) as count
                FROM arbiter_decisions {where_clause}
                    {"AND" if where_clause else "WHERE"} overridden = 1
            """, params).fetchone()["count"]

            return {
                "total_decisions": total,
                "by_action": {row["action_type"]: row["count"] for row in by_action},
                "by_outcome": {row["outcome"]: row["count"] for row in by_outcome},
                "override_count": overrides,
                "override_rate": round(overrides / max(1, total), 3),
            }


# ==================== CLI Interface ====================

def main():
    parser = argparse.ArgumentParser(
        description="Claude Monitor Decision Arbiter"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # arbitrate command
    arb_parser = subparsers.add_parser("arbitrate", help="Arbitrate suggestions")
    arb_parser.add_argument("session_id", help="Session ID")
    arb_parser.add_argument("--suggestions", help="Suggestions JSON array")

    # explain command
    explain_parser = subparsers.add_parser("explain", help="Explain a decision")
    explain_parser.add_argument("decision_id", help="Decision ID")

    # audit command
    audit_parser = subparsers.add_parser("audit", help="Get audit trail")
    audit_parser.add_argument("session_id", help="Session ID")
    audit_parser.add_argument("--limit", type=int, default=20, help="Max entries")

    # override command
    override_parser = subparsers.add_parser("override", help="Override a decision")
    override_parser.add_argument("decision_id", help="Decision ID")
    override_parser.add_argument("new_action", choices=[a.value for a in ActionType])
    override_parser.add_argument("--content", default="", help="New action content")
    override_parser.add_argument("--reason", required=True, help="Override reason")

    # stats command
    stats_parser = subparsers.add_parser("stats", help="Show statistics")
    stats_parser.add_argument("--session", help="Session ID filter")

    args = parser.parse_args()
    arbiter = DecisionArbiter()

    if args.command == "arbitrate":
        suggestions = []
        if args.suggestions:
            raw = json.loads(args.suggestions)
            if not isinstance(raw, list):
                raw = []

            for s in raw:
                if not isinstance(s, dict):
                    continue

                source_raw = s.get("source", "heuristic")
                action_raw = s.get("action_type", "wait")
                try:
                    source = SuggestionSource(source_raw)
                except Exception:
                    source = SuggestionSource.HEURISTIC
                try:
                    action_type = ActionType(action_raw)
                except Exception:
                    action_type = ActionType.WAIT

                content = s.get("content")
                if content is None:
                    content = s.get("action_content")
                if content is None:
                    content = s.get("action") or ""

                try:
                    confidence = float(s.get("confidence", 0.5) or 0.5)
                except Exception:
                    confidence = 0.5
                try:
                    priority = int(s.get("priority", 0) or 0)
                except Exception:
                    priority = 0
                try:
                    safety_score = float(s.get("safety_score", 1.0) or 1.0)
                except Exception:
                    safety_score = 1.0

                suggestions.append(Suggestion(
                    source=source,
                    action_type=action_type,
                    action_content=str(content or ""),
                    confidence=confidence,
                    priority=priority,
                    safety_score=safety_score,
                    reasoning=str(s.get("reasoning", "") or ""),
                    metadata=s.get("metadata") if isinstance(s.get("metadata"), dict) else {},
                ))

        result = arbiter.arbitrate(args.session_id, suggestions)
        print(json.dumps(result.to_dict(), indent=2))

    elif args.command == "explain":
        explanation = arbiter.explain(args.decision_id)
        print(json.dumps(explanation, indent=2))

    elif args.command == "audit":
        audit = arbiter.audit(args.session_id, args.limit)
        print(json.dumps(audit, indent=2))

    elif args.command == "override":
        decision = arbiter.override(
            args.decision_id,
            ActionType(args.new_action),
            args.content,
            args.reason
        )
        print(json.dumps(decision.to_dict(), indent=2))

    elif args.command == "stats":
        stats = arbiter.get_stats(args.session)
        print(json.dumps(stats, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
