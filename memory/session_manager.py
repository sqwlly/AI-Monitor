#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Session Manager
Handles session lifecycle: create, update, query, close
"""

import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

# Python 3.6 compatibility
try:
    from typing import Any
except ImportError:
    pass

from .database import Database
from base import DataClassMixin


class Session(DataClassMixin):
    """Session data container"""

    def __init__(
        self,
        session_id: str,
        target: str,
        project_path: Optional[str] = None,
        start_time: int = 0,
        end_time: Optional[int] = None,
        status: str = 'active',
        summary: Optional[str] = None,
        total_commands: int = 0,
        total_waits: int = 0,
        last_stage: Optional[str] = None,
        last_role: Optional[str] = None,
        **kwargs
    ):
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


class SessionManager:
    """
    Session lifecycle manager

    Responsibilities:
    1. Create new sessions
    2. Update session status and metadata
    3. Query session information
    4. Close sessions
    """

    def __init__(self, db: Database = None):
        """
        Initialize session manager

        Args:
            db: Database instance (creates default if None)
        """
        self.db = db or Database()

    def start_session(self, target: str, project_path: Optional[str] = None) -> str:
        """
        Start a new session

        Args:
            target: Target identifier (e.g., "session:window.pane")
            project_path: Optional project root path

        Returns:
            New session ID
        """
        session_id = str(uuid.uuid4())[:8]
        now = int(time.time())

        # Insert session
        self.db.execute("""
            INSERT INTO sessions (session_id, target, project_path, start_time, status)
            VALUES (?, ?, ?, ?, 'active')
        """, (session_id, target, project_path, now))

        # Update project progress
        if project_path:
            self.db.execute("""
                INSERT INTO project_progress (project_path, last_session_id, total_sessions, last_updated)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(project_path) DO UPDATE SET
                    last_session_id = excluded.last_session_id,
                    total_sessions = total_sessions + 1,
                    last_updated = excluded.last_updated
            """, (project_path, session_id, now))

        return session_id

    def end_session(
        self,
        session_id: str,
        status: str,
        summary: Optional[str] = None
    ) -> bool:
        """
        End a session

        Args:
            session_id: Session to end
            status: Final status (completed, error, etc.)
            summary: Optional summary

        Returns:
            True if successful, False if session not found
        """
        now = int(time.time())

        # Get session info
        row = self.db.execute(
            "SELECT start_time, project_path FROM sessions WHERE session_id = ?",
            (session_id,),
            fetch_one=True
        )

        if not row:
            return False

        start_time = row['start_time']
        project_path = row['project_path']
        duration = now - start_time

        # Close open stage timeline entries
        self.db.execute("""
            UPDATE stage_timeline
            SET exited_at = ?, duration_s = ? - entered_at
            WHERE session_id = ? AND exited_at IS NULL
        """, (now, now, session_id))

        # Update session
        self.db.execute("""
            UPDATE sessions
            SET end_time = ?, status = ?, summary = ?
            WHERE session_id = ?
        """, (now, status, summary, session_id))

        # Update project progress
        if project_path:
            self.db.execute("""
                UPDATE project_progress
                SET total_time_s = total_time_s + ?,
                    last_updated = ?
                WHERE project_path = ?
            """, (duration, now, project_path))

        return True

    def get_session(self, session_id: str) -> Optional[Session]:
        """
        Get session by ID

        Args:
            session_id: Session identifier

        Returns:
            Session object or None if not found
        """
        row = self.db.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
            fetch_one=True
        )

        if row:
            return Session(**dict(row))
        return None

    def list_sessions(
        self,
        status: str = 'all',
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        List sessions

        Args:
            status: Filter by status (all, active, completed, etc.)
            limit: Maximum results

        Returns:
            List of session dictionaries
        """
        if status == 'all':
            rows = self.db.execute("""
                SELECT session_id, target, project_path, start_time, end_time,
                       status, total_commands, total_waits, last_stage
                FROM sessions ORDER BY start_time DESC LIMIT ?
            """, (limit,), fetch_all=True)
        else:
            rows = self.db.execute("""
                SELECT session_id, target, project_path, start_time, end_time,
                       status, total_commands, total_waits, last_stage
                FROM sessions WHERE status = ? ORDER BY start_time DESC LIMIT ?
            """, (status, limit), fetch_all=True)

        return [dict(row) for row in rows]

    def resolve_active_session_id(self, target: str) -> Optional[str]:
        """
        Find active session for given target

        Args:
            target: Target identifier (e.g., "session:window.pane")

        Returns:
            Session ID or None if no active session
        """
        target = (target or "").strip()
        if not target:
            return None

        row = self.db.execute("""
            SELECT session_id
            FROM sessions
            WHERE target = ? AND status = 'active'
            ORDER BY start_time DESC
            LIMIT 1
        """, (target,), fetch_one=True)

        if row:
            return row["session_id"]
        return None

    def update_session_activity(
        self,
        session_id: str,
        stage: Optional[str] = None,
        role: Optional[str] = None,
        command_sent: bool = False,
        wait: bool = False
    ):
        """
        Update session activity counters

        Args:
            session_id: Session to update
            stage: Current stage
            role: Current role
            command_sent: Whether a command was sent
            wait: Whether a wait occurred
        """
        updates = []
        params = []

        if stage:
            updates.append("last_stage = ?")
            params.append(stage)
        if role:
            updates.append("last_role = ?")
            params.append(role)
        if command_sent:
            updates.append("total_commands = total_commands + 1")
        if wait:
            updates.append("total_waits = total_waits + 1")

        if updates:
            params.append(session_id)
            self.db.execute(f"""
                UPDATE sessions SET {', '.join(updates)}
                WHERE session_id = ?
            """, params)
