#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Status Visualizer
状态可视化 - 生成各种状态展示

功能：
1. 实时状态面板（当前目标进度/阶段/最近决策/健康指标）
2. 历史时间线（事件/决策/进度时间线）
3. 诊断面板（错误统计/阻塞分析/性能指标）
4. 报告生成（会话/项目/学习报告）

Usage:
    python3 status_visualizer.py dashboard <session_id>
    python3 status_visualizer.py timeline <session_id> [--limit 20]
    python3 status_visualizer.py health <session_id>
    python3 status_visualizer.py report <session_id> [--format text|json|html]
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from compat_dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Database path
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))


class HealthStatus(Enum):
    """Health status indicators"""
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class ProgressBar:
    """A progress bar representation"""
    current: float
    total: float = 1.0
    width: int = 30
    filled_char: str = "█"
    empty_char: str = "░"

    def render(self) -> str:
        """Render progress bar as string"""
        ratio = min(1.0, max(0.0, self.current / self.total))
        filled = int(self.width * ratio)
        bar = self.filled_char * filled + self.empty_char * (self.width - filled)
        percentage = ratio * 100
        return f"[{bar}] {percentage:.1f}%"


@dataclass
class StatusPanel:
    """Status panel data"""
    session_id: str
    stage: str = "unknown"
    progress: float = 0.0
    goal: str = ""
    current_activity: str = ""
    last_decision: str = ""
    health: HealthStatus = HealthStatus.UNKNOWN
    health_details: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    timestamp: int = 0

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "goal": self.goal,
            "current_activity": self.current_activity,
            "last_decision": self.last_decision,
            "health": self.health.value,
            "health_details": self.health_details,
            "metrics": self.metrics,
            "warnings": self.warnings,
            "timestamp": self.timestamp,
        }


@dataclass
class TimelineEvent:
    """A timeline event"""
    timestamp: int
    event_type: str
    title: str
    description: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    importance: str = "normal"  # low, normal, high, critical

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "time_str": datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S"),
            "event_type": self.event_type,
            "title": self.title,
            "description": self.description,
            "data": self.data,
            "importance": self.importance,
        }


class StatusVisualizer:
    """Status visualization engine"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH

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

    # ==================== Dashboard ====================

    def get_dashboard(self, session_id: str) -> StatusPanel:
        """Get current status dashboard"""
        panel = StatusPanel(
            session_id=session_id,
            timestamp=int(time.time()),
        )

        with self._get_conn() as conn:
            # Get session info
            try:
                session = conn.execute("""
                    SELECT * FROM sessions
                    WHERE session_id = ?
                """, (session_id,)).fetchone()

                if session:
                    panel.stage = session.get("last_stage", "unknown") or "unknown"
            except sqlite3.OperationalError:
                pass

            # Get goal info
            try:
                goal = conn.execute("""
                    SELECT * FROM goals
                    WHERE session_id = ? AND level = 'goal'
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (session_id,)).fetchone()

                if goal:
                    panel.goal = goal.get("title", "") or ""
                    panel.progress = goal.get("progress", 0) or 0
            except sqlite3.OperationalError:
                pass

            # Get last decision
            try:
                decision = conn.execute("""
                    SELECT * FROM decisions
                    WHERE session_id = ?
                    ORDER BY timestamp DESC
                    LIMIT 1
                """, (session_id,)).fetchone()

                if decision:
                    panel.last_decision = decision.get("outcome", "") or "unknown"
                    panel.current_activity = (decision.get("input_preview", "") or "")[:100]
            except sqlite3.OperationalError:
                pass

            # Calculate health
            panel.health, panel.health_details = self._calculate_health(conn, session_id)

            # Get metrics
            panel.metrics = self._get_metrics(conn, session_id)

            # Get warnings
            panel.warnings = self._get_warnings(conn, session_id)

        return panel

    def _calculate_health(
        self,
        conn: sqlite3.Connection,
        session_id: str
    ) -> Tuple[HealthStatus, Dict[str, Any]]:
        """Calculate session health status"""
        details = {}
        issues = []

        # Check for recent errors
        try:
            error_count = conn.execute("""
                SELECT COUNT(*) as count FROM decisions
                WHERE session_id = ?
                  AND timestamp > ?
                  AND (input_preview LIKE '%error%' OR input_preview LIKE '%Error%')
            """, (session_id, int(time.time()) - 300)).fetchone()["count"]

            details["recent_errors"] = error_count
            if error_count >= 5:
                issues.append("critical")
            elif error_count >= 2:
                issues.append("warning")
        except sqlite3.OperationalError:
            pass

        # Check decision rate
        try:
            decision_count = conn.execute("""
                SELECT COUNT(*) as count FROM decisions
                WHERE session_id = ?
                  AND timestamp > ?
            """, (session_id, int(time.time()) - 600)).fetchone()["count"]

            details["decisions_10min"] = decision_count
            if decision_count == 0:
                issues.append("warning")
        except sqlite3.OperationalError:
            pass

        # Check for stuck patterns
        try:
            loops = conn.execute("""
                SELECT input_hash, COUNT(*) as count
                FROM decisions
                WHERE session_id = ?
                  AND timestamp > ?
                GROUP BY input_hash
                HAVING count >= 3
            """, (session_id, int(time.time()) - 600)).fetchall()

            details["loop_patterns"] = len(loops)
            if len(loops) >= 2:
                issues.append("critical")
            elif len(loops) >= 1:
                issues.append("warning")
        except sqlite3.OperationalError:
            pass

        # Determine overall health
        if "critical" in issues:
            return HealthStatus.CRITICAL, details
        elif "warning" in issues:
            return HealthStatus.WARNING, details
        elif details:
            return HealthStatus.HEALTHY, details
        else:
            return HealthStatus.UNKNOWN, details

    def _get_metrics(
        self,
        conn: sqlite3.Connection,
        session_id: str
    ) -> Dict[str, Any]:
        """Get session metrics"""
        metrics = {}

        try:
            # Total decisions
            total = conn.execute("""
                SELECT COUNT(*) as count FROM decisions
                WHERE session_id = ?
            """, (session_id,)).fetchone()
            metrics["total_decisions"] = total["count"] if total else 0
        except sqlite3.OperationalError:
            metrics["total_decisions"] = 0

        try:
            # Decisions by outcome
            by_outcome = conn.execute("""
                SELECT outcome, COUNT(*) as count
                FROM decisions
                WHERE session_id = ?
                GROUP BY outcome
            """, (session_id,)).fetchall()
            metrics["by_outcome"] = {row["outcome"]: row["count"] for row in by_outcome}
        except sqlite3.OperationalError:
            metrics["by_outcome"] = {}

        try:
            # Session duration
            session = conn.execute("""
                SELECT start_time FROM sessions WHERE session_id = ?
            """, (session_id,)).fetchone()
            if session and session["start_time"]:
                duration = int(time.time()) - session["start_time"]
                metrics["duration_minutes"] = duration // 60
        except sqlite3.OperationalError:
            pass

        return metrics

    def _get_warnings(
        self,
        conn: sqlite3.Connection,
        session_id: str
    ) -> List[str]:
        """Get active warnings for session"""
        warnings = []

        try:
            # Check for high error rate
            recent = conn.execute("""
                SELECT
                    SUM(CASE WHEN input_preview LIKE '%error%' THEN 1 ELSE 0 END) as errors,
                    COUNT(*) as total
                FROM decisions
                WHERE session_id = ? AND timestamp > ?
            """, (session_id, int(time.time()) - 300)).fetchone()

            if recent and recent["total"] > 5:
                error_rate = recent["errors"] / recent["total"]
                if error_rate > 0.5:
                    warnings.append(f"High error rate: {error_rate:.0%}")
        except sqlite3.OperationalError:
            pass

        try:
            # Check for stuck state
            same_stage = conn.execute("""
                SELECT stage, COUNT(*) as count
                FROM decisions
                WHERE session_id = ? AND timestamp > ?
                GROUP BY stage
                ORDER BY count DESC
                LIMIT 1
            """, (session_id, int(time.time()) - 300)).fetchone()

            if same_stage and same_stage["count"] > 10:
                warnings.append(f"Stuck in stage: {same_stage['stage']}")
        except sqlite3.OperationalError:
            pass

        return warnings

    def render_dashboard(self, panel: StatusPanel) -> str:
        """Render dashboard as text"""
        lines = []

        # Header
        health_icons = {
            HealthStatus.HEALTHY: "🟢",
            HealthStatus.WARNING: "🟡",
            HealthStatus.CRITICAL: "🔴",
            HealthStatus.UNKNOWN: "⚪",
        }
        health_icon = health_icons.get(panel.health, "⚪")

        lines.append("╔═══════════════════════════════════════════════════════════╗")
        lines.append(f"║  {health_icon} Session: {panel.session_id:<44} ║")
        lines.append("╠═══════════════════════════════════════════════════════════╣")

        # Goal and progress
        if panel.goal:
            lines.append(f"║  Goal: {panel.goal[:50]:<50} ║")
            progress_bar = ProgressBar(panel.progress, 1.0, 40).render()
            lines.append(f"║  Progress: {progress_bar:<46} ║")
        else:
            lines.append(f"║  Goal: (not set){'':<42} ║")

        # Current state
        lines.append("╠═══════════════════════════════════════════════════════════╣")
        lines.append(f"║  Stage: {panel.stage:<50} ║")
        lines.append(f"║  Last Decision: {panel.last_decision:<42} ║")

        # Activity
        if panel.current_activity:
            activity = panel.current_activity[:45]
            lines.append(f"║  Activity: {activity:<47} ║")

        # Metrics
        if panel.metrics:
            lines.append("╠═══════════════════════════════════════════════════════════╣")
            lines.append(f"║  Metrics:{'':<50} ║")
            for key, value in list(panel.metrics.items())[:4]:
                if isinstance(value, dict):
                    continue
                lines.append(f"║    • {key}: {str(value):<48} ║")

        # Warnings
        if panel.warnings:
            lines.append("╠═══════════════════════════════════════════════════════════╣")
            lines.append(f"║  ⚠️ Warnings:{'':<46} ║")
            for warning in panel.warnings[:3]:
                lines.append(f"║    • {warning[:50]:<52} ║")

        lines.append("╚═══════════════════════════════════════════════════════════╝")

        return "\n".join(lines)

    # ==================== Timeline ====================

    def get_timeline(
        self,
        session_id: str,
        limit: int = 20
    ) -> List[TimelineEvent]:
        """Get timeline of events for session"""
        events = []

        with self._get_conn() as conn:
            # Get decisions
            try:
                decisions = conn.execute("""
                    SELECT * FROM decisions
                    WHERE session_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (session_id, limit)).fetchall()

                for d in decisions:
                    importance = "normal"
                    if d["outcome"] == "command":
                        importance = "high"
                    elif "error" in (d["input_preview"] or "").lower():
                        importance = "critical"

                    events.append(TimelineEvent(
                        timestamp=d["timestamp"],
                        event_type="decision",
                        title=f"Decision: {d['outcome']}",
                        description=(d["input_preview"] or "")[:100],
                        data={"stage": d["stage"], "role": d["role"]},
                        importance=importance,
                    ))
            except sqlite3.OperationalError:
                pass

            # Get interventions
            try:
                interventions = conn.execute("""
                    SELECT * FROM interventions
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (session_id, limit)).fetchall()

                for i in interventions:
                    importance = "high" if i["urgency"] in ["high", "critical"] else "normal"
                    events.append(TimelineEvent(
                        timestamp=i["created_at"],
                        event_type="intervention",
                        title=f"Intervention: {i['intervention_type']}",
                        description=i["message"] or "",
                        data={"trigger": i["trigger"], "outcome": i["outcome"]},
                        importance=importance,
                    ))
            except sqlite3.OperationalError:
                pass

            # Get plan events
            try:
                plan_events = conn.execute("""
                    SELECT * FROM plan_events
                    WHERE plan_id IN (
                        SELECT plan_id FROM plans WHERE session_id = ?
                    )
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (session_id, limit)).fetchall()

                for p in plan_events:
                    events.append(TimelineEvent(
                        timestamp=p["created_at"],
                        event_type="plan",
                        title=f"Plan: {p['event_type']}",
                        description="",
                        data=json.loads(p["event_data"] or "{}"),
                        importance="normal",
                    ))
            except sqlite3.OperationalError:
                pass

        # Sort by timestamp
        events.sort(key=lambda e: e.timestamp, reverse=True)
        return events[:limit]

    def render_timeline(self, events: List[TimelineEvent]) -> str:
        """Render timeline as text"""
        lines = []

        for event in events:
            time_str = datetime.fromtimestamp(event.timestamp).strftime("%H:%M:%S")

            # Importance indicators
            importance_markers = {
                "low": "○",
                "normal": "●",
                "high": "◆",
                "critical": "★",
            }
            marker = importance_markers.get(event.importance, "●")

            # Event type colors/markers
            type_prefixes = {
                "decision": "📋",
                "intervention": "🔔",
                "plan": "📝",
                "error": "❌",
            }
            prefix = type_prefixes.get(event.event_type, "•")

            lines.append(f"  {time_str} {marker} {prefix} {event.title}")
            if event.description:
                lines.append(f"           └─ {event.description[:60]}")

        return "\n".join(lines)

    # ==================== Health Report ====================

    def get_health_report(self, session_id: str) -> Dict[str, Any]:
        """Get detailed health report"""
        report = {
            "session_id": session_id,
            "generated_at": int(time.time()),
            "overall_health": "unknown",
            "components": {},
            "recommendations": [],
        }

        with self._get_conn() as conn:
            # Decision health
            try:
                decisions = conn.execute("""
                    SELECT outcome, COUNT(*) as count
                    FROM decisions
                    WHERE session_id = ?
                    GROUP BY outcome
                """, (session_id,)).fetchall()

                decision_health = {row["outcome"]: row["count"] for row in decisions}
                total = sum(decision_health.values())
                wait_ratio = decision_health.get("wait", 0) / max(1, total)

                report["components"]["decisions"] = {
                    "total": total,
                    "distribution": decision_health,
                    "wait_ratio": round(wait_ratio, 3),
                    "status": "healthy" if wait_ratio > 0.5 else "warning",
                }
            except sqlite3.OperationalError:
                report["components"]["decisions"] = {"status": "unknown"}

            # Error health
            try:
                errors = conn.execute("""
                    SELECT COUNT(*) as count FROM decisions
                    WHERE session_id = ?
                      AND input_preview LIKE '%error%'
                """, (session_id,)).fetchone()

                error_count = errors["count"] if errors else 0
                report["components"]["errors"] = {
                    "count": error_count,
                    "status": "critical" if error_count > 10 else "warning" if error_count > 3 else "healthy",
                }
            except sqlite3.OperationalError:
                report["components"]["errors"] = {"status": "unknown"}

            # Progress health
            try:
                goals = conn.execute("""
                    SELECT progress, status FROM goals
                    WHERE session_id = ? AND level = 'goal'
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (session_id,)).fetchone()

                if goals:
                    report["components"]["progress"] = {
                        "value": goals["progress"],
                        "goal_status": goals["status"],
                        "status": "healthy" if goals["progress"] > 0.5 else "warning",
                    }
                else:
                    report["components"]["progress"] = {"status": "unknown", "note": "No goals set"}
            except sqlite3.OperationalError:
                report["components"]["progress"] = {"status": "unknown"}

            # Generate recommendations
            if report["components"].get("errors", {}).get("status") == "critical":
                report["recommendations"].append("High error count detected - consider reviewing recent changes")

            if report["components"].get("decisions", {}).get("wait_ratio", 1) < 0.3:
                report["recommendations"].append("Low wait ratio - monitor may be intervening too frequently")

            # Determine overall health
            statuses = [c.get("status", "unknown") for c in report["components"].values()]
            if "critical" in statuses:
                report["overall_health"] = "critical"
            elif "warning" in statuses:
                report["overall_health"] = "warning"
            elif all(s == "healthy" for s in statuses if s != "unknown"):
                report["overall_health"] = "healthy"

        return report

    # ==================== Report Generation ====================

    def generate_report(
        self,
        session_id: str,
        format: str = "text"
    ) -> str:
        """Generate session report"""
        dashboard = self.get_dashboard(session_id)
        timeline = self.get_timeline(session_id, 10)
        health = self.get_health_report(session_id)

        if format == "json":
            return json.dumps({
                "dashboard": dashboard.to_dict(),
                "timeline": [e.to_dict() for e in timeline],
                "health": health,
            }, indent=2)

        elif format == "html":
            return self._render_html_report(dashboard, timeline, health)

        else:  # text
            return self._render_text_report(dashboard, timeline, health)

    def _render_text_report(
        self,
        dashboard: StatusPanel,
        timeline: List[TimelineEvent],
        health: Dict[str, Any]
    ) -> str:
        """Render text format report"""
        lines = []

        lines.append("=" * 60)
        lines.append("SESSION REPORT")
        lines.append("=" * 60)
        lines.append("")

        # Dashboard section
        lines.append(self.render_dashboard(dashboard))
        lines.append("")

        # Timeline section
        lines.append("RECENT TIMELINE")
        lines.append("-" * 40)
        lines.append(self.render_timeline(timeline))
        lines.append("")

        # Health section
        lines.append("HEALTH SUMMARY")
        lines.append("-" * 40)
        lines.append(f"Overall: {health['overall_health'].upper()}")

        for component, data in health.get("components", {}).items():
            status = data.get("status", "unknown")
            lines.append(f"  {component}: {status}")

        if health.get("recommendations"):
            lines.append("")
            lines.append("Recommendations:")
            for rec in health["recommendations"]:
                lines.append(f"  • {rec}")

        lines.append("")
        lines.append("=" * 60)

        return "\n".join(lines)

    def _render_html_report(
        self,
        dashboard: StatusPanel,
        timeline: List[TimelineEvent],
        health: Dict[str, Any]
    ) -> str:
        """Render HTML format report"""
        health_colors = {
            "healthy": "#28a745",
            "warning": "#ffc107",
            "critical": "#dc3545",
            "unknown": "#6c757d",
        }

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Session Report - {dashboard.session_id}</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
        .panel {{ background: #f8f9fa; border-radius: 8px; padding: 20px; margin-bottom: 20px; }}
        .health-badge {{ padding: 4px 12px; border-radius: 4px; color: white; font-weight: bold; }}
        .timeline-item {{ padding: 10px 0; border-bottom: 1px solid #eee; }}
        .metric {{ display: inline-block; margin-right: 20px; }}
    </style>
</head>
<body>
    <h1>Session Report</h1>
    <p>Session ID: {dashboard.session_id}</p>

    <div class="panel">
        <h2>Status
            <span class="health-badge" style="background: {health_colors.get(health['overall_health'], '#6c757d')}">
                {health['overall_health'].upper()}
            </span>
        </h2>
        <p><strong>Stage:</strong> {dashboard.stage}</p>
        <p><strong>Goal:</strong> {dashboard.goal or 'Not set'}</p>
        <p><strong>Progress:</strong> {dashboard.progress:.1%}</p>
    </div>

    <div class="panel">
        <h2>Metrics</h2>
        {"".join(f'<span class="metric"><strong>{k}:</strong> {v}</span>' for k, v in dashboard.metrics.items() if not isinstance(v, dict))}
    </div>

    <div class="panel">
        <h2>Recent Timeline</h2>
        {"".join(f'<div class="timeline-item"><strong>{e.title}</strong><br/><small>{datetime.fromtimestamp(e.timestamp).strftime("%H:%M:%S")} - {e.description}</small></div>' for e in timeline)}
    </div>

    {"<div class='panel'><h2>Recommendations</h2><ul>" + "".join(f"<li>{r}</li>" for r in health.get('recommendations', [])) + "</ul></div>" if health.get('recommendations') else ""}
</body>
</html>"""

        return html


# ==================== CLI Interface ====================

def main():
    parser = argparse.ArgumentParser(
        description="Claude Monitor Status Visualizer"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # dashboard command
    dash_parser = subparsers.add_parser("dashboard", help="Show dashboard")
    dash_parser.add_argument("session_id", help="Session ID")
    dash_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # timeline command
    time_parser = subparsers.add_parser("timeline", help="Show timeline")
    time_parser.add_argument("session_id", help="Session ID")
    time_parser.add_argument("--limit", type=int, default=20, help="Max events")

    # health command
    health_parser = subparsers.add_parser("health", help="Show health report")
    health_parser.add_argument("session_id", help="Session ID")

    # report command
    report_parser = subparsers.add_parser("report", help="Generate report")
    report_parser.add_argument("session_id", help="Session ID")
    report_parser.add_argument(
        "--format",
        choices=["text", "json", "html"],
        default="text",
        help="Output format"
    )

    args = parser.parse_args()
    visualizer = StatusVisualizer()

    if args.command == "dashboard":
        panel = visualizer.get_dashboard(args.session_id)
        if args.json:
            print(json.dumps(panel.to_dict(), indent=2))
        else:
            print(visualizer.render_dashboard(panel))

    elif args.command == "timeline":
        events = visualizer.get_timeline(args.session_id, args.limit)
        print(visualizer.render_timeline(events))

    elif args.command == "health":
        health = visualizer.get_health_report(args.session_id)
        print(json.dumps(health, indent=2))

    elif args.command == "report":
        report = visualizer.generate_report(args.session_id, args.format)
        print(report)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
