#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Statistics Module
Provides statistics and export functionality
"""

import csv
import io
import json
import time
from typing import Dict, List, Optional

# Python 3.6 compatibility
try:
    from typing import Any
except ImportError:
    pass

from .database import Database


class Statistics:
    """
    Statistics and export manager

    Responsibilities:
    1. Calculate session and project statistics
    2. Export session data in various formats
    3. Clean old data
    """

    def __init__(self, db: Database = None):
        """
        Initialize statistics manager

        Args:
            db: Database instance (creates default if None)
        """
        self.db = db or Database()

    def get_stats(self, project_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Get statistics for a project or overall

        Args:
            project_path: Filter by project (None for all)

        Returns:
            Statistics dictionary
        """
        if project_path:
            return self._get_project_stats(project_path)
        else:
            return self._get_overall_stats()

    def _get_project_stats(self, project_path: str) -> Dict[str, Any]:
        """Get statistics for a specific project"""
        # Get project progress
        row = self.db.execute("""
            SELECT total_sessions, total_commands, total_time_s
            FROM project_progress
            WHERE project_path = ?
        """, (project_path,), fetch_one=True)

        if not row:
            return {'error': 'Project not found'}

        # Get session counts by status
        status_row = self.db.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error
            FROM sessions
            WHERE project_path = ?
        """, (project_path,), fetch_one=True)

        # Get recent activity
        recent_row = self.db.execute("""
            SELECT MAX(end_time) as last_activity
            FROM sessions
            WHERE project_path = ? AND end_time IS NOT NULL
        """, (project_path,), fetch_one=True)

        return {
            'project_path': project_path,
            'total_sessions': row['total_sessions'] or 0,
            'total_commands': row['total_commands'] or 0,
            'total_time_s': row['total_time_s'] or 0,
            'total_time_hours': round((row['total_time_s'] or 0) / 3600, 2),
            'active_sessions': status_row['active'] or 0,
            'completed_sessions': status_row['completed'] or 0,
            'error_sessions': status_row['error'] or 0,
            'last_activity': recent_row['last_activity'] if recent_row else None,
        }

    def _get_overall_stats(self) -> Dict[str, Any]:
        """Get overall statistics"""
        # Session stats
        session_row = self.db.execute("""
            SELECT
                COUNT(*) as total_sessions,
                SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_sessions,
                SUM(total_commands) as total_commands,
                SUM(total_waits) as total_waits
            FROM sessions
        """, fetch_one=True)

        # Project stats
        project_row = self.db.execute("""
            SELECT
                COUNT(*) as total_projects,
                SUM(total_sessions) as all_sessions,
                SUM(total_commands) as all_commands
            FROM project_progress
        """, fetch_one=True)

        # Recent activity (last 24 hours)
        cutoff = int(time.time()) - 86400
        recent_row = self.db.execute("""
            SELECT COUNT(*) as recent_sessions
            FROM sessions
            WHERE start_time > ?
        """, (cutoff,), fetch_one=True)

        return {
            'total_sessions': session_row['total_sessions'] or 0,
            'active_sessions': session_row['active_sessions'] or 0,
            'total_commands': session_row['total_commands'] or 0,
            'total_waits': session_row['total_waits'] or 0,
            'total_projects': project_row['total_projects'] or 0,
            'recent_sessions_24h': recent_row['recent_sessions'] or 0,
        }

    def export_session(
        self,
        session_id: str,
        format: str = 'json'
    ) -> str:
        """
        Export session data

        Args:
            session_id: Session to export
            format: Export format (json or csv)

        Returns:
            Exported data as string
        """
        # Get session info
        session = self.db.execute("""
            SELECT * FROM sessions WHERE session_id = ?
        """, (session_id,), fetch_one=True)

        if not session:
            return f"Error: Session {session_id} not found"

        # Get decisions
        decisions = self.db.execute("""
            SELECT timestamp, stage, role, input_preview, output, outcome, latency_ms
            FROM decisions
            WHERE session_id = ?
            ORDER BY timestamp ASC
        """, (session_id,), fetch_all=True)

        # Get stage timeline
        stages = self.db.execute("""
            SELECT stage, entered_at, exited_at, duration_s, commands_sent, waits, role_used
            FROM stage_timeline
            WHERE session_id = ?
            ORDER BY entered_at ASC
        """, (session_id,), fetch_all=True)

        if format == 'json':
            return self._export_json(session, decisions, stages)
        elif format == 'csv':
            return self._export_csv(session, decisions, stages)
        else:
            return f"Error: Unsupported format {format}"

    def _export_json(self, session, decisions, stages) -> str:
        """Export as JSON"""
        data = {
            'session': dict(session),
            'decisions': [dict(d) for d in decisions],
            'stages': [dict(s) for s in stages],
        }
        return json.dumps(data, indent=2, default=str)

    def _export_csv(self, session, decisions, stages) -> str:
        """Export as CSV (focus on decisions)"""
        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow([
            'Session', 'Target', 'Project', 'Start', 'End', 'Status'
        ])
        writer.writerow([
            session['session_id'],
            session['target'],
            session['project_path'] or '',
            session['start_time'],
            session['end_time'] or '',
            session['status']
        ])

        # Decisions
        writer.writerow([])
        writer.writerow(['Timestamp', 'Stage', 'Role', 'Output', 'Outcome', 'Latency_ms'])
        for d in decisions:
            writer.writerow([
                d['timestamp'],
                d['stage'] or '',
                d['role'] or '',
                d['output'][:50],
                d['outcome'],
                d['latency_ms'] or ''
            ])

        return output.getvalue()

    def clean(self, days: int = 30):
        """
        Clean old data

        Args:
            days: Delete data older than this many days
        """
        cutoff = int(time.time()) - (days * 86400)

        # Delete old sessions (cascade will handle related data)
        self.db.execute("""
            DELETE FROM sessions
            WHERE end_time < ? AND status != 'active'
        """, (cutoff,))

        # Delete old error patterns
        self.db.execute("""
            DELETE FROM error_patterns
            WHERE last_seen < ?
        """, (cutoff,))

        # Vacuum to reclaim space
        self.db.script_exec("VACUUM")

        return {"deleted_older_than": cutoff, "days": days}
