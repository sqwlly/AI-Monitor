#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pytest Configuration and Fixtures
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def temp_db():
    """Temporary database fixture for testing"""
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"

    yield db_path

    # Cleanup
    shutil.rmtree(temp_dir)


@pytest.fixture
def temp_dir():
    """Temporary directory fixture"""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_session(temp_db):
    """Sample session fixture"""
    from memory.database import Database
    from memory.session_manager import SessionManager

    db = Database(temp_db)
    manager = SessionManager(db)

    session_id = manager.start_session("test:pane", "/tmp/test")

    return {
        "session_id": session_id,
        "manager": manager,
        "db": db
    }


@pytest.fixture
def sample_project(temp_db):
    """Sample project data fixture"""
    from memory.database import Database
    from memory.session_manager import SessionManager
    from memory.decision_recorder import DecisionRecorder

    db = Database(temp_db)
    session_mgr = SessionManager(db)
    decision_mgr = DecisionRecorder(db)

    # Create session
    session_id = session_mgr.start_session("test:pane", "/tmp/test")

    # Add some decisions
    decision_mgr.record_decision(
        session_id, "coding", "monitor",
        "npm install", "success"
    )
    decision_mgr.record_decision(
        session_id, "testing", "tester",
        "WAIT", "success"
    )

    return {
        "session_id": session_id,
        "session_mgr": session_mgr,
        "decision_mgr": decision_mgr,
        "db": db
    }
