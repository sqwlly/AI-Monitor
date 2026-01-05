#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Error Tracker
Tracks error patterns and fix outcomes
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


class ErrorTracker:
    """
    Error pattern tracking and fix suggestion manager

    Responsibilities:
    1. Record error occurrences with signatures
    2. Track fix attempts and outcomes
    3. Suggest successful fixes based on history
    """

    def __init__(self, db: Database = None):
        """
        Initialize error tracker

        Args:
            db: Database instance (creates default if None)
        """
        self.db = db or Database()

    def record_error(
        self,
        error_signature: str,
        error_preview: str,
        project_path: Optional[str] = None
    ):
        """
        Record an error occurrence

        Args:
            error_signature: Unique error signature (e.g., first line of traceback)
            error_preview: Preview of error message
            project_path: Project where error occurred
        """
        now = int(time.time())
        pattern_hash = hashlib.sha256(error_signature.encode()).hexdigest()[:16]

        # Check if pattern exists
        row = self.db.execute(
            "SELECT occurrences, successful_fixes, failed_fixes FROM error_patterns WHERE pattern_hash = ?",
            (pattern_hash,),
            fetch_one=True
        )

        if row:
            # Update existing
            self.db.execute("""
                UPDATE error_patterns
                SET occurrences = occurrences + 1,
                    last_seen = ?,
                    error_preview = ?
                WHERE pattern_hash = ?
            """, (now, error_preview, pattern_hash))
        else:
            # Insert new
            self.db.execute("""
                INSERT INTO error_patterns
                (pattern_hash, error_signature, error_preview, first_seen, last_seen, project_path)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (pattern_hash, error_signature, error_preview, now, now, project_path))

    def record_fix_outcome(
        self,
        error_signature: str,
        command: str,
        success: bool
    ):
        """
        Record outcome of a fix attempt

        Args:
            error_signature: Error that was fixed
            command: Command that was tried
            success: Whether the fix worked
        """
        pattern_hash = hashlib.sha256(error_signature.encode()).hexdigest()[:16]
        now = int(time.time())

        # Get current fix tracking
        row = self.db.execute(
            "SELECT successful_fixes, failed_fixes FROM error_patterns WHERE pattern_hash = ?",
            (pattern_hash,),
            fetch_one=True
        )

        if not row:
            return

        # Parse existing fix lists
        successful = json.loads(row['successful_fixes'] or '[]')
        failed = json.loads(row['failed_fixes'] or '[]')

        # Add new outcome
        if success:
            if command not in successful:
                successful.append(command)
            # Remove from failed if present
            if command in failed:
                failed.remove(command)
        else:
            if command not in failed:
                failed.append(command)

        # Update
        self.db.execute("""
            UPDATE error_patterns
            SET successful_fixes = ?,
                failed_fixes = ?,
                last_seen = ?
            WHERE pattern_hash = ?
        """, (json.dumps(successful), json.dumps(failed), now, pattern_hash))

    def get_fix_suggestions(
        self,
        error_signature: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Get fix suggestions for an error

        Args:
            error_signature: Error to find fixes for
            limit: Maximum suggestions

        Returns:
            List of suggested fixes with success counts
        """
        pattern_hash = hashlib.sha256(error_signature.encode()).hexdigest()[:16]

        row = self.db.execute(
            "SELECT successful_fixes FROM error_patterns WHERE pattern_hash = ?",
            (pattern_hash,),
            fetch_one=True
        )

        if not row or not row['successful_fixes']:
            return []

        successful = json.loads(row['successful_fixes'])

        # Get occurrence counts for each successful fix
        suggestions = []
        for fix in successful[:limit]:
            suggestions.append({
                'command': fix,
                'source': 'historical_success'
            })

        return suggestions

    def get_common_errors(
        self,
        project_path: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get most common errors

        Args:
            project_path: Filter by project
            limit: Maximum results

        Returns:
            List of error patterns with occurrence counts
        """
        if project_path:
            rows = self.db.execute("""
                SELECT error_signature, error_preview, occurrences,
                       first_seen, last_seen, successful_fixes
                FROM error_patterns
                WHERE project_path = ?
                ORDER BY occurrences DESC
                LIMIT ?
            """, (project_path, limit), fetch_all=True)
        else:
            rows = self.db.execute("""
                SELECT error_signature, error_preview, occurrences,
                       first_seen, last_seen, successful_fixes
                FROM error_patterns
                ORDER BY occurrences DESC
                LIMIT ?
            """, (limit,), fetch_all=True)

        results = []
        for row in rows:
            results.append({
                'signature': row['error_signature'],
                'preview': row['error_preview'],
                'occurrences': row['occurrences'],
                'first_seen': row['first_seen'],
                'last_seen': row['last_seen'],
                'has_fixes': bool(row['successful_fixes']),
            })

        return results
