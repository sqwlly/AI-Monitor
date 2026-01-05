#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Smart Notifier
智能通知系统 - 智能过滤和优化通知

功能：
1. 通知智能过滤（重复合并/低优先级延迟/上下文聚合）
2. 通知时机优化（用户活跃度感知/关键节点/紧急程度）
3. 通知内容优化（简洁摘要/可操作建议/上下文链接）
4. 通知反馈学习（打开率/响应时间/有效性评估）

Usage:
    python3 smart_notifier.py send <session_id> <message> [--priority <level>]
    python3 smart_notifier.py queue <session_id>
    python3 smart_notifier.py flush <session_id>
    python3 smart_notifier.py stats [--session <session_id>]
"""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from compat_dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Database path
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


class NotificationPriority(Enum):
    """Notification priority levels"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class NotificationStatus(Enum):
    """Notification status"""
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


class NotificationChannel(Enum):
    """Notification delivery channel"""
    DESKTOP = "desktop"
    TERMINAL = "terminal"
    SOUND = "sound"
    LOG = "log"


@dataclass
class Notification:
    """A notification"""
    notification_id: str = ""
    session_id: str = ""
    title: str = ""
    message: str = ""
    priority: NotificationPriority = NotificationPriority.NORMAL
    status: NotificationStatus = NotificationStatus.QUEUED
    channel: NotificationChannel = NotificationChannel.DESKTOP
    category: str = ""
    actions: List[Dict[str, str]] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""              # For deduplication
    group_id: Optional[str] = None     # For grouping related notifications
    expires_at: Optional[int] = None
    created_at: int = 0
    sent_at: Optional[int] = None
    read_at: Optional[int] = None

    def to_dict(self) -> Dict:
        return {
            "notification_id": self.notification_id,
            "session_id": self.session_id,
            "title": self.title,
            "message": self.message,
            "priority": self.priority.value,
            "status": self.status.value,
            "channel": self.channel.value,
            "category": self.category,
            "actions": self.actions,
            "context": self.context,
            "fingerprint": self.fingerprint,
            "group_id": self.group_id,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "sent_at": self.sent_at,
            "read_at": self.read_at,
        }


@dataclass
class NotificationGroup:
    """A group of related notifications"""
    group_id: str
    session_id: str
    title: str
    notifications: List[Notification]
    priority: NotificationPriority
    created_at: int

    def to_summary(self) -> str:
        """Create summary message for group"""
        count = len(self.notifications)
        if count == 1:
            return self.notifications[0].message
        return f"{count} notifications: {self.title}"


class SmartNotifier:
    """Smart notification system"""

    # Deduplication window in seconds
    DEDUP_WINDOW = 60

    # Priority delays (seconds before sending)
    PRIORITY_DELAYS = {
        NotificationPriority.LOW: 30,
        NotificationPriority.NORMAL: 5,
        NotificationPriority.HIGH: 0,
        NotificationPriority.URGENT: 0,
    }

    # Maximum queued notifications before forcing flush
    MAX_QUEUE_SIZE = 10

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    def _ensure_db(self):
        """Ensure database exists with proper schema"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    title TEXT,
                    message TEXT NOT NULL,
                    priority TEXT DEFAULT 'normal',
                    status TEXT DEFAULT 'queued',
                    channel TEXT DEFAULT 'desktop',
                    category TEXT,
                    actions TEXT,
                    context TEXT,
                    fingerprint TEXT,
                    group_id TEXT,
                    expires_at INTEGER,
                    created_at INTEGER NOT NULL,
                    sent_at INTEGER,
                    read_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS notification_stats (
                    stat_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    date TEXT,
                    total_sent INTEGER DEFAULT 0,
                    total_read INTEGER DEFAULT 0,
                    total_dismissed INTEGER DEFAULT 0,
                    avg_response_time REAL,
                    by_priority TEXT,
                    by_category TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_notif_session
                    ON notifications(session_id);
                CREATE INDEX IF NOT EXISTS idx_notif_status
                    ON notifications(status);
                CREATE INDEX IF NOT EXISTS idx_notif_fingerprint
                    ON notifications(fingerprint);
                CREATE INDEX IF NOT EXISTS idx_notif_group
                    ON notifications(group_id);
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

    def _row_to_notification(self, row: sqlite3.Row) -> Notification:
        """Convert database row to Notification object"""
        return Notification(
            notification_id=row["notification_id"],
            session_id=row["session_id"],
            title=row["title"] or "",
            message=row["message"] or "",
            priority=NotificationPriority(row["priority"] or "normal"),
            status=NotificationStatus(row["status"] or "queued"),
            channel=NotificationChannel(row["channel"] or "desktop"),
            category=row["category"] or "",
            actions=json.loads(row["actions"] or "[]"),
            context=json.loads(row["context"] or "{}"),
            fingerprint=row["fingerprint"] or "",
            group_id=row["group_id"],
            expires_at=row["expires_at"],
            created_at=row["created_at"] or 0,
            sent_at=row["sent_at"],
            read_at=row["read_at"],
        )

    # ==================== Notification Creation ====================

    def notify(
        self,
        session_id: str,
        message: str,
        title: str = "",
        priority: NotificationPriority = NotificationPriority.NORMAL,
        category: str = "",
        actions: Optional[List[Dict[str, str]]] = None,
        context: Optional[Dict[str, Any]] = None,
        channel: NotificationChannel = NotificationChannel.DESKTOP,
        immediate: bool = False
    ) -> Notification:
        """Create and queue a notification"""
        # Generate fingerprint for deduplication
        fingerprint = self._generate_fingerprint(session_id, title, message, category)

        # Check for duplicates
        if self._is_duplicate(session_id, fingerprint):
            # Return the existing notification
            with self._get_conn() as conn:
                existing = conn.execute("""
                    SELECT * FROM notifications
                    WHERE session_id = ? AND fingerprint = ?
                      AND status IN ('queued', 'sent')
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (session_id, fingerprint)).fetchone()

                if existing:
                    return self._row_to_notification(existing)

        notification = Notification(
            notification_id=str(uuid.uuid4())[:8],
            session_id=session_id,
            title=title or self._generate_title(category, priority),
            message=message,
            priority=priority,
            status=NotificationStatus.QUEUED,
            channel=channel,
            category=category,
            actions=actions or [],
            context=context or {},
            fingerprint=fingerprint,
            expires_at=int(time.time()) + 3600,  # 1 hour expiry
            created_at=int(time.time()),
        )

        self._save_notification(notification)

        # Send immediately if urgent or requested
        if immediate or priority == NotificationPriority.URGENT:
            self._send_notification(notification)
        elif priority == NotificationPriority.HIGH:
            # High priority: short delay then send
            self._send_notification(notification)
        else:
            # Queue for batch sending
            self._check_queue_flush(session_id)

        return notification

    def _generate_fingerprint(
        self,
        session_id: str,
        title: str,
        message: str,
        category: str
    ) -> str:
        """Generate fingerprint for deduplication"""
        content = f"{session_id}:{category}:{title}:{message[:100]}"
        return hashlib.md5(content.encode()).hexdigest()[:12]

    def _generate_title(
        self,
        category: str,
        priority: NotificationPriority
    ) -> str:
        """Generate default title"""
        titles = {
            "error": "Error Detected",
            "warning": "Warning",
            "progress": "Progress Update",
            "completion": "Task Completed",
            "intervention": "Intervention",
            "info": "Information",
        }
        default = titles.get(category, "Claude Monitor")

        if priority == NotificationPriority.URGENT:
            return f"🚨 {default}"
        elif priority == NotificationPriority.HIGH:
            return f"⚠️ {default}"

        return default

    def _is_duplicate(self, session_id: str, fingerprint: str) -> bool:
        """Check if notification is a duplicate"""
        cutoff = int(time.time()) - self.DEDUP_WINDOW

        with self._get_conn() as conn:
            existing = conn.execute("""
                SELECT COUNT(*) as count FROM notifications
                WHERE session_id = ? AND fingerprint = ?
                  AND created_at > ?
                  AND status IN ('queued', 'sent', 'delivered')
            """, (session_id, fingerprint, cutoff)).fetchone()

            return existing["count"] > 0 if existing else False

    def _save_notification(self, notification: Notification):
        """Save notification to database"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO notifications (
                    notification_id, session_id, title, message,
                    priority, status, channel, category, actions,
                    context, fingerprint, group_id, expires_at,
                    created_at, sent_at, read_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                notification.notification_id,
                notification.session_id,
                notification.title,
                notification.message,
                notification.priority.value,
                notification.status.value,
                notification.channel.value,
                notification.category,
                json.dumps(notification.actions),
                json.dumps(notification.context),
                notification.fingerprint,
                notification.group_id,
                notification.expires_at,
                notification.created_at,
                notification.sent_at,
                notification.read_at,
            ))

    def _check_queue_flush(self, session_id: str):
        """Check if queue should be flushed"""
        with self._get_conn() as conn:
            queued = conn.execute("""
                SELECT COUNT(*) as count FROM notifications
                WHERE session_id = ? AND status = 'queued'
            """, (session_id,)).fetchone()

            if queued["count"] >= self.MAX_QUEUE_SIZE:
                self.flush(session_id)

    # ==================== Notification Sending ====================

    def _send_notification(self, notification: Notification):
        """Send a notification through the appropriate channel"""
        if notification.channel == NotificationChannel.DESKTOP:
            self._send_desktop(notification)
        elif notification.channel == NotificationChannel.TERMINAL:
            self._send_terminal(notification)
        elif notification.channel == NotificationChannel.SOUND:
            self._send_sound(notification)
        else:
            self._send_log(notification)

        notification.status = NotificationStatus.SENT
        notification.sent_at = int(time.time())
        self._save_notification(notification)

    def _send_desktop(self, notification: Notification):
        """Send desktop notification"""
        title = notification.title
        message = notification.message

        # Try different notification systems
        if sys.platform == "darwin":
            # macOS
            script = f'''
            display notification "{message}" with title "{title}"
            '''
            try:
                subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    timeout=5
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass

        elif sys.platform.startswith("linux"):
            # Linux - try notify-send
            try:
                urgency = {
                    NotificationPriority.LOW: "low",
                    NotificationPriority.NORMAL: "normal",
                    NotificationPriority.HIGH: "critical",
                    NotificationPriority.URGENT: "critical",
                }.get(notification.priority, "normal")

                subprocess.run(
                    ["notify-send", "-u", urgency, title, message],
                    capture_output=True,
                    timeout=5
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass

    def _send_terminal(self, notification: Notification):
        """Send terminal bell/message"""
        # Send bell character
        print("\a", end="", flush=True)

        # Also print message
        print(f"\n[{notification.title}] {notification.message}\n", flush=True)

    def _send_sound(self, notification: Notification):
        """Play notification sound"""
        if sys.platform == "darwin":
            try:
                subprocess.run(
                    ["afplay", "/System/Library/Sounds/Ping.aiff"],
                    capture_output=True,
                    timeout=5
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
        else:
            # Linux - try paplay
            try:
                subprocess.run(
                    ["paplay", "/usr/share/sounds/freedesktop/stereo/message.oga"],
                    capture_output=True,
                    timeout=5
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass

    def _send_log(self, notification: Notification):
        """Log notification"""
        log_path = self.db_path.parent / "notifications.log"
        with open(log_path, "a") as f:
            f.write(f"{notification.created_at}|{notification.priority.value}|"
                    f"{notification.title}|{notification.message}\n")

    # ==================== Queue Management ====================

    def get_queue(self, session_id: str) -> List[Notification]:
        """Get queued notifications for session"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM notifications
                WHERE session_id = ? AND status = 'queued'
                ORDER BY priority DESC, created_at ASC
            """, (session_id,)).fetchall()

            return [self._row_to_notification(row) for row in rows]

    def flush(self, session_id: str) -> int:
        """Flush queued notifications"""
        queued = self.get_queue(session_id)

        if not queued:
            return 0

        # Group similar notifications
        groups = self._group_notifications(queued)

        sent_count = 0
        for group in groups:
            if len(group.notifications) == 1:
                # Send individual notification
                self._send_notification(group.notifications[0])
            else:
                # Send grouped summary
                summary_notification = Notification(
                    notification_id=str(uuid.uuid4())[:8],
                    session_id=session_id,
                    title=group.title,
                    message=group.to_summary(),
                    priority=group.priority,
                    channel=NotificationChannel.DESKTOP,
                    group_id=group.group_id,
                    created_at=int(time.time()),
                )
                self._send_notification(summary_notification)

                # Mark individual notifications as sent
                for n in group.notifications:
                    n.status = NotificationStatus.SENT
                    n.sent_at = int(time.time())
                    n.group_id = group.group_id
                    self._save_notification(n)

            sent_count += len(group.notifications)

        return sent_count

    def _group_notifications(
        self,
        notifications: List[Notification]
    ) -> List[NotificationGroup]:
        """Group related notifications"""
        groups = {}

        for n in notifications:
            # Group by category and priority
            key = f"{n.category}:{n.priority.value}"

            if key not in groups:
                groups[key] = NotificationGroup(
                    group_id=str(uuid.uuid4())[:8],
                    session_id=n.session_id,
                    title=f"{n.category.title() if n.category else 'Updates'}",
                    notifications=[],
                    priority=n.priority,
                    created_at=n.created_at,
                )

            groups[key].notifications.append(n)

        return list(groups.values())

    # ==================== Notification Actions ====================

    def mark_read(self, notification_id: str):
        """Mark notification as read"""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE notifications SET
                    status = 'read',
                    read_at = ?
                WHERE notification_id = ?
            """, (int(time.time()), notification_id))

    def dismiss(self, notification_id: str):
        """Dismiss notification"""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE notifications SET status = 'dismissed'
                WHERE notification_id = ?
            """, (notification_id,))

    def clear_session(self, session_id: str):
        """Clear all notifications for a session"""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE notifications SET status = 'dismissed'
                WHERE session_id = ? AND status IN ('queued', 'sent')
            """, (session_id,))

    # ==================== Statistics ====================

    def get_stats(
        self,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get notification statistics"""
        with self._get_conn() as conn:
            where_clause = ""
            params = []

            if session_id:
                where_clause = "WHERE session_id = ?"
                params.append(session_id)

            total = conn.execute(f"""
                SELECT COUNT(*) as count FROM notifications {where_clause}
            """, params).fetchone()["count"]

            by_status = conn.execute(f"""
                SELECT status, COUNT(*) as count
                FROM notifications {where_clause}
                GROUP BY status
            """, params).fetchall()

            by_priority = conn.execute(f"""
                SELECT priority, COUNT(*) as count
                FROM notifications {where_clause}
                GROUP BY priority
            """, params).fetchall()

            # Calculate read rate
            read_stats = conn.execute(f"""
                SELECT
                    SUM(CASE WHEN status = 'read' THEN 1 ELSE 0 END) as read_count,
                    SUM(CASE WHEN status IN ('sent', 'delivered', 'read', 'dismissed') THEN 1 ELSE 0 END) as delivered_count
                FROM notifications {where_clause}
            """, params).fetchone()

            read_rate = 0
            if read_stats and read_stats["delivered_count"] > 0:
                read_rate = read_stats["read_count"] / read_stats["delivered_count"]

            # Calculate average response time
            avg_response = conn.execute(f"""
                SELECT AVG(read_at - sent_at) as avg_time
                FROM notifications
                {where_clause}
                    {"AND" if where_clause else "WHERE"} read_at IS NOT NULL AND sent_at IS NOT NULL
            """, params).fetchone()["avg_time"]

            return {
                "total_notifications": total,
                "by_status": {row["status"]: row["count"] for row in by_status},
                "by_priority": {row["priority"]: row["count"] for row in by_priority},
                "read_rate": round(read_rate, 3),
                "average_response_seconds": round(avg_response, 1) if avg_response else None,
            }

    def get_recent(
        self,
        session_id: str,
        limit: int = 10
    ) -> List[Notification]:
        """Get recent notifications"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM notifications
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (session_id, limit)).fetchall()

            return [self._row_to_notification(row) for row in rows]


# ==================== CLI Interface ====================

def main():
    parser = argparse.ArgumentParser(
        description="Claude Monitor Smart Notifier"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # send command
    send_parser = subparsers.add_parser("send", help="Send notification")
    send_parser.add_argument("session_id", help="Session ID")
    send_parser.add_argument("message", help="Notification message")
    send_parser.add_argument("--title", help="Notification title")
    send_parser.add_argument(
        "--priority",
        choices=[p.value for p in NotificationPriority],
        default="normal",
        help="Priority level"
    )
    send_parser.add_argument("--category", default="info", help="Category")
    send_parser.add_argument("--immediate", action="store_true", help="Send immediately")

    # queue command
    queue_parser = subparsers.add_parser("queue", help="Show queue")
    queue_parser.add_argument("session_id", help="Session ID")

    # flush command
    flush_parser = subparsers.add_parser("flush", help="Flush queue")
    flush_parser.add_argument("session_id", help="Session ID")

    # recent command
    recent_parser = subparsers.add_parser("recent", help="Show recent")
    recent_parser.add_argument("session_id", help="Session ID")
    recent_parser.add_argument("--limit", type=int, default=10, help="Max items")

    # stats command
    stats_parser = subparsers.add_parser("stats", help="Show statistics")
    stats_parser.add_argument("--session", help="Session ID filter")

    # clear command
    clear_parser = subparsers.add_parser("clear", help="Clear notifications")
    clear_parser.add_argument("session_id", help="Session ID")

    args = parser.parse_args()
    notifier = SmartNotifier()

    if args.command == "send":
        notification = notifier.notify(
            session_id=args.session_id,
            message=args.message,
            title=args.title or "",
            priority=NotificationPriority(args.priority),
            category=args.category,
            immediate=args.immediate,
        )
        print(json.dumps(notification.to_dict(), indent=2))

    elif args.command == "queue":
        queue = notifier.get_queue(args.session_id)
        print(json.dumps([n.to_dict() for n in queue], indent=2))

    elif args.command == "flush":
        count = notifier.flush(args.session_id)
        print(f"Flushed {count} notifications")

    elif args.command == "recent":
        recent = notifier.get_recent(args.session_id, args.limit)
        print(json.dumps([n.to_dict() for n in recent], indent=2))

    elif args.command == "stats":
        stats = notifier.get_stats(args.session)
        print(json.dumps(stats, indent=2))

    elif args.command == "clear":
        notifier.clear_session(args.session_id)
        print(f"Cleared notifications for {args.session_id}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
