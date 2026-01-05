#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Database Module
Handles database initialization, schema management, and connections
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


# Default paths
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"

# Environment variable override
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


class Database:
    """
    Database manager for memory subsystem

    Handles:
    1. Database initialization and schema management
    2. Connection management with context managers
    3. Foreign key enforcement
    """

    def __init__(self, db_path: Path = None):
        """
        Initialize database manager

        Args:
            db_path: Path to database file (uses default if None)
        """
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    def _ensure_db(self):
        """Ensure database directory and file exist"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.db_path.exists():
            self._init_db()

    def _init_db(self):
        """Initialize database with schema"""
        schema = self._get_schema()
        with self.get_connection() as conn:
            conn.executescript(schema)

    def _get_schema(self) -> str:
        """Get database schema"""
        return """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            target TEXT NOT NULL,
            project_path TEXT,
            start_time INTEGER NOT NULL,
            end_time INTEGER,
            status TEXT DEFAULT 'active',
            summary TEXT,
            total_commands INTEGER DEFAULT 0,
            total_waits INTEGER DEFAULT 0,
            last_stage TEXT,
            last_role TEXT,
            created_at INTEGER DEFAULT (strftime('%s', 'now'))
        );

        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            stage TEXT,
            role TEXT,
            input_hash TEXT,
            input_preview TEXT,
            output TEXT NOT NULL,
            outcome TEXT,
            latency_ms INTEGER,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS stage_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            entered_at INTEGER NOT NULL,
            exited_at INTEGER,
            duration_s INTEGER,
            commands_sent INTEGER DEFAULT 0,
            waits INTEGER DEFAULT 0,
            role_used TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS error_patterns (
            pattern_hash TEXT PRIMARY KEY,
            error_signature TEXT NOT NULL,
            error_preview TEXT,
            occurrences INTEGER DEFAULT 1,
            first_seen INTEGER,
            last_seen INTEGER,
            successful_fixes TEXT,
            failed_fixes TEXT,
            project_path TEXT
        );

        CREATE TABLE IF NOT EXISTS project_progress (
            project_path TEXT PRIMARY KEY,
            last_session_id TEXT,
            total_sessions INTEGER DEFAULT 0,
            total_commands INTEGER DEFAULT 0,
            total_time_s INTEGER DEFAULT 0,
            stage_distribution TEXT,
            milestones TEXT,
            learned_patterns TEXT,
            last_updated INTEGER,
            FOREIGN KEY (last_session_id) REFERENCES sessions(session_id)
        );

        CREATE INDEX IF NOT EXISTS idx_decisions_session ON decisions(session_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_path);
        CREATE INDEX IF NOT EXISTS idx_stage_timeline_session ON stage_timeline(session_id);
        CREATE INDEX IF NOT EXISTS idx_error_patterns_hash ON error_patterns(pattern_hash);
        """

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Get database connection with proper error handling

        Yields:
            SQLite connection with row factory and foreign keys enabled
        """
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute(self, sql: str, params=(), fetch_one=False, fetch_all=False):
        """
        Convenience method for executing SQL

        Args:
            sql: SQL statement
            params: Query parameters
            fetch_one: Return first row if True
            fetch_all: Return all rows if True

        Returns:
            Query result or None
        """
        with self.get_connection() as conn:
            cursor = conn.execute(sql, params)
            if fetch_one:
                return cursor.fetchone()
            elif fetch_all:
                return cursor.fetchall()
            return None

    def script_exec(self, script: str):
        """Execute SQL script"""
        with self.get_connection() as conn:
            conn.executescript(script)
