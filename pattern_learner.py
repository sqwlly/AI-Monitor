#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Pattern Learner
模式学习引擎 - 从历史决策中提取可复用模式

功能：
1. 成功模式提取（输入/行动/结果/上下文模式）
2. 失败模式提取（特征识别/原因分类/规避策略）
3. 模式存储（指纹/置信度/统计）
4. 模式应用（匹配/排序/反馈）

Usage:
    python3 pattern_learner.py extract <session_id>
    python3 pattern_learner.py match <input_text> [--limit 5]
    python3 pattern_learner.py apply <pattern_id> [--record-outcome success|failure]
    python3 pattern_learner.py stats [--type success|failure|all]
    python3 pattern_learner.py prune [--min-confidence 0.3] [--max-age-days 90]
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
from typing import Any, Dict, List, Optional, Tuple

# Database path
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


class PatternType(Enum):
    """Pattern type classification"""
    SUCCESS = "success"        # Successfully resolved situation
    FAILURE = "failure"        # Failed attempt to avoid
    NEUTRAL = "neutral"        # Informational pattern


class TriggerCategory(Enum):
    """Trigger situation category"""
    ERROR = "error"                    # Error occurred
    STUCK = "stuck"                    # Progress stalled
    LOOP = "loop"                      # Repeated actions
    DEVIATION = "deviation"            # Off-track from goal
    COMPLETION = "completion"          # Task completed
    DEPENDENCY = "dependency"          # Missing dependency
    PERMISSION = "permission"          # Permission issue
    NETWORK = "network"                # Network issue
    CONFIG = "config"                  # Configuration issue
    UNKNOWN = "unknown"                # Unclassified


@dataclass
class Pattern:
    """Learned pattern"""
    pattern_id: str = ""
    pattern_type: PatternType = PatternType.NEUTRAL
    trigger_signature: str = ""        # Trigger condition fingerprint
    trigger_category: TriggerCategory = TriggerCategory.UNKNOWN
    trigger_keywords: List[str] = field(default_factory=list)
    action_template: str = ""          # Action template
    action_type: str = ""              # wait/command/nudge/ask/notify
    expected_outcome: str = ""         # Expected result
    context_constraints: Dict[str, Any] = field(default_factory=dict)
    source_sessions: List[str] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    confidence: float = 0.5
    last_used_at: int = 0
    created_at: int = 0

    def to_dict(self) -> Dict:
        return {
            "pattern_id": self.pattern_id,
            "pattern_type": self.pattern_type.value,
            "trigger_signature": self.trigger_signature,
            "trigger_category": self.trigger_category.value,
            "trigger_keywords": self.trigger_keywords,
            "action_template": self.action_template,
            "action_type": self.action_type,
            "expected_outcome": self.expected_outcome,
            "context_constraints": self.context_constraints,
            "source_sessions": self.source_sessions,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "confidence": self.confidence,
            "last_used_at": self.last_used_at,
            "created_at": self.created_at,
        }

    @property
    def total_uses(self) -> int:
        return self.success_count + self.failure_count

    @property
    def success_rate(self) -> float:
        if self.total_uses == 0:
            return 0.5
        return self.success_count / self.total_uses


@dataclass
class PatternMatch:
    """Pattern match result"""
    pattern: Pattern
    similarity: float              # 0.0 - 1.0
    matched_keywords: List[str]    # Keywords that matched
    context_fit: float             # Context compatibility 0.0 - 1.0

    @property
    def score(self) -> float:
        """Combined match score"""
        return (
            self.similarity * 0.4 +
            self.pattern.confidence * 0.3 +
            self.pattern.success_rate * 0.2 +
            self.context_fit * 0.1
        )


class PatternLearner:
    """Pattern learning engine"""

    # Trigger patterns for categorization
    TRIGGER_PATTERNS = {
        TriggerCategory.ERROR: [
            r"error", r"exception", r"failed", r"failure", r"traceback",
            r"syntaxerror", r"typeerror", r"valueerror", r"keyerror",
            r"attributeerror", r"importerror", r"modulenotfound",
        ],
        TriggerCategory.STUCK: [
            r"stuck", r"hanging", r"waiting", r"idle", r"no progress",
            r"timeout", r"blocked", r"pending",
        ],
        TriggerCategory.LOOP: [
            r"loop", r"repeated", r"again", r"same error", r"retry",
            r"circular", r"infinite", r"recursion",
        ],
        TriggerCategory.DEVIATION: [
            r"wrong", r"incorrect", r"unexpected", r"unrelated",
            r"off-track", r"deviated", r"tangent",
        ],
        TriggerCategory.COMPLETION: [
            r"success", r"completed", r"done", r"finished", r"passed",
            r"build succeeded", r"tests passed", r"all green",
        ],
        TriggerCategory.DEPENDENCY: [
            r"missing", r"not found", r"not installed", r"dependency",
            r"package", r"module", r"require", r"npm", r"pip",
        ],
        TriggerCategory.PERMISSION: [
            r"permission", r"denied", r"access", r"forbidden",
            r"unauthorized", r"sudo", r"root",
        ],
        TriggerCategory.NETWORK: [
            r"network", r"connection", r"timeout", r"unreachable",
            r"dns", r"socket", r"http", r"ssl",
        ],
        TriggerCategory.CONFIG: [
            r"config", r"configuration", r"settings", r"env",
            r"environment", r"variable", r"missing key",
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
                CREATE TABLE IF NOT EXISTS learned_patterns (
                    pattern_id TEXT PRIMARY KEY,
                    pattern_type TEXT NOT NULL,
                    trigger_signature TEXT NOT NULL,
                    trigger_category TEXT DEFAULT 'unknown',
                    trigger_keywords TEXT,
                    action_template TEXT,
                    action_type TEXT,
                    expected_outcome TEXT,
                    context_constraints TEXT,
                    source_sessions TEXT,
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    confidence REAL DEFAULT 0.5,
                    last_used_at INTEGER,
                    created_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_patterns_type
                    ON learned_patterns(pattern_type);
                CREATE INDEX IF NOT EXISTS idx_patterns_category
                    ON learned_patterns(trigger_category);
                CREATE INDEX IF NOT EXISTS idx_patterns_confidence
                    ON learned_patterns(confidence DESC);
                CREATE INDEX IF NOT EXISTS idx_patterns_signature
                    ON learned_patterns(trigger_signature);
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

    def _row_to_pattern(self, row: sqlite3.Row) -> Pattern:
        """Convert database row to Pattern object"""
        return Pattern(
            pattern_id=row["pattern_id"],
            pattern_type=PatternType(row["pattern_type"]),
            trigger_signature=row["trigger_signature"],
            trigger_category=TriggerCategory(row["trigger_category"] or "unknown"),
            trigger_keywords=json.loads(row["trigger_keywords"] or "[]"),
            action_template=row["action_template"] or "",
            action_type=row["action_type"] or "",
            expected_outcome=row["expected_outcome"] or "",
            context_constraints=json.loads(row["context_constraints"] or "{}"),
            source_sessions=json.loads(row["source_sessions"] or "[]"),
            success_count=row["success_count"] or 0,
            failure_count=row["failure_count"] or 0,
            confidence=row["confidence"] or 0.5,
            last_used_at=row["last_used_at"] or 0,
            created_at=row["created_at"] or 0,
        )

    # ==================== Pattern Extraction ====================

    def extract_from_session(self, session_id: str) -> List[Pattern]:
        """Extract patterns from a session's decision history"""
        patterns = []

        with self._get_conn() as conn:
            # Get session decisions
            decisions = conn.execute("""
                SELECT * FROM decisions
                WHERE session_id = ?
                ORDER BY timestamp ASC
            """, (session_id,)).fetchall()

            if not decisions:
                return patterns

            # Analyze decision sequences
            for i, decision in enumerate(decisions):
                # Get context (previous decisions)
                context_window = decisions[max(0, i - 3):i]
                next_decisions = decisions[i + 1:i + 3]

                # Try to extract pattern from this decision
                pattern = self._extract_pattern_from_decision(
                    decision, context_window, next_decisions, session_id
                )
                if pattern:
                    patterns.append(pattern)

        # Merge similar patterns
        merged = self._merge_similar_patterns(patterns)

        # Save patterns to database
        for pattern in merged:
            self._save_pattern(pattern)

        return merged

    def _extract_pattern_from_decision(
        self,
        decision: sqlite3.Row,
        context: List[sqlite3.Row],
        next_decisions: List[sqlite3.Row],
        session_id: str
    ) -> Optional[Pattern]:
        """Extract a pattern from a single decision with context"""
        outcome = decision["outcome"]
        if not outcome:
            return None

        # Determine pattern type based on outcome
        if outcome in ["wait", "ok"]:
            pattern_type = PatternType.SUCCESS
        elif outcome in ["nudge", "command"]:
            # Check if subsequent decisions indicate success
            if next_decisions and any(d["outcome"] in ["wait", "ok"] for d in next_decisions):
                pattern_type = PatternType.SUCCESS
            else:
                pattern_type = PatternType.NEUTRAL
        else:
            pattern_type = PatternType.NEUTRAL

        # Build trigger context
        input_preview = decision["input_preview"] or ""
        trigger_category = self._categorize_trigger(input_preview)
        trigger_keywords = self._extract_keywords(input_preview)
        trigger_signature = self._compute_signature(trigger_keywords)

        # Build action template
        output = decision["output"] or ""
        action_type = outcome

        # Build context constraints
        context_constraints = {
            "stage": decision["stage"],
            "role": decision["role"],
            "previous_outcomes": [d["outcome"] for d in context if d["outcome"]],
        }

        return Pattern(
            pattern_id=str(uuid.uuid4())[:8],
            pattern_type=pattern_type,
            trigger_signature=trigger_signature,
            trigger_category=trigger_category,
            trigger_keywords=trigger_keywords,
            action_template=output,
            action_type=action_type,
            expected_outcome="resolved" if pattern_type == PatternType.SUCCESS else "unknown",
            context_constraints=context_constraints,
            source_sessions=[session_id],
            success_count=1 if pattern_type == PatternType.SUCCESS else 0,
            failure_count=0,
            confidence=0.5,
            created_at=int(time.time()),
        )

    def _categorize_trigger(self, text: str) -> TriggerCategory:
        """Categorize trigger based on text content"""
        text_lower = text.lower()

        for category, patterns in self.TRIGGER_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    return category

        return TriggerCategory.UNKNOWN

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract significant keywords from text"""
        # Normalize text
        text = text.lower()

        # Remove common words
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "must", "shall",
            "can", "need", "dare", "ought", "used", "to", "of", "in",
            "for", "on", "with", "at", "by", "from", "as", "into",
            "through", "during", "before", "after", "above", "below",
            "between", "under", "again", "further", "then", "once",
            "here", "there", "when", "where", "why", "how", "all",
            "each", "few", "more", "most", "other", "some", "such",
            "no", "nor", "not", "only", "own", "same", "so", "than",
            "too", "very", "just", "and", "but", "if", "or", "because",
            "until", "while", "this", "that", "these", "those", "it",
        }

        # Extract words
        words = re.findall(r'\b[a-z][a-z0-9_]{2,}\b', text)

        # Filter and deduplicate
        keywords = []
        seen = set()
        for word in words:
            if word not in stop_words and word not in seen:
                keywords.append(word)
                seen.add(word)

        return keywords[:20]  # Limit to top 20 keywords

    def _compute_signature(self, keywords: List[str]) -> str:
        """Compute a stable signature from keywords"""
        if not keywords:
            return "empty"

        # Sort keywords for stability
        sorted_keywords = sorted(set(keywords))
        content = "|".join(sorted_keywords)
        return hashlib.md5(content.encode()).hexdigest()[:12]

    def _merge_similar_patterns(self, patterns: List[Pattern]) -> List[Pattern]:
        """Merge patterns with similar signatures"""
        if not patterns:
            return []

        merged = {}
        for pattern in patterns:
            sig = pattern.trigger_signature
            if sig in merged:
                # Merge into existing pattern
                existing = merged[sig]
                existing.success_count += pattern.success_count
                existing.failure_count += pattern.failure_count
                existing.source_sessions.extend(pattern.source_sessions)
                existing.source_sessions = list(set(existing.source_sessions))
                # Update confidence based on accumulated data
                existing.confidence = self._compute_confidence(existing)
            else:
                merged[sig] = pattern

        return list(merged.values())

    def _compute_confidence(self, pattern: Pattern) -> float:
        """Compute pattern confidence based on usage statistics"""
        total = pattern.total_uses
        if total == 0:
            return 0.5

        # Base confidence on success rate with Bayesian smoothing
        # Prior: 0.5 with weight of 2 observations
        prior_weight = 2
        smoothed_success = (pattern.success_count + prior_weight * 0.5)
        smoothed_total = total + prior_weight
        confidence = smoothed_success / smoothed_total

        # Boost confidence for well-tested patterns
        experience_factor = min(1.0, total / 10)  # Max boost at 10 uses
        confidence = confidence * (0.7 + 0.3 * experience_factor)

        return round(confidence, 3)

    def _save_pattern(self, pattern: Pattern):
        """Save or update pattern in database"""
        with self._get_conn() as conn:
            # Check if similar pattern exists
            existing = conn.execute("""
                SELECT pattern_id FROM learned_patterns
                WHERE trigger_signature = ?
            """, (pattern.trigger_signature,)).fetchone()

            if existing:
                # Update existing pattern
                conn.execute("""
                    UPDATE learned_patterns SET
                        success_count = success_count + ?,
                        failure_count = failure_count + ?,
                        source_sessions = ?,
                        confidence = ?,
                        last_used_at = ?
                    WHERE pattern_id = ?
                """, (
                    pattern.success_count,
                    pattern.failure_count,
                    json.dumps(pattern.source_sessions),
                    pattern.confidence,
                    int(time.time()),
                    existing["pattern_id"],
                ))
            else:
                # Insert new pattern
                conn.execute("""
                    INSERT INTO learned_patterns (
                        pattern_id, pattern_type, trigger_signature,
                        trigger_category, trigger_keywords, action_template,
                        action_type, expected_outcome, context_constraints,
                        source_sessions, success_count, failure_count,
                        confidence, last_used_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pattern.pattern_id,
                    pattern.pattern_type.value,
                    pattern.trigger_signature,
                    pattern.trigger_category.value,
                    json.dumps(pattern.trigger_keywords),
                    pattern.action_template,
                    pattern.action_type,
                    pattern.expected_outcome,
                    json.dumps(pattern.context_constraints),
                    json.dumps(pattern.source_sessions),
                    pattern.success_count,
                    pattern.failure_count,
                    pattern.confidence,
                    int(time.time()),
                    pattern.created_at,
                ))

    # ==================== Pattern Matching ====================

    def match(
        self,
        input_text: str,
        context: Optional[Dict[str, Any]] = None,
        limit: int = 5
    ) -> List[PatternMatch]:
        """Find patterns matching the input"""
        matches = []
        context = context or {}

        # Extract features from input
        input_keywords = self._extract_keywords(input_text)
        input_category = self._categorize_trigger(input_text)

        with self._get_conn() as conn:
            # Get candidate patterns
            patterns = conn.execute("""
                SELECT * FROM learned_patterns
                WHERE pattern_type IN ('success', 'neutral')
                  AND confidence >= 0.3
                ORDER BY confidence DESC, success_count DESC
                LIMIT 100
            """).fetchall()

            for row in patterns:
                pattern = self._row_to_pattern(row)
                match = self._compute_match(
                    pattern, input_keywords, input_category, context
                )
                if match.similarity > 0.2:
                    matches.append(match)

        # Sort by score and return top matches
        matches.sort(key=lambda m: m.score, reverse=True)
        return matches[:limit]

    def _compute_match(
        self,
        pattern: Pattern,
        input_keywords: List[str],
        input_category: TriggerCategory,
        context: Dict[str, Any]
    ) -> PatternMatch:
        """Compute match between pattern and input"""
        # Keyword similarity (Jaccard)
        pattern_keywords = set(pattern.trigger_keywords)
        input_kw_set = set(input_keywords)

        if pattern_keywords and input_kw_set:
            intersection = pattern_keywords & input_kw_set
            union = pattern_keywords | input_kw_set
            keyword_similarity = len(intersection) / len(union)
            matched_keywords = list(intersection)
        else:
            keyword_similarity = 0.0
            matched_keywords = []

        # Category match bonus
        category_bonus = 0.3 if pattern.trigger_category == input_category else 0.0

        # Combined similarity
        similarity = min(1.0, keyword_similarity + category_bonus)

        # Context fit
        context_fit = self._compute_context_fit(pattern, context)

        return PatternMatch(
            pattern=pattern,
            similarity=similarity,
            matched_keywords=matched_keywords,
            context_fit=context_fit,
        )

    def _compute_context_fit(
        self,
        pattern: Pattern,
        context: Dict[str, Any]
    ) -> float:
        """Compute how well context matches pattern constraints"""
        if not pattern.context_constraints or not context:
            return 0.5

        constraints = pattern.context_constraints
        matches = 0
        total = 0

        # Check stage match
        if "stage" in constraints and "stage" in context:
            total += 1
            if constraints["stage"] == context["stage"]:
                matches += 1

        # Check role match
        if "role" in constraints and "role" in context:
            total += 1
            if constraints["role"] == context["role"]:
                matches += 1

        if total == 0:
            return 0.5

        return matches / total

    # ==================== Pattern Application ====================

    def get_suggestion(
        self,
        input_text: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Get best pattern-based suggestion for input"""
        matches = self.match(input_text, context, limit=1)
        if not matches:
            return None

        best = matches[0]
        if best.score < 0.4:
            return None

        return {
            "pattern_id": best.pattern.pattern_id,
            "action_type": best.pattern.action_type,
            "action_template": best.pattern.action_template,
            "confidence": best.pattern.confidence,
            "match_score": best.score,
            "matched_keywords": best.matched_keywords,
            "source": "pattern_learner",
        }

    def record_outcome(
        self,
        pattern_id: str,
        outcome: str  # "success" or "failure"
    ):
        """Record outcome of applying a pattern"""
        with self._get_conn() as conn:
            if outcome == "success":
                conn.execute("""
                    UPDATE learned_patterns SET
                        success_count = success_count + 1,
                        last_used_at = ?,
                        confidence = (success_count + 1.0) / (success_count + failure_count + 2.0)
                    WHERE pattern_id = ?
                """, (int(time.time()), pattern_id))
            else:
                conn.execute("""
                    UPDATE learned_patterns SET
                        failure_count = failure_count + 1,
                        last_used_at = ?,
                        confidence = (success_count + 1.0) / (success_count + failure_count + 2.0)
                    WHERE pattern_id = ?
                """, (int(time.time()), pattern_id))

    # ==================== Failure Pattern Extraction ====================

    def extract_failure_patterns(self, session_id: str) -> List[Pattern]:
        """Extract failure patterns to avoid"""
        patterns = []

        with self._get_conn() as conn:
            # Find repeated errors or loops in session
            decisions = conn.execute("""
                SELECT * FROM decisions
                WHERE session_id = ?
                ORDER BY timestamp ASC
            """, (session_id,)).fetchall()

            # Detect loops (same action repeated without progress)
            action_counts = defaultdict(int)
            for decision in decisions:
                action_key = f"{decision['stage']}:{decision['outcome']}"
                action_counts[action_key] += 1

            # Create failure patterns for repeated actions
            for action_key, count in action_counts.items():
                if count >= 3:  # Threshold for loop detection
                    pattern = Pattern(
                        pattern_id=str(uuid.uuid4())[:8],
                        pattern_type=PatternType.FAILURE,
                        trigger_signature=hashlib.md5(action_key.encode()).hexdigest()[:12],
                        trigger_category=TriggerCategory.LOOP,
                        trigger_keywords=[action_key.replace(":", " ").split()[0]],
                        action_template=f"Avoid repeating: {action_key}",
                        action_type="avoid",
                        expected_outcome="loop_detected",
                        context_constraints={"loop_count": count},
                        source_sessions=[session_id],
                        failure_count=count,
                        confidence=0.7,
                        created_at=int(time.time()),
                    )
                    patterns.append(pattern)
                    self._save_pattern(pattern)

        return patterns

    # ==================== Statistics & Maintenance ====================

    def get_stats(
        self,
        pattern_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get pattern statistics"""
        with self._get_conn() as conn:
            where_clause = ""
            params = []
            if pattern_type and pattern_type != "all":
                where_clause = "WHERE pattern_type = ?"
                params.append(pattern_type)

            total = conn.execute(f"""
                SELECT COUNT(*) as count FROM learned_patterns {where_clause}
            """, params).fetchone()["count"]

            by_type = conn.execute("""
                SELECT pattern_type, COUNT(*) as count
                FROM learned_patterns
                GROUP BY pattern_type
            """).fetchall()

            by_category = conn.execute("""
                SELECT trigger_category, COUNT(*) as count
                FROM learned_patterns
                GROUP BY trigger_category
            """).fetchall()

            avg_confidence = conn.execute(f"""
                SELECT AVG(confidence) as avg FROM learned_patterns {where_clause}
            """, params).fetchone()["avg"] or 0

            most_used = conn.execute("""
                SELECT pattern_id, action_template, success_count, failure_count, confidence
                FROM learned_patterns
                ORDER BY (success_count + failure_count) DESC
                LIMIT 5
            """).fetchall()

            return {
                "total_patterns": total,
                "by_type": {row["pattern_type"]: row["count"] for row in by_type},
                "by_category": {row["trigger_category"]: row["count"] for row in by_category},
                "average_confidence": round(avg_confidence, 3),
                "most_used": [
                    {
                        "pattern_id": row["pattern_id"],
                        "action": row["action_template"][:50],
                        "uses": row["success_count"] + row["failure_count"],
                        "confidence": row["confidence"],
                    }
                    for row in most_used
                ],
            }

    def prune(
        self,
        min_confidence: float = 0.3,
        max_age_days: int = 90
    ) -> int:
        """Remove low-quality or stale patterns"""
        cutoff_time = int(time.time()) - (max_age_days * 86400)

        with self._get_conn() as conn:
            result = conn.execute("""
                DELETE FROM learned_patterns
                WHERE (confidence < ? AND (success_count + failure_count) < 5)
                   OR (last_used_at < ? AND last_used_at > 0)
            """, (min_confidence, cutoff_time))

            return result.rowcount

    def get_pattern(self, pattern_id: str) -> Optional[Pattern]:
        """Get a specific pattern by ID"""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM learned_patterns WHERE pattern_id = ?
            """, (pattern_id,)).fetchone()

            if row:
                return self._row_to_pattern(row)
            return None


# ==================== CLI Interface ====================

def main():
    parser = argparse.ArgumentParser(
        description="Claude Monitor Pattern Learner"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # extract command
    extract_parser = subparsers.add_parser(
        "extract", help="Extract patterns from session"
    )
    extract_parser.add_argument("session_id", help="Session ID")

    # match command
    match_parser = subparsers.add_parser(
        "match", help="Find matching patterns"
    )
    match_parser.add_argument("session_id", help="Session ID (for context)")
    match_parser.add_argument("input_text", help="Input text to match")
    match_parser.add_argument("--limit", type=int, default=5, help="Max results")

    # learn command
    learn_parser = subparsers.add_parser(
        "learn", help="Learn a new pattern from experience"
    )
    learn_parser.add_argument("session_id", help="Session ID")
    learn_parser.add_argument("trigger_text", help="Text that triggered the action")
    learn_parser.add_argument("action", help="Action taken")
    learn_parser.add_argument(
        "outcome",
        choices=["success", "failure", "neutral"],
        help="Outcome of the action"
    )

    # apply command
    apply_parser = subparsers.add_parser(
        "apply", help="Record pattern application outcome"
    )
    apply_parser.add_argument("pattern_id", help="Pattern ID")
    apply_parser.add_argument(
        "--record-outcome",
        choices=["success", "failure"],
        required=True,
        help="Outcome of applying pattern"
    )

    # stats command
    stats_parser = subparsers.add_parser("stats", help="Show statistics")
    stats_parser.add_argument(
        "--type",
        choices=["success", "failure", "neutral", "all"],
        default="all",
        help="Pattern type filter"
    )

    # prune command
    prune_parser = subparsers.add_parser(
        "prune", help="Remove low-quality patterns"
    )
    prune_parser.add_argument(
        "--min-confidence", type=float, default=0.3,
        help="Minimum confidence threshold"
    )
    prune_parser.add_argument(
        "--max-age-days", type=int, default=90,
        help="Maximum age in days"
    )

    args = parser.parse_args()
    learner = PatternLearner()

    if args.command == "extract":
        patterns = learner.extract_from_session(args.session_id)
        print(json.dumps([p.to_dict() for p in patterns], indent=2))

    elif args.command == "match":
        matches = learner.match(args.input_text, limit=args.limit)
        if matches:
            # 输出简洁摘要供 shell 注入
            summary_parts = []
            for m in matches[:3]:
                summary_parts.append(f"[pattern] confidence={m.pattern.confidence:.2f} action='{m.pattern.action_template[:50]}'")
            print("\n".join(summary_parts))
        # 如果需要完整 JSON，可用 --json 参数（未来扩展）

    elif args.command == "learn":
        # 从触发文本中提取关键词作为模式
        keywords = learner._extract_keywords(args.trigger_text)
        pattern = Pattern(
            pattern_id=str(uuid.uuid4())[:8],
            session_id=args.session_id,
            trigger_type="output",
            trigger_keywords=keywords[:10],
            trigger_signature=learner._compute_signature(args.trigger_text),
            action_type="command" if not args.action.startswith("WAIT") else "wait",
            action_template=args.action,
            outcome_type=args.outcome,
            confidence=0.5,  # 初始置信度
            success_count=1 if args.outcome == "success" else 0,
            failure_count=1 if args.outcome == "failure" else 0,
            last_used_at=int(time.time()),
            created_at=int(time.time()),
        )
        learner._save_pattern(pattern)
        print(f"Learned pattern {pattern.pattern_id}")

    elif args.command == "apply":
        learner.record_outcome(args.pattern_id, args.record_outcome)
        print(f"Recorded {args.record_outcome} for pattern {args.pattern_id}")

    elif args.command == "stats":
        stats = learner.get_stats(args.type)
        print(json.dumps(stats, indent=2))

    elif args.command == "prune":
        removed = learner.prune(args.min_confidence, args.max_age_days)
        print(f"Removed {removed} patterns")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
