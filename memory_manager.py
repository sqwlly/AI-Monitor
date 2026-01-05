#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Memory Manager (Facade Mode)
Task memory management system - facade for modular subsystem

Usage:
    python3 memory_manager.py start-session <target> [project_path]
    python3 memory_manager.py end-session <session_id> <status> [summary]
    python3 memory_manager.py record <session_id> <stage> <role> <output> <outcome>
    python3 memory_manager.py resume <project_path>
    python3 memory_manager.py stats [project_path]
    python3 memory_manager.py export <session_id> [--format json|csv]
    python3 memory_manager.py list [--status active|completed|all]
    python3 memory_manager.py clean [--days N]
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Handle both direct execution and module import
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    from memory.database import Database, DB_PATH, DEFAULT_DB_PATH
    from memory.session_manager import SessionManager, Session
    from memory.decision_recorder import DecisionRecorder, Decision
    from memory.error_tracker import ErrorTracker
    from memory.context_recovery import ContextRecovery, ResumeContext
    from memory.statistics import Statistics
else:
    # Try relative import first, fall back to absolute
    try:
        from .database import Database, DB_PATH, DEFAULT_DB_PATH
        from .session_manager import SessionManager, Session
        from .decision_recorder import DecisionRecorder, Decision
        from .error_tracker import ErrorTracker
        from .context_recovery import ContextRecovery, ResumeContext
        from .statistics import Statistics
    except ImportError:
        from memory.database import Database, DB_PATH, DEFAULT_DB_PATH
        from memory.session_manager import SessionManager, Session
        from memory.decision_recorder import DecisionRecorder, Decision
        from memory.error_tracker import ErrorTracker
        from memory.context_recovery import ContextRecovery, ResumeContext
        from memory.statistics import Statistics


class MemoryManager:
    """
    Memory Manager - Facade for memory subsystem

    Delegates to specialized modules:
    - SessionManager: Session lifecycle
    - DecisionRecorder: Decision tracking
    - ErrorTracker: Error pattern tracking
    - ContextRecovery: Resume context
    - Statistics: Stats and export
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize memory manager with subsystems

        Args:
            db_path: Path to database (uses default if None)
        """
        # Initialize database (shared by all subsystems)
        self.db = Database(db_path or DB_PATH)

        # Initialize subsystems
        self.session = SessionManager(self.db)
        self.decision = DecisionRecorder(self.db)
        self.error = ErrorTracker(self.db)
        self.context = ContextRecovery(self.db)
        self.stats = Statistics(self.db)

    # ==================== Session Management (delegated) ====================

    def start_session(self, target: str, project_path: Optional[str] = None) -> str:
        """Start a new session, returns session_id"""
        return self.session.start_session(target, project_path)

    def end_session(self, session_id: str, status: str, summary: Optional[str] = None):
        """End a session"""
        return self.session.end_session(session_id, status, summary)

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get session information"""
        return self.session.get_session(session_id)

    def list_sessions(self, status: str = 'all', limit: int = 20) -> List[Dict]:
        """List sessions"""
        return self.session.list_sessions(status, limit)

    def resolve_active_session_id(self, target: str) -> Optional[str]:
        """Resolve active session ID for a target"""
        return self.session.resolve_active_session_id(target)

    # ==================== Decision Recording (delegated) ====================

    def record_decision(
        self,
        session_id: str,
        stage: Optional[str],
        role: Optional[str],
        output: str,
        outcome: str,
        input_text: Optional[str] = None,
        latency_ms: Optional[int] = None
    ):
        """Record a decision"""
        return self.decision.record_decision(
            session_id, stage, role, output, outcome, input_text, latency_ms
        )

    def get_recent_decisions(self, session_id: str, limit: int = 10) -> List[Dict]:
        """Get recent decisions for a session"""
        return self.decision.get_recent_decisions(session_id, limit)

    # ==================== Error Tracking (delegated) ====================

    def record_error(
        self,
        error_signature: str,
        error_preview: str,
        project_path: Optional[str] = None
    ):
        """Record an error occurrence"""
        self.error.record_error(error_signature, error_preview, project_path)

    def record_fix_outcome(self, error_signature: str, command: str, success: bool):
        """Record outcome of a fix attempt"""
        self.error.record_fix_outcome(error_signature, command, success)

    def get_fix_suggestions(self, error_signature: str) -> List[Dict]:
        """Get fix suggestions for an error"""
        return self.error.get_fix_suggestions(error_signature)

    # ==================== Context Recovery (delegated) ====================

    def get_resume_context(self, project_path: str) -> Optional[Dict]:
        """Get context for resuming work on a project"""
        return self.context.get_resume_context(project_path)

    # ==================== Statistics (delegated) ====================

    def get_stats(self, project_path: Optional[str] = None) -> Dict:
        """Get statistics"""
        return self.stats.get_stats(project_path)

    def export_session(self, session_id: str, format: str = 'json') -> str:
        """Export session data"""
        return self.stats.export_session(session_id, format)

    def clean(self, days: int = 30):
        """Clean old data"""
        return self.stats.clean(days)


# ==================== Legacy Compatibility ====================

# Re-export classes for backward compatibility
__all__ = [
    'MemoryManager',
    'Session',
    'Decision',
    'ResumeContext',
    'DB_PATH',
    'DEFAULT_DB_PATH',
]


# ==================== CLI Interface ====================

def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(description="Memory Manager")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # start-session
    start_parser = subparsers.add_parser("start-session", help="Start a new session")
    start_parser.add_argument("target", help="Target (e.g., session:window.pane)")
    start_parser.add_argument("project_path", nargs="?", help="Project root path")

    # end-session
    end_parser = subparsers.add_parser("end-session", help="End a session")
    end_parser.add_argument("session_id", help="Session ID")
    end_parser.add_argument("status", help="Final status")
    end_parser.add_argument("summary", nargs="?", help="Optional summary")

    # record
    record_parser = subparsers.add_parser("record", help="Record a decision")
    record_parser.add_argument("session_id", help="Session ID")
    record_parser.add_argument("stage", help="Current stage")
    record_parser.add_argument("role", help="AI role")
    record_parser.add_argument("output", help="AI output")
    record_parser.add_argument("outcome", help="Result outcome")

    # resume
    resume_parser = subparsers.add_parser("resume", help="Get resume context")
    resume_parser.add_argument("project_path", help="Project path")

    # stats
    stats_parser = subparsers.add_parser("stats", help="Show statistics")
    stats_parser.add_argument("project_path", nargs="?", help="Project path")

    # export
    export_parser = subparsers.add_parser("export", help="Export session")
    export_parser.add_argument("session_id", help="Session ID")
    export_parser.add_argument("--format", default="json", choices=["json", "csv"])

    # list
    list_parser = subparsers.add_parser("list", help="List sessions")
    list_parser.add_argument("--status", default="all", help="Filter by status")
    list_parser.add_argument("--limit", type=int, default=20, help="Max results")

    # clean
    clean_parser = subparsers.add_parser("clean", help="Clean old data")
    clean_parser.add_argument("--days", type=int, default=30, help="Delete data older than N days")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Initialize manager
    manager = MemoryManager()

    # Execute command
    if args.command == "start-session":
        session_id = manager.start_session(args.target, args.project_path)
        print(session_id)

    elif args.command == "end-session":
        manager.end_session(args.session_id, args.status, args.summary)
        print(f"Session {args.session_id} ended with status {args.status}")

    elif args.command == "record":
        manager.record_decision(
            args.session_id, args.stage, args.role, args.output, args.outcome
        )
        print("Decision recorded")

    elif args.command == "resume":
        context = manager.get_resume_context(args.project_path)
        if context:
            print(json.dumps(context, indent=2, default=str))
        else:
            print("No resume context found")

    elif args.command == "stats":
        stats = manager.get_stats(args.project_path)
        print(json.dumps(stats, indent=2, default=str))

    elif args.command == "export":
        data = manager.export_session(args.session_id, args.format)
        print(data)

    elif args.command == "list":
        sessions = manager.list_sessions(args.status, args.limit)
        for s in sessions:
            print(f"{s['session_id']}: {s['target']} - {s['status']}")

    elif args.command == "clean":
        result = manager.clean(args.days)
        print(f"Cleaned data older than {args.days} days")

    return 0


if __name__ == "__main__":
    sys.exit(main())
