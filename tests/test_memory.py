#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Memory Subsystem Unit Tests
Tests for session management, decision recording, and statistics
"""

import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import time

from memory.database import Database
from memory.session_manager import SessionManager, Session
from memory.decision_recorder import DecisionRecorder
from memory.error_tracker import ErrorTracker
from memory.context_recovery import ContextRecovery
from memory.statistics import Statistics
from memory_manager import MemoryManager


class TestDatabase:
    """Database module tests"""

    def test_database_initialization(self, temp_db):
        """Test database creates and initializes correctly"""
        db = Database(temp_db)
        assert temp_db.exists()

        # Check tables exist
        with db.get_connection() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t['name'] for t in tables]

            assert 'sessions' in table_names
            assert 'decisions' in table_names
            assert 'stage_timeline' in table_names
            assert 'error_patterns' in table_names
            assert 'project_progress' in table_names

    def test_connection_context(self, temp_db):
        """Test connection context manager"""
        db = Database(temp_db)

        with db.get_connection() as conn:
            result = conn.execute("SELECT 1").fetchone()
            assert result[0] == 1


class TestSessionManager:
    """Session manager tests"""

    def test_start_session(self, sample_session):
        """Test starting a new session"""
        session_id = sample_session['session_id']
        assert session_id is not None
        assert len(session_id) == 8

    def test_get_session(self, sample_session):
        """Test retrieving session information"""
        manager = sample_session['manager']
        session_id = sample_session['session_id']

        session = manager.get_session(session_id)
        assert session is not None
        assert session.session_id == session_id
        assert session.status == 'active'
        assert session.target == "test:pane"

    def test_end_session(self, sample_session):
        """Test ending a session"""
        manager = sample_session['manager']
        session_id = sample_session['session_id']

        result = manager.end_session(session_id, "completed", "Test done")
        assert result is True

        session = manager.get_session(session_id)
        assert session.status == "completed"
        assert session.summary == "Test done"
        assert session.end_time is not None

    def test_list_sessions(self, sample_session):
        """Test listing sessions"""
        manager = sample_session['manager']

        sessions = manager.list_sessions(limit=10)
        assert len(sessions) >= 1
        assert any(s['session_id'] == sample_session['session_id'] for s in sessions)

    def test_resolve_active_session_id(self, sample_session):
        """Test resolving active session by target"""
        manager = sample_session['manager']

        session_id = manager.resolve_active_session_id("test:pane")
        assert session_id == sample_session['session_id']


class TestDecisionRecorder:
    """Decision recorder tests"""

    def test_record_decision(self, sample_project):
        """Test recording a decision"""
        recorder = sample_project['decision_mgr']
        session_id = sample_project['session_id']

        decision_id = recorder.record_decision(
            session_id, "coding", "senior-engineer",
            "git status", "success", "some input"
        )

        assert decision_id > 0

    def test_get_recent_decisions(self, sample_project):
        """Test retrieving recent decisions"""
        recorder = sample_project['decision_mgr']
        session_id = sample_project['session_id']

        decisions = recorder.get_recent_decisions(session_id, limit=5)
        assert len(decisions) >= 2  # We added 2 in fixture


class TestErrorTracker:
    """Error tracker tests"""

    def test_record_error(self, temp_db):
        """Test recording an error"""
        db = Database(temp_db)
        tracker = ErrorTracker(db)

        tracker.record_error(
            "ValueError: test error",
            "Traceback (most recent call last): ...",
            "/tmp/test"
        )

        # Check it was recorded
        row = db.execute(
            "SELECT * FROM error_patterns LIMIT 1",
            fetch_one=True
        )
        assert row is not None
        assert row['occurrences'] == 1

    def test_record_fix_outcome(self, temp_db):
        """Test recording fix outcome"""
        db = Database(temp_db)
        tracker = ErrorTracker(db)

        error_sig = "ValueError: test error"
        tracker.record_error(error_sig, "preview", "/tmp/test")
        tracker.record_fix_outcome(error_sig, "fix command", True)

        suggestions = tracker.get_fix_suggestions(error_sig)
        assert len(suggestions) >= 1
        assert suggestions[0]['command'] == "fix command"


class TestContextRecovery:
    """Context recovery tests"""

    def test_get_resume_context(self, temp_db):
        """Test getting resume context"""
        db = Database(temp_db)
        session_mgr = SessionManager(db)
        context_mgr = ContextRecovery(db)

        # Create a session with project
        session_id = session_mgr.start_session("test:pane", "/tmp/test")

        # Should return None for new project
        context = context_mgr.get_resume_context("/tmp/test")
        assert context is not None
        assert context['last_session_id'] == session_id


class TestStatistics:
    """Statistics tests"""

    def test_get_stats(self, temp_db):
        """Test getting statistics"""
        db = Database(temp_db)
        session_mgr = SessionManager(db)
        stats = Statistics(db)

        # Create some activity
        session_mgr.start_session("test:pane", "/tmp/test")
        session_mgr.start_session("test:pane2", "/tmp/test2")

        overall_stats = stats.get_stats()
        assert 'total_sessions' in overall_stats
        assert overall_stats['total_sessions'] >= 2


class TestMemoryManager:
    """Memory manager facade tests"""

    def test_manager_initialization(self, temp_db):
        """Test memory manager initializes all subsystems"""
        manager = MemoryManager(temp_db)

        assert manager.db is not None
        assert manager.session is not None
        assert manager.decision is not None
        assert manager.error is not None
        assert manager.context is not None
        assert manager.stats is not None

    def test_manager_delegation(self, temp_db):
        """Test manager properly delegates to subsystems"""
        manager = MemoryManager(temp_db)

        # Start session via manager
        session_id = manager.start_session("test:pane", "/tmp/test")
        assert session_id is not None

        # Record decision via manager
        manager.record_decision(
            session_id, "coding", "monitor",
            "ls -la", "success"
        )

        # Get stats via manager
        stats = manager.get_stats()
        assert 'total_sessions' in stats


@pytest.mark.integration
class TestIntegration:
    """Integration tests for memory subsystem"""

    def test_full_session_lifecycle(self, temp_db):
        """Test complete session lifecycle"""
        manager = MemoryManager(temp_db)

        # 1. Start session
        session_id = manager.start_session("mytarget:0.1", "/project")
        assert session_id is not None

        # 2. Record decisions
        manager.record_decision(session_id, "coding", "dev", "cmd1", "success")
        manager.record_decision(session_id, "testing", "qa", "WAIT", "success")

        # 3. Get recent decisions
        decisions = manager.get_recent_decisions(session_id)
        assert len(decisions) == 2

        # 4. End session
        manager.end_session(session_id, "completed", "All done")

        # 5. Verify
        session = manager.get_session(session_id)
        assert session.status == "completed"

    def test_project_tracking(self, temp_db):
        """Test project-level tracking"""
        manager = MemoryManager(temp_db)

        # Multiple sessions for same project
        s1 = manager.start_session("target:0.1", "/project")
        manager.end_session(s1, "completed")

        s2 = manager.start_session("target:0.1", "/project")
        manager.end_session(s2, "completed")

        # Check stats
        stats = manager.get_stats("/project")
        assert stats['total_sessions'] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
