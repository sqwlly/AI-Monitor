#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Decision Recorder
Records and queries AI decisions during monitoring
"""

import hashlib
import json
import time
from typing import Dict, List, Optional

# Python 3.6 compatibility
try:
    from typing import Any
except ImportError:
    pass

from .database import Database
from base import DataClassMixin


class Decision(DataClassMixin):
    """Decision data container"""

    def __init__(
        self,
        session_id: str,
        timestamp: int,
        output: str,
        outcome: str,
        stage: Optional[str] = None,
        role: Optional[str] = None,
        input_hash: str = '',
        input_preview: str = '',
        latency_ms: Optional[int] = None,
        **kwargs
    ):
        self.session_id = session_id
        self.timestamp = timestamp
        self.stage = stage
        self.role = role
        self.input_hash = input_hash
        self.input_preview = input_preview
        self.output = output
        self.outcome = outcome
        self.latency_ms = latency_ms


class DecisionRecorder:
    """
    Decision recording and query manager

    Responsibilities:
    1. Record AI decisions with context
    2. Query recent decisions for analysis
    3. Track stage timeline
    """

    def __init__(self, db: Database = None):
        """
        Initialize decision recorder

        Args:
            db: Database instance (creates default if None)
        """
        self.db = db or Database()

    def record_decision(
        self,
        session_id: str,
        stage: Optional[str],
        role: Optional[str],
        output: str,
        outcome: str,
        input_text: Optional[str] = None,
        latency_ms: Optional[int] = None
    ) -> int:
        """
        Record a decision

        Args:
            session_id: Session identifier
            stage: Current stage
            role: AI role used
            output: AI output (command or WAIT)
            outcome: Result of the decision
            input_text: Input context (optional)
            latency_ms: Decision latency in milliseconds

        Returns:
            Decision record ID
        """
        now = int(time.time())
        input_hash = hashlib.sha256((input_text or "").encode()).hexdigest()[:16]
        input_preview = (input_text or "")[:200]

        # Insert decision and get row ID in same transaction
        decision_id = None
        with self.db.get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO decisions
                (session_id, timestamp, stage, role, input_hash, input_preview, output, outcome, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, now, stage, role, input_hash, input_preview, output, outcome, latency_ms))
            decision_id = cursor.lastrowid

        # Update stage timeline
        self._update_stage_timeline(session_id, stage, role, output)

        # Update session last_* fields
        if stage or role:
            updates = []
            params = []
            if stage:
                updates.append("last_stage = ?")
                params.append(stage)
            if role:
                updates.append("last_role = ?")
                params.append(role)
            params.append(session_id)
            self.db.execute(f"""
                UPDATE sessions SET {', '.join(updates)} WHERE session_id = ?
            """, params)

        return decision_id or 0

    def _update_stage_timeline(
        self,
        session_id: str,
        stage: Optional[str],
        role: Optional[str],
        output: str
    ):
        """
        Update stage timeline

        Args:
            session_id: Session identifier
            stage: Current stage
            role: Current role
            output: AI output
        """
        if not stage:
            return

        now = int(time.time())

        # Check if there's an open entry for this stage
        row = self.db.execute("""
            SELECT id FROM stage_timeline
            WHERE session_id = ? AND stage = ? AND exited_at IS NULL
            ORDER BY entered_at DESC LIMIT 1
        """, (session_id, stage), fetch_one=True)

        if row:
            # Update existing entry
            is_command = output.strip() and output.strip().upper() != "WAIT"
            self.db.execute("""
                UPDATE stage_timeline
                SET commands_sent = commands_sent + ?,
                    waits = waits + ?
                WHERE id = ?
            """, (1 if is_command else 0, 0 if is_command else 1, row['id']))
        else:
            # Close previous stage and create new entry
            self.db.execute("""
                UPDATE stage_timeline
                SET exited_at = ?, duration_s = ? - entered_at
                WHERE session_id = ? AND exited_at IS NULL
            """, (now, now, session_id))

            self.db.execute("""
                INSERT INTO stage_timeline
                (session_id, stage, entered_at, role_used, commands_sent, waits)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (session_id, stage, now, role,
                  1 if (output and output.strip().upper() != "WAIT") else 0,
                  0 if (output and output.strip().upper() != "WAIT") else 1))

    def get_recent_decisions(
        self,
        session_id: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get recent decisions for a session

        Args:
            session_id: Session identifier
            limit: Maximum number of decisions

        Returns:
            List of decision dictionaries
        """
        rows = self.db.execute("""
            SELECT id, session_id, timestamp, stage, role, input_preview,
                   output, outcome, latency_ms
            FROM decisions
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (session_id, limit), fetch_all=True)

        return [dict(row) for row in rows]

    def get_decision_stats(self, session_id: str) -> Dict[str, Any]:
        """
        Get decision statistics for a session

        Args:
            session_id: Session identifier

        Returns:
            Statistics dictionary
        """
        row = self.db.execute("""
            SELECT
                COUNT(*) as total_decisions,
                SUM(CASE WHEN outcome != 'WAIT' THEN 1 ELSE 0 END) as commands_sent,
                SUM(CASE WHEN outcome = 'WAIT' THEN 1 ELSE 0 END) as waits,
                AVG(latency_ms) as avg_latency_ms
            FROM decisions
            WHERE session_id = ?
        """, (session_id,), fetch_one=True)

        return {
            'total_decisions': row['total_decisions'] or 0,
            'commands_sent': row['commands_sent'] or 0,
            'waits': row['waits'] or 0,
            'avg_latency_ms': int(row['avg_latency_ms'] or 0),
        }
