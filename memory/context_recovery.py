#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Context Recovery
Provides project context and resume information
"""

import json
from typing import Dict, List, Optional

# Python 3.6 compatibility
try:
    from typing import Any
except ImportError:
    pass

from .database import Database
from base import DataClassMixin


class ResumeContext(DataClassMixin):
    """Resume context data container"""

    def __init__(
        self,
        last_session_id: Optional[str] = None,
        last_stage: Optional[str] = None,
        last_summary: Optional[str] = None,
        total_sessions: int = 0,
        total_commands: int = 0,
        recent_decisions: Optional[List[Dict]] = None,
        learned_patterns: Optional[List[str]] = None,
        stage_distribution: Optional[Dict[str, int]] = None,
        **kwargs
    ):
        self.last_session_id = last_session_id
        self.last_stage = last_stage
        self.last_summary = last_summary
        self.total_sessions = total_sessions
        self.total_commands = total_commands
        self.recent_decisions = recent_decisions or []
        self.learned_patterns = learned_patterns or []
        self.stage_distribution = stage_distribution or {}


class ContextRecovery:
    """
    Context recovery manager

    Responsibilities:
    1. Get project progress context
    2. Provide resume information
    3. Track learned patterns
    """

    def __init__(self, db: Database = None):
        """
        Initialize context recovery

        Args:
            db: Database instance (creates default if None)
        """
        self.db = db or Database()

    def get_resume_context(self, project_path: str) -> Optional[Dict[str, Any]]:
        """
        Get context for resuming work on a project

        Args:
            project_path: Project root path

        Returns:
            Resume context dictionary or None if no history
        """
        # Get project progress
        row = self.db.execute("""
            SELECT last_session_id, total_sessions, total_commands,
                   stage_distribution, learned_patterns
            FROM project_progress
            WHERE project_path = ?
        """, (project_path,), fetch_one=True)

        if not row:
            return None

        # Get last session details
        session_row = self.db.execute("""
            SELECT session_id, last_stage as stage, summary, end_time, status
            FROM sessions
            WHERE session_id = ?
        """, (row['last_session_id'],), fetch_one=True)

        if not session_row:
            return None

        # Get recent decisions
        decisions_rows = self.db.execute("""
            SELECT stage, role, output, outcome, timestamp
            FROM decisions
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT 5
        """, (row['last_session_id'],), fetch_all=True)

        recent_decisions = []
        for dr in decisions_rows:
            recent_decisions.append({
                'stage': dr['stage'],
                'role': dr['role'],
                'output': dr['output'][:100],
                'outcome': dr['outcome'],
                'timestamp': dr['timestamp'],
            })

        # Parse JSON fields
        stage_distribution = json.loads(row['stage_distribution'] or '{}')
        learned_patterns = json.loads(row['learned_patterns'] or '[]')

        return {
            'last_session_id': row['last_session_id'],
            'last_stage': session_row['stage'],
            'last_summary': session_row['summary'],
            'last_status': session_row['status'],
            'total_sessions': row['total_sessions'],
            'total_commands': row['total_commands'],
            'recent_decisions': recent_decisions,
            'stage_distribution': stage_distribution,
            'learned_patterns': learned_patterns,
        }

    def update_project_progress(
        self,
        project_path: str,
        session_id: str,
        stage: Optional[str] = None
    ):
        """
        Update project progress tracking

        Args:
            project_path: Project root
            session_id: Current session
            stage: Current stage
        """
        if not stage:
            return

        # Get current distribution
        row = self.db.execute("""
            SELECT stage_distribution FROM project_progress WHERE project_path = ?
        """, (project_path,), fetch_one=True)

        if row:
            distribution = json.loads(row['stage_distribution'] or '{}')
        else:
            distribution = {}

        # Update stage count
        distribution[stage] = distribution.get(stage, 0) + 1

        self.db.execute("""
            UPDATE project_progress
            SET stage_distribution = ?
            WHERE project_path = ?
        """, (json.dumps(distribution), project_path))

    def get_stage_history(
        self,
        session_id: str,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get stage transition history for a session

        Args:
            session_id: Session identifier
            limit: Maximum entries

        Returns:
            List of stage timeline entries
        """
        rows = self.db.execute("""
            SELECT stage, entered_at, exited_at, duration_s,
                   commands_sent, waits, role_used
            FROM stage_timeline
            WHERE session_id = ?
            ORDER BY entered_at ASC
            LIMIT ?
        """, (session_id, limit), fetch_all=True)

        return [dict(row) for row in rows]
