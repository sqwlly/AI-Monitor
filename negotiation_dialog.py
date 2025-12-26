#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Negotiation Dialog
协商对话系统 - 与用户进行有意义的协商对话

功能：
1. 不确定性表达（置信度显示/多方案呈现/风险说明）
2. 确认请求（高风险操作/方向性决策/资源消耗）
3. 澄清请求（模糊意图/约束条件/优先级）
4. 反馈收集（满意度/改进建议/纠错反馈）

Usage:
    python3 negotiation_dialog.py create <dialog_type> <session_id> [--context <json>]
    python3 negotiation_dialog.py respond <dialog_id> <response>
    python3 negotiation_dialog.py status <dialog_id>
    python3 negotiation_dialog.py list <session_id>
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from compat_dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# Database path
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


class DialogType(Enum):
    """Type of dialog"""
    CONFIRMATION = "confirmation"        # Request confirmation
    CLARIFICATION = "clarification"      # Request clarification
    CHOICE = "choice"                    # Present options
    FEEDBACK = "feedback"                # Request feedback
    WARNING = "warning"                  # Display warning
    INFORMATION = "information"          # Provide information


class DialogPriority(Enum):
    """Dialog priority"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DialogStatus(Enum):
    """Dialog status"""
    PENDING = "pending"            # Waiting for response
    RESPONDED = "responded"        # User responded
    EXPIRED = "expired"            # Timed out
    CANCELLED = "cancelled"        # Cancelled by system


@dataclass
class DialogOption:
    """An option in a choice dialog"""
    key: str                       # Short key (e.g., 'a', '1', 'yes')
    label: str                     # Display label
    description: str = ""          # Optional description
    is_default: bool = False       # Is this the default option
    is_recommended: bool = False   # Is this recommended

    def to_dict(self) -> Dict:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "is_default": self.is_default,
            "is_recommended": self.is_recommended,
        }


@dataclass
class Dialog:
    """A dialog interaction"""
    dialog_id: str = ""
    session_id: str = ""
    dialog_type: DialogType = DialogType.INFORMATION
    priority: DialogPriority = DialogPriority.MEDIUM
    title: str = ""
    message: str = ""
    options: List[DialogOption] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    status: DialogStatus = DialogStatus.PENDING
    response: Optional[str] = None
    response_data: Dict[str, Any] = field(default_factory=dict)
    expires_at: Optional[int] = None
    created_at: int = 0
    responded_at: Optional[int] = None

    def to_dict(self) -> Dict:
        return {
            "dialog_id": self.dialog_id,
            "session_id": self.session_id,
            "dialog_type": self.dialog_type.value,
            "priority": self.priority.value,
            "title": self.title,
            "message": self.message,
            "options": [o.to_dict() for o in self.options],
            "context": self.context,
            "status": self.status.value,
            "response": self.response,
            "response_data": self.response_data,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "responded_at": self.responded_at,
        }

    def format_for_display(self) -> str:
        """Format dialog for terminal display"""
        lines = []

        # Header with priority indicator
        priority_markers = {
            DialogPriority.LOW: "ℹ️",
            DialogPriority.MEDIUM: "❓",
            DialogPriority.HIGH: "⚠️",
            DialogPriority.CRITICAL: "🚨",
        }
        marker = priority_markers.get(self.priority, "")

        lines.append(f"{marker} {self.title}")
        lines.append("-" * 40)
        lines.append(self.message)

        if self.options:
            lines.append("")
            lines.append("Options:")
            for opt in self.options:
                marker = "→" if opt.is_recommended else " "
                default = "(default)" if opt.is_default else ""
                lines.append(f"  {marker} [{opt.key}] {opt.label} {default}")
                if opt.description:
                    lines.append(f"       {opt.description}")

        return "\n".join(lines)


class NegotiationDialog:
    """Negotiation dialog system"""

    # Default timeout in seconds
    DEFAULT_TIMEOUT = 300  # 5 minutes

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    def _ensure_db(self):
        """Ensure database exists with proper schema"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS dialogs (
                    dialog_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    dialog_type TEXT NOT NULL,
                    priority TEXT DEFAULT 'medium',
                    title TEXT,
                    message TEXT NOT NULL,
                    options TEXT,
                    context TEXT,
                    status TEXT DEFAULT 'pending',
                    response TEXT,
                    response_data TEXT,
                    expires_at INTEGER,
                    created_at INTEGER NOT NULL,
                    responded_at INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_dialogs_session
                    ON dialogs(session_id);
                CREATE INDEX IF NOT EXISTS idx_dialogs_status
                    ON dialogs(status);
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

    def _row_to_dialog(self, row: sqlite3.Row) -> Dialog:
        """Convert database row to Dialog object"""
        options_data = json.loads(row["options"] or "[]")
        options = [
            DialogOption(
                key=o.get("key", ""),
                label=o.get("label", ""),
                description=o.get("description", ""),
                is_default=o.get("is_default", False),
                is_recommended=o.get("is_recommended", False),
            )
            for o in options_data
        ]

        return Dialog(
            dialog_id=row["dialog_id"],
            session_id=row["session_id"],
            dialog_type=DialogType(row["dialog_type"]),
            priority=DialogPriority(row["priority"] or "medium"),
            title=row["title"] or "",
            message=row["message"] or "",
            options=options,
            context=json.loads(row["context"] or "{}"),
            status=DialogStatus(row["status"] or "pending"),
            response=row["response"],
            response_data=json.loads(row["response_data"] or "{}"),
            expires_at=row["expires_at"],
            created_at=row["created_at"] or 0,
            responded_at=row["responded_at"],
        )

    # ==================== Dialog Creation ====================

    def create(
        self,
        session_id: str,
        dialog_type: DialogType,
        message: str,
        title: str = "",
        options: Optional[List[DialogOption]] = None,
        priority: DialogPriority = DialogPriority.MEDIUM,
        context: Optional[Dict[str, Any]] = None,
        timeout: int = DEFAULT_TIMEOUT
    ) -> Dialog:
        """Create a new dialog"""
        dialog = Dialog(
            dialog_id=str(uuid.uuid4())[:8],
            session_id=session_id,
            dialog_type=dialog_type,
            priority=priority,
            title=title or self._default_title(dialog_type),
            message=message,
            options=options or [],
            context=context or {},
            status=DialogStatus.PENDING,
            expires_at=int(time.time()) + timeout if timeout > 0 else None,
            created_at=int(time.time()),
        )

        self._save_dialog(dialog)
        return dialog

    def _default_title(self, dialog_type: DialogType) -> str:
        """Get default title for dialog type"""
        titles = {
            DialogType.CONFIRMATION: "Confirmation Required",
            DialogType.CLARIFICATION: "Clarification Needed",
            DialogType.CHOICE: "Please Choose",
            DialogType.FEEDBACK: "Feedback Request",
            DialogType.WARNING: "Warning",
            DialogType.INFORMATION: "Information",
        }
        return titles.get(dialog_type, "Dialog")

    def _save_dialog(self, dialog: Dialog):
        """Save dialog to database"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO dialogs (
                    dialog_id, session_id, dialog_type, priority,
                    title, message, options, context, status,
                    response, response_data, expires_at,
                    created_at, responded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                dialog.dialog_id,
                dialog.session_id,
                dialog.dialog_type.value,
                dialog.priority.value,
                dialog.title,
                dialog.message,
                json.dumps([o.to_dict() for o in dialog.options]),
                json.dumps(dialog.context),
                dialog.status.value,
                dialog.response,
                json.dumps(dialog.response_data),
                dialog.expires_at,
                dialog.created_at,
                dialog.responded_at,
            ))

    # ==================== Convenience Creators ====================

    def confirm(
        self,
        session_id: str,
        message: str,
        title: str = "Confirm Action",
        priority: DialogPriority = DialogPriority.MEDIUM,
        context: Optional[Dict[str, Any]] = None
    ) -> Dialog:
        """Create a confirmation dialog"""
        options = [
            DialogOption("y", "Yes", "Proceed with action", is_default=True),
            DialogOption("n", "No", "Cancel action"),
        ]
        return self.create(
            session_id=session_id,
            dialog_type=DialogType.CONFIRMATION,
            message=message,
            title=title,
            options=options,
            priority=priority,
            context=context,
        )

    def clarify(
        self,
        session_id: str,
        question: str,
        options: Optional[List[str]] = None,
        title: str = "Clarification Needed",
        context: Optional[Dict[str, Any]] = None
    ) -> Dialog:
        """Create a clarification dialog"""
        dialog_options = []
        if options:
            for i, opt in enumerate(options):
                dialog_options.append(
                    DialogOption(str(i + 1), opt)
                )
            dialog_options.append(
                DialogOption("o", "Other (specify)", "Provide custom answer")
            )

        return self.create(
            session_id=session_id,
            dialog_type=DialogType.CLARIFICATION,
            message=question,
            title=title,
            options=dialog_options,
            priority=DialogPriority.MEDIUM,
            context=context,
        )

    def choose(
        self,
        session_id: str,
        message: str,
        options: List[Tuple[str, str, bool]],  # (label, description, recommended)
        title: str = "Please Choose",
        priority: DialogPriority = DialogPriority.MEDIUM,
        context: Optional[Dict[str, Any]] = None
    ) -> Dialog:
        """Create a choice dialog"""
        dialog_options = []
        for i, (label, desc, recommended) in enumerate(options):
            dialog_options.append(
                DialogOption(
                    key=str(i + 1),
                    label=label,
                    description=desc,
                    is_recommended=recommended,
                    is_default=(i == 0),
                )
            )

        return self.create(
            session_id=session_id,
            dialog_type=DialogType.CHOICE,
            message=message,
            title=title,
            options=dialog_options,
            priority=priority,
            context=context,
        )

    def warn(
        self,
        session_id: str,
        message: str,
        severity: str = "medium",
        require_acknowledgment: bool = True,
        context: Optional[Dict[str, Any]] = None
    ) -> Dialog:
        """Create a warning dialog"""
        priority = {
            "low": DialogPriority.LOW,
            "medium": DialogPriority.MEDIUM,
            "high": DialogPriority.HIGH,
            "critical": DialogPriority.CRITICAL,
        }.get(severity, DialogPriority.MEDIUM)

        options = []
        if require_acknowledgment:
            options = [
                DialogOption("ok", "OK, understood"),
                DialogOption("stop", "Stop and review"),
            ]

        return self.create(
            session_id=session_id,
            dialog_type=DialogType.WARNING,
            message=message,
            title=f"Warning ({severity})",
            options=options,
            priority=priority,
            context=context,
        )

    def request_feedback(
        self,
        session_id: str,
        question: str,
        rating_scale: bool = False,
        title: str = "Feedback Request",
        context: Optional[Dict[str, Any]] = None
    ) -> Dialog:
        """Create a feedback dialog"""
        options = []
        if rating_scale:
            options = [
                DialogOption("1", "😞 Poor", "Not helpful"),
                DialogOption("2", "😐 Fair", "Somewhat helpful"),
                DialogOption("3", "🙂 Good", "Helpful"),
                DialogOption("4", "😊 Great", "Very helpful"),
                DialogOption("5", "🌟 Excellent", "Extremely helpful"),
            ]

        return self.create(
            session_id=session_id,
            dialog_type=DialogType.FEEDBACK,
            message=question,
            title=title,
            options=options,
            priority=DialogPriority.LOW,
            context=context,
        )

    def express_uncertainty(
        self,
        session_id: str,
        topic: str,
        confidence: float,
        alternatives: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None
    ) -> Dialog:
        """Express uncertainty with alternatives"""
        message_parts = [
            f"I'm not entirely certain about: {topic}",
            f"Confidence level: {confidence:.0%}",
            "",
            "Here are the alternatives I'm considering:",
        ]

        options = []
        for i, alt in enumerate(alternatives):
            options.append(
                DialogOption(
                    key=str(i + 1),
                    label=alt.get("name", f"Option {i + 1}"),
                    description=alt.get("description", ""),
                    is_recommended=alt.get("recommended", False),
                )
            )
            message_parts.append(
                f"  {i + 1}. {alt.get('name')}: {alt.get('description', '')}"
            )

        options.append(
            DialogOption("h", "Help me decide", "Provide more information")
        )

        return self.create(
            session_id=session_id,
            dialog_type=DialogType.CHOICE,
            message="\n".join(message_parts),
            title="Uncertainty - Need Guidance",
            options=options,
            priority=DialogPriority.MEDIUM,
            context={**(context or {}), "confidence": confidence, "alternatives": alternatives},
        )

    # ==================== Response Handling ====================

    def respond(
        self,
        dialog_id: str,
        response: str,
        response_data: Optional[Dict[str, Any]] = None
    ) -> Dialog:
        """Record a response to a dialog"""
        dialog = self.get(dialog_id)
        if not dialog:
            raise ValueError(f"Dialog {dialog_id} not found")

        if dialog.status != DialogStatus.PENDING:
            raise ValueError(f"Dialog {dialog_id} is not pending (status: {dialog.status.value})")

        # Check expiration
        if dialog.expires_at and time.time() > dialog.expires_at:
            dialog.status = DialogStatus.EXPIRED
            self._save_dialog(dialog)
            raise ValueError(f"Dialog {dialog_id} has expired")

        # Validate response against options if applicable
        if dialog.options:
            valid_keys = {o.key.lower() for o in dialog.options}
            if response.lower() not in valid_keys:
                # Allow free-form if "other" option exists
                if not any(o.key.lower() == "o" for o in dialog.options):
                    raise ValueError(f"Invalid response. Valid options: {valid_keys}")

        dialog.response = response
        dialog.response_data = response_data or {}
        dialog.status = DialogStatus.RESPONDED
        dialog.responded_at = int(time.time())

        self._save_dialog(dialog)
        return dialog

    def cancel(self, dialog_id: str, reason: str = "") -> Dialog:
        """Cancel a pending dialog"""
        dialog = self.get(dialog_id)
        if not dialog:
            raise ValueError(f"Dialog {dialog_id} not found")

        dialog.status = DialogStatus.CANCELLED
        dialog.response_data = {"cancel_reason": reason}

        self._save_dialog(dialog)
        return dialog

    # ==================== Query Methods ====================

    def get(self, dialog_id: str) -> Optional[Dialog]:
        """Get a dialog by ID"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM dialogs WHERE dialog_id = ?",
                (dialog_id,)
            ).fetchone()

            if row:
                return self._row_to_dialog(row)
            return None

    def get_pending(self, session_id: str) -> List[Dialog]:
        """Get all pending dialogs for a session"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM dialogs
                WHERE session_id = ? AND status = 'pending'
                ORDER BY created_at DESC
            """, (session_id,)).fetchall()

            dialogs = [self._row_to_dialog(row) for row in rows]

            # Check for expired dialogs
            now = int(time.time())
            active = []
            for dialog in dialogs:
                if dialog.expires_at and now > dialog.expires_at:
                    dialog.status = DialogStatus.EXPIRED
                    self._save_dialog(dialog)
                else:
                    active.append(dialog)

            return active

    def list_dialogs(
        self,
        session_id: str,
        status: Optional[DialogStatus] = None,
        limit: int = 50
    ) -> List[Dialog]:
        """List dialogs for a session"""
        with self._get_conn() as conn:
            if status:
                rows = conn.execute("""
                    SELECT * FROM dialogs
                    WHERE session_id = ? AND status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (session_id, status.value, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM dialogs
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (session_id, limit)).fetchall()

            return [self._row_to_dialog(row) for row in rows]

    def get_response_stats(self, session_id: str) -> Dict[str, Any]:
        """Get response statistics for a session"""
        with self._get_conn() as conn:
            total = conn.execute("""
                SELECT COUNT(*) as count FROM dialogs WHERE session_id = ?
            """, (session_id,)).fetchone()["count"]

            by_status = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM dialogs WHERE session_id = ?
                GROUP BY status
            """, (session_id,)).fetchall()

            by_type = conn.execute("""
                SELECT dialog_type, COUNT(*) as count
                FROM dialogs WHERE session_id = ?
                GROUP BY dialog_type
            """, (session_id,)).fetchall()

            # Calculate response time
            avg_response = conn.execute("""
                SELECT AVG(responded_at - created_at) as avg_time
                FROM dialogs
                WHERE session_id = ? AND status = 'responded'
            """, (session_id,)).fetchone()["avg_time"]

            return {
                "total_dialogs": total,
                "by_status": {row["status"]: row["count"] for row in by_status},
                "by_type": {row["dialog_type"]: row["count"] for row in by_type},
                "average_response_time": round(avg_response, 1) if avg_response else None,
            }


# ==================== CLI Interface ====================

def main():
    parser = argparse.ArgumentParser(
        description="Claude Monitor Negotiation Dialog"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # create command
    create_parser = subparsers.add_parser("create", help="Create a dialog")
    create_parser.add_argument(
        "dialog_type",
        choices=[t.value for t in DialogType],
        help="Dialog type"
    )
    create_parser.add_argument("session_id", help="Session ID")
    create_parser.add_argument("--message", required=True, help="Dialog message")
    create_parser.add_argument("--title", help="Dialog title")
    create_parser.add_argument("--context", help="Context JSON")

    # respond command
    respond_parser = subparsers.add_parser("respond", help="Respond to dialog")
    respond_parser.add_argument("dialog_id", help="Dialog ID")
    respond_parser.add_argument("response", help="Response")

    # status command
    status_parser = subparsers.add_parser("status", help="Get dialog status")
    status_parser.add_argument("dialog_id", help="Dialog ID")

    # list command
    list_parser = subparsers.add_parser("list", help="List dialogs")
    list_parser.add_argument("session_id", help="Session ID")
    list_parser.add_argument("--status", choices=[s.value for s in DialogStatus])

    # pending command
    pending_parser = subparsers.add_parser("pending", help="Get pending dialogs")
    pending_parser.add_argument("session_id", help="Session ID")

    args = parser.parse_args()
    dialog_system = NegotiationDialog()

    if args.command == "create":
        context = json.loads(args.context) if args.context else {}
        dialog = dialog_system.create(
            session_id=args.session_id,
            dialog_type=DialogType(args.dialog_type),
            message=args.message,
            title=args.title or "",
            context=context,
        )
        print(json.dumps(dialog.to_dict(), indent=2))
        print("\n" + dialog.format_for_display())

    elif args.command == "respond":
        dialog = dialog_system.respond(args.dialog_id, args.response)
        print(json.dumps(dialog.to_dict(), indent=2))

    elif args.command == "status":
        dialog = dialog_system.get(args.dialog_id)
        if dialog:
            print(json.dumps(dialog.to_dict(), indent=2))
        else:
            print("Dialog not found")

    elif args.command == "list":
        status = DialogStatus(args.status) if args.status else None
        dialogs = dialog_system.list_dialogs(args.session_id, status)
        print(json.dumps([d.to_dict() for d in dialogs], indent=2))

    elif args.command == "pending":
        dialogs = dialog_system.get_pending(args.session_id)
        for d in dialogs:
            print(d.format_for_display())
            print()

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
