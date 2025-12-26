#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Plan Generator
规划生成器 - 生成和管理执行计划

功能：
1. 计划生成（基于目标/状态/约束）
2. 计划验证（可行性/依赖/风险）
3. 计划执行监控（进度/偏离/调整）
4. 计划优化（反馈/新信息/环境变化）

Usage:
    python3 plan_generator.py generate <session_id> <goal>
    python3 plan_generator.py validate <plan_id>
    python3 plan_generator.py track <plan_id>
    python3 plan_generator.py adjust <plan_id> [--reason <reason>]
    python3 plan_generator.py complete <plan_id> <step_index>
"""

import argparse
import json
import os
import re
import sqlite3
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


class PlanStatus(Enum):
    """Plan lifecycle status"""
    DRAFT = "draft"                # Not yet validated
    ACTIVE = "active"              # Currently executing
    PAUSED = "paused"              # Temporarily stopped
    COMPLETED = "completed"        # Successfully finished
    ABANDONED = "abandoned"        # Gave up
    FAILED = "failed"              # Could not complete


class StepStatus(Enum):
    """Step status within a plan"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    BLOCKED = "blocked"


class StepType(Enum):
    """Type of plan step"""
    ACTION = "action"              # Do something
    CHECK = "check"                # Verify condition
    WAIT = "wait"                  # Wait for something
    BRANCH = "branch"              # Conditional branch
    LOOP = "loop"                  # Repeat until condition


class RiskLevel(Enum):
    """Risk level assessment"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class PlanStep:
    """A step in the execution plan"""
    step_id: str = ""
    index: int = 0
    step_type: StepType = StepType.ACTION
    title: str = ""
    description: str = ""
    action: str = ""
    expected_outcome: str = ""
    dependencies: List[int] = field(default_factory=list)  # Indices of prerequisite steps
    status: StepStatus = StepStatus.PENDING
    started_at: Optional[int] = None
    completed_at: Optional[int] = None
    result: str = ""
    notes: str = ""

    def to_dict(self) -> Dict:
        return {
            "step_id": self.step_id,
            "index": self.index,
            "step_type": self.step_type.value,
            "title": self.title,
            "description": self.description,
            "action": self.action,
            "expected_outcome": self.expected_outcome,
            "dependencies": self.dependencies,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "notes": self.notes,
        }


@dataclass
class Plan:
    """Execution plan"""
    plan_id: str = ""
    session_id: str = ""
    goal: str = ""
    goal_type: str = ""            # implement/fix/refactor/test/deploy
    status: PlanStatus = PlanStatus.DRAFT
    steps: List[PlanStep] = field(default_factory=list)
    constraints: Dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW
    risk_factors: List[str] = field(default_factory=list)
    estimated_duration: int = 0    # Seconds
    actual_duration: int = 0
    progress: float = 0.0          # 0.0 - 1.0
    created_at: int = 0
    started_at: Optional[int] = None
    completed_at: Optional[int] = None
    revision: int = 1
    parent_plan_id: Optional[str] = None  # For revised plans

    def to_dict(self) -> Dict:
        return {
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "goal": self.goal,
            "goal_type": self.goal_type,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "constraints": self.constraints,
            "risk_level": self.risk_level.value,
            "risk_factors": self.risk_factors,
            "estimated_duration": self.estimated_duration,
            "actual_duration": self.actual_duration,
            "progress": round(self.progress, 3),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "revision": self.revision,
            "parent_plan_id": self.parent_plan_id,
        }

    def get_current_step(self) -> Optional[PlanStep]:
        """Get the current in-progress step"""
        for step in self.steps:
            if step.status == StepStatus.IN_PROGRESS:
                return step
        return None

    def get_next_step(self) -> Optional[PlanStep]:
        """Get the next pending step"""
        for step in self.steps:
            if step.status == StepStatus.PENDING:
                # Check dependencies
                deps_met = all(
                    self.steps[d].status == StepStatus.COMPLETED
                    for d in step.dependencies
                    if d < len(self.steps)
                )
                if deps_met:
                    return step
        return None


@dataclass
class ValidationResult:
    """Plan validation result"""
    is_valid: bool
    issues: List[Dict[str, Any]]
    warnings: List[str]
    risk_assessment: Dict[str, Any]

    def to_dict(self) -> Dict:
        return {
            "is_valid": self.is_valid,
            "issues": self.issues,
            "warnings": self.warnings,
            "risk_assessment": self.risk_assessment,
        }


class PlanGenerator:
    """Plan generation and management engine"""

    # Goal type patterns
    GOAL_PATTERNS = {
        "implement": [r"implement", r"create", r"add", r"build", r"develop"],
        "fix": [r"fix", r"repair", r"resolve", r"debug", r"solve"],
        "refactor": [r"refactor", r"improve", r"optimize", r"clean", r"restructure"],
        "test": [r"test", r"verify", r"validate", r"check"],
        "deploy": [r"deploy", r"release", r"publish", r"ship"],
        "configure": [r"configure", r"setup", r"install", r"enable"],
        "document": [r"document", r"describe", r"explain", r"comment"],
    }

    # Risk patterns
    RISK_PATTERNS = {
        RiskLevel.CRITICAL: [
            r"production", r"database", r"delete", r"drop", r"destroy",
            r"force", r"sudo", r"root", r"credentials",
        ],
        RiskLevel.HIGH: [
            r"deploy", r"migration", r"schema", r"security", r"auth",
            r"api", r"external", r"third-party",
        ],
        RiskLevel.MEDIUM: [
            r"refactor", r"change", r"update", r"modify", r"remove",
        ],
    }

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    def _ensure_db(self):
        """Ensure database exists with proper schema"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    goal_type TEXT,
                    status TEXT DEFAULT 'draft',
                    steps TEXT,
                    constraints TEXT,
                    risk_level TEXT DEFAULT 'low',
                    risk_factors TEXT,
                    estimated_duration INTEGER DEFAULT 0,
                    actual_duration INTEGER DEFAULT 0,
                    progress REAL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    started_at INTEGER,
                    completed_at INTEGER,
                    revision INTEGER DEFAULT 1,
                    parent_plan_id TEXT
                );

                CREATE TABLE IF NOT EXISTS plan_events (
                    event_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    step_index INTEGER,
                    event_data TEXT,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
                );

                CREATE INDEX IF NOT EXISTS idx_plans_session
                    ON plans(session_id);
                CREATE INDEX IF NOT EXISTS idx_plans_status
                    ON plans(status);
                CREATE INDEX IF NOT EXISTS idx_events_plan
                    ON plan_events(plan_id);
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

    def _row_to_plan(self, row: sqlite3.Row) -> Plan:
        """Convert database row to Plan object"""
        steps_data = json.loads(row["steps"] or "[]")
        steps = [
            PlanStep(
                step_id=s.get("step_id", ""),
                index=s.get("index", i),
                step_type=StepType(s.get("step_type", "action")),
                title=s.get("title", ""),
                description=s.get("description", ""),
                action=s.get("action", ""),
                expected_outcome=s.get("expected_outcome", ""),
                dependencies=s.get("dependencies", []),
                status=StepStatus(s.get("status", "pending")),
                started_at=s.get("started_at"),
                completed_at=s.get("completed_at"),
                result=s.get("result", ""),
                notes=s.get("notes", ""),
            )
            for i, s in enumerate(steps_data)
        ]

        return Plan(
            plan_id=row["plan_id"],
            session_id=row["session_id"],
            goal=row["goal"],
            goal_type=row["goal_type"] or "",
            status=PlanStatus(row["status"] or "draft"),
            steps=steps,
            constraints=json.loads(row["constraints"] or "{}"),
            risk_level=RiskLevel(row["risk_level"] or "low"),
            risk_factors=json.loads(row["risk_factors"] or "[]"),
            estimated_duration=row["estimated_duration"] or 0,
            actual_duration=row["actual_duration"] or 0,
            progress=row["progress"] or 0.0,
            created_at=row["created_at"] or 0,
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            revision=row["revision"] or 1,
            parent_plan_id=row["parent_plan_id"],
        )

    # ==================== Plan Generation ====================

    def generate(
        self,
        session_id: str,
        goal: str,
        constraints: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> Plan:
        """Generate an execution plan for a goal"""
        constraints = constraints or {}
        context = context or {}

        # Analyze goal type
        goal_type = self._analyze_goal_type(goal)

        # Generate steps based on goal type
        steps = self._generate_steps(goal, goal_type, context)

        # Assess risks
        risk_level, risk_factors = self._assess_risks(goal, steps)

        # Estimate duration
        estimated_duration = self._estimate_duration(steps)

        # Create plan
        plan = Plan(
            plan_id=str(uuid.uuid4())[:8],
            session_id=session_id,
            goal=goal,
            goal_type=goal_type,
            status=PlanStatus.DRAFT,
            steps=steps,
            constraints=constraints,
            risk_level=risk_level,
            risk_factors=risk_factors,
            estimated_duration=estimated_duration,
            created_at=int(time.time()),
        )

        self._save_plan(plan)
        self._log_event(plan.plan_id, "created", None, {
            "goal": goal,
            "step_count": len(steps),
        })

        return plan

    def _analyze_goal_type(self, goal: str) -> str:
        """Analyze and categorize the goal"""
        goal_lower = goal.lower()

        for goal_type, patterns in self.GOAL_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, goal_lower):
                    return goal_type

        return "general"

    def _generate_steps(
        self,
        goal: str,
        goal_type: str,
        context: Dict[str, Any]
    ) -> List[PlanStep]:
        """Generate plan steps based on goal type"""
        steps = []

        # Common templates by goal type
        templates = {
            "implement": [
                ("Understand requirements", "Analyze what needs to be implemented", StepType.CHECK),
                ("Design approach", "Plan the implementation approach", StepType.ACTION),
                ("Implement core functionality", "Write the main code", StepType.ACTION),
                ("Add error handling", "Handle edge cases and errors", StepType.ACTION),
                ("Write tests", "Create unit/integration tests", StepType.ACTION),
                ("Verify implementation", "Run tests and validate", StepType.CHECK),
            ],
            "fix": [
                ("Reproduce issue", "Confirm the bug exists", StepType.CHECK),
                ("Identify root cause", "Debug and find the source", StepType.ACTION),
                ("Develop fix", "Implement the solution", StepType.ACTION),
                ("Test fix", "Verify the fix works", StepType.CHECK),
                ("Check for regressions", "Ensure nothing else broke", StepType.CHECK),
            ],
            "refactor": [
                ("Identify scope", "Define what to refactor", StepType.CHECK),
                ("Ensure tests exist", "Verify test coverage", StepType.CHECK),
                ("Perform refactoring", "Make the changes", StepType.ACTION),
                ("Run tests", "Verify nothing broke", StepType.CHECK),
                ("Review changes", "Check code quality", StepType.CHECK),
            ],
            "test": [
                ("Identify test cases", "Define what to test", StepType.ACTION),
                ("Write test code", "Implement tests", StepType.ACTION),
                ("Run tests", "Execute test suite", StepType.ACTION),
                ("Analyze results", "Review test output", StepType.CHECK),
                ("Fix failures", "Address any failures", StepType.ACTION),
            ],
            "deploy": [
                ("Pre-deploy checks", "Verify readiness", StepType.CHECK),
                ("Backup current state", "Create backup/snapshot", StepType.ACTION),
                ("Deploy changes", "Push to target environment", StepType.ACTION),
                ("Verify deployment", "Check deployment status", StepType.CHECK),
                ("Post-deploy validation", "Smoke tests", StepType.CHECK),
            ],
            "configure": [
                ("Review requirements", "Understand configuration needs", StepType.CHECK),
                ("Apply configuration", "Make configuration changes", StepType.ACTION),
                ("Verify configuration", "Test the configuration", StepType.CHECK),
            ],
            "document": [
                ("Identify scope", "Determine what to document", StepType.CHECK),
                ("Write documentation", "Create the content", StepType.ACTION),
                ("Review for clarity", "Check readability", StepType.CHECK),
            ],
        }

        template = templates.get(goal_type, [
            ("Analyze goal", "Understand what needs to be done", StepType.CHECK),
            ("Execute task", "Perform the main work", StepType.ACTION),
            ("Verify completion", "Check if goal is met", StepType.CHECK),
        ])

        for i, (title, description, step_type) in enumerate(template):
            step = PlanStep(
                step_id=str(uuid.uuid4())[:8],
                index=i,
                step_type=step_type,
                title=title,
                description=description,
                action=f"Execute: {title}",
                expected_outcome=f"Complete: {title}",
                dependencies=[i - 1] if i > 0 else [],
                status=StepStatus.PENDING,
            )
            steps.append(step)

        return steps

    def _assess_risks(
        self,
        goal: str,
        steps: List[PlanStep]
    ) -> Tuple[RiskLevel, List[str]]:
        """Assess plan risk level"""
        risk_factors = []
        max_risk = RiskLevel.LOW

        content = goal.lower()
        for step in steps:
            content += " " + step.title.lower() + " " + step.action.lower()

        for level, patterns in self.RISK_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, content):
                    risk_factors.append(f"Contains '{pattern}' ({level.value} risk)")
                    if list(RiskLevel).index(level) > list(RiskLevel).index(max_risk):
                        max_risk = level

        return max_risk, risk_factors

    def _estimate_duration(self, steps: List[PlanStep]) -> int:
        """Estimate plan duration in seconds"""
        # Simple estimation: 60 seconds per action, 30 per check
        duration = 0
        for step in steps:
            if step.step_type == StepType.ACTION:
                duration += 60
            elif step.step_type == StepType.CHECK:
                duration += 30
            elif step.step_type == StepType.WAIT:
                duration += 120
            else:
                duration += 45

        return duration

    def _save_plan(self, plan: Plan):
        """Save plan to database"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO plans (
                    plan_id, session_id, goal, goal_type, status,
                    steps, constraints, risk_level, risk_factors,
                    estimated_duration, actual_duration, progress,
                    created_at, started_at, completed_at,
                    revision, parent_plan_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                plan.plan_id,
                plan.session_id,
                plan.goal,
                plan.goal_type,
                plan.status.value,
                json.dumps([s.to_dict() for s in plan.steps]),
                json.dumps(plan.constraints),
                plan.risk_level.value,
                json.dumps(plan.risk_factors),
                plan.estimated_duration,
                plan.actual_duration,
                plan.progress,
                plan.created_at,
                plan.started_at,
                plan.completed_at,
                plan.revision,
                plan.parent_plan_id,
            ))

    # ==================== Plan Validation ====================

    def validate(self, plan_id: str) -> ValidationResult:
        """Validate a plan for feasibility"""
        plan = self.get_plan(plan_id)
        if not plan:
            return ValidationResult(
                is_valid=False,
                issues=[{"type": "not_found", "message": "Plan not found"}],
                warnings=[],
                risk_assessment={},
            )

        issues = []
        warnings = []

        # Check for empty plan
        if not plan.steps:
            issues.append({
                "type": "empty_plan",
                "message": "Plan has no steps",
            })

        # Check step dependencies
        for step in plan.steps:
            for dep in step.dependencies:
                if dep >= len(plan.steps):
                    issues.append({
                        "type": "invalid_dependency",
                        "message": f"Step {step.index} depends on non-existent step {dep}",
                        "step_index": step.index,
                    })
                elif dep >= step.index:
                    issues.append({
                        "type": "forward_dependency",
                        "message": f"Step {step.index} depends on later step {dep}",
                        "step_index": step.index,
                    })

        # Check for circular dependencies
        if self._has_circular_deps(plan.steps):
            issues.append({
                "type": "circular_dependency",
                "message": "Plan contains circular dependencies",
            })

        # Risk warnings
        if plan.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
            warnings.append(f"Plan has {plan.risk_level.value} risk level")
            for factor in plan.risk_factors:
                warnings.append(f"Risk: {factor}")

        # Duration warning
        if plan.estimated_duration > 3600:  # More than 1 hour
            warnings.append(f"Estimated duration is {plan.estimated_duration // 60} minutes")

        is_valid = len(issues) == 0

        # If valid, mark as active
        if is_valid and plan.status == PlanStatus.DRAFT:
            plan.status = PlanStatus.ACTIVE
            plan.started_at = int(time.time())
            self._save_plan(plan)
            self._log_event(plan_id, "validated", None, {"is_valid": True})

        return ValidationResult(
            is_valid=is_valid,
            issues=issues,
            warnings=warnings,
            risk_assessment={
                "level": plan.risk_level.value,
                "factors": plan.risk_factors,
            },
        )

    def _has_circular_deps(self, steps: List[PlanStep]) -> bool:
        """Check for circular dependencies using DFS"""
        visited = set()
        rec_stack = set()

        def dfs(step_idx: int) -> bool:
            visited.add(step_idx)
            rec_stack.add(step_idx)

            if step_idx < len(steps):
                for dep in steps[step_idx].dependencies:
                    if dep not in visited:
                        if dfs(dep):
                            return True
                    elif dep in rec_stack:
                        return True

            rec_stack.remove(step_idx)
            return False

        for i in range(len(steps)):
            if i not in visited:
                if dfs(i):
                    return True

        return False

    # ==================== Plan Execution ====================

    def get_plan(self, plan_id: str) -> Optional[Plan]:
        """Get a plan by ID"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM plans WHERE plan_id = ?",
                (plan_id,)
            ).fetchone()

            if row:
                return self._row_to_plan(row)
            return None

    def start_step(self, plan_id: str, step_index: int) -> Optional[PlanStep]:
        """Mark a step as started"""
        plan = self.get_plan(plan_id)
        if not plan or step_index >= len(plan.steps):
            return None

        step = plan.steps[step_index]
        step.status = StepStatus.IN_PROGRESS
        step.started_at = int(time.time())

        self._save_plan(plan)
        self._log_event(plan_id, "step_started", step_index, {
            "step_title": step.title,
        })

        return step

    def complete_step(
        self,
        plan_id: str,
        step_index: int,
        result: str = "",
        success: bool = True
    ) -> Optional[PlanStep]:
        """Mark a step as completed"""
        plan = self.get_plan(plan_id)
        if not plan or step_index >= len(plan.steps):
            return None

        step = plan.steps[step_index]
        step.status = StepStatus.COMPLETED if success else StepStatus.FAILED
        step.completed_at = int(time.time())
        step.result = result

        # Update plan progress
        completed = sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED)
        plan.progress = completed / len(plan.steps)

        # Check if plan is complete
        if plan.progress >= 1.0:
            plan.status = PlanStatus.COMPLETED
            plan.completed_at = int(time.time())
            plan.actual_duration = plan.completed_at - (plan.started_at or plan.created_at)

        self._save_plan(plan)
        self._log_event(plan_id, "step_completed", step_index, {
            "step_title": step.title,
            "success": success,
            "result": result[:200],
        })

        return step

    def skip_step(self, plan_id: str, step_index: int, reason: str = "") -> Optional[PlanStep]:
        """Skip a step"""
        plan = self.get_plan(plan_id)
        if not plan or step_index >= len(plan.steps):
            return None

        step = plan.steps[step_index]
        step.status = StepStatus.SKIPPED
        step.notes = reason

        # Update progress (skipped steps count toward completion)
        completed = sum(
            1 for s in plan.steps
            if s.status in [StepStatus.COMPLETED, StepStatus.SKIPPED]
        )
        plan.progress = completed / len(plan.steps)

        self._save_plan(plan)
        self._log_event(plan_id, "step_skipped", step_index, {
            "step_title": step.title,
            "reason": reason,
        })

        return step

    def block_step(self, plan_id: str, step_index: int, reason: str = "") -> Optional[PlanStep]:
        """Mark a step as blocked"""
        plan = self.get_plan(plan_id)
        if not plan or step_index >= len(plan.steps):
            return None

        step = plan.steps[step_index]
        step.status = StepStatus.BLOCKED
        step.notes = reason

        self._save_plan(plan)
        self._log_event(plan_id, "step_blocked", step_index, {
            "step_title": step.title,
            "reason": reason,
        })

        return step

    # ==================== Plan Adjustment ====================

    def adjust(
        self,
        plan_id: str,
        reason: str,
        adjustments: Optional[Dict[str, Any]] = None
    ) -> Plan:
        """Adjust a plan based on new information"""
        plan = self.get_plan(plan_id)
        if not plan:
            raise ValueError(f"Plan {plan_id} not found")

        adjustments = adjustments or {}

        # Create new revision
        new_plan = Plan(
            plan_id=str(uuid.uuid4())[:8],
            session_id=plan.session_id,
            goal=adjustments.get("goal", plan.goal),
            goal_type=plan.goal_type,
            status=PlanStatus.DRAFT,
            steps=plan.steps.copy(),
            constraints=adjustments.get("constraints", plan.constraints),
            risk_level=plan.risk_level,
            risk_factors=plan.risk_factors,
            estimated_duration=plan.estimated_duration,
            created_at=int(time.time()),
            revision=plan.revision + 1,
            parent_plan_id=plan_id,
        )

        # Apply step adjustments if any
        if "add_steps" in adjustments:
            for step_data in adjustments["add_steps"]:
                step = PlanStep(
                    step_id=str(uuid.uuid4())[:8],
                    index=len(new_plan.steps),
                    step_type=StepType(step_data.get("type", "action")),
                    title=step_data.get("title", "New step"),
                    description=step_data.get("description", ""),
                    action=step_data.get("action", ""),
                    expected_outcome=step_data.get("expected_outcome", ""),
                )
                new_plan.steps.append(step)

        if "remove_steps" in adjustments:
            indices = set(adjustments["remove_steps"])
            new_plan.steps = [s for s in new_plan.steps if s.index not in indices]
            # Re-index
            for i, step in enumerate(new_plan.steps):
                step.index = i

        # Mark old plan as superseded
        plan.status = PlanStatus.ABANDONED
        self._save_plan(plan)

        # Save new plan
        self._save_plan(new_plan)
        self._log_event(new_plan.plan_id, "adjusted", None, {
            "reason": reason,
            "from_plan": plan_id,
        })

        return new_plan

    def track(self, plan_id: str) -> Dict[str, Any]:
        """Get tracking information for a plan"""
        plan = self.get_plan(plan_id)
        if not plan:
            return {"error": "Plan not found"}

        current = plan.get_current_step()
        next_step = plan.get_next_step()

        # Calculate time metrics
        elapsed = 0
        if plan.started_at:
            elapsed = int(time.time()) - plan.started_at

        remaining = max(0, plan.estimated_duration - elapsed)

        return {
            "plan_id": plan_id,
            "status": plan.status.value,
            "progress": round(plan.progress, 3),
            "current_step": current.to_dict() if current else None,
            "next_step": next_step.to_dict() if next_step else None,
            "steps_completed": sum(1 for s in plan.steps if s.status == StepStatus.COMPLETED),
            "steps_total": len(plan.steps),
            "elapsed_seconds": elapsed,
            "estimated_remaining": remaining,
            "blocked_steps": [
                s.to_dict() for s in plan.steps
                if s.status == StepStatus.BLOCKED
            ],
        }

    def _log_event(
        self,
        plan_id: str,
        event_type: str,
        step_index: Optional[int],
        event_data: Dict[str, Any]
    ):
        """Log a plan event"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO plan_events (
                    event_id, plan_id, event_type, step_index,
                    event_data, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                str(uuid.uuid4())[:8],
                plan_id,
                event_type,
                step_index,
                json.dumps(event_data),
                int(time.time()),
            ))

    # ==================== Query Methods ====================

    def get_active_plans(self, session_id: str) -> List[Plan]:
        """Get all active plans for a session"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM plans
                WHERE session_id = ? AND status = 'active'
                ORDER BY created_at DESC
            """, (session_id,)).fetchall()

            return [self._row_to_plan(row) for row in rows]

    def get_plan_history(self, plan_id: str) -> List[Dict[str, Any]]:
        """Get event history for a plan"""
        with self._get_conn() as conn:
            events = conn.execute("""
                SELECT * FROM plan_events
                WHERE plan_id = ?
                ORDER BY created_at ASC
            """, (plan_id,)).fetchall()

            return [
                {
                    "event_id": e["event_id"],
                    "event_type": e["event_type"],
                    "step_index": e["step_index"],
                    "data": json.loads(e["event_data"] or "{}"),
                    "created_at": e["created_at"],
                }
                for e in events
            ]


# ==================== CLI Interface ====================

def main():
    parser = argparse.ArgumentParser(
        description="Claude Monitor Plan Generator"
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # generate command
    gen_parser = subparsers.add_parser("generate", help="Generate a plan")
    gen_parser.add_argument("session_id", help="Session ID")
    gen_parser.add_argument("goal", help="Goal to achieve")
    gen_parser.add_argument("--constraints", help="Constraints JSON")

    # validate command
    val_parser = subparsers.add_parser("validate", help="Validate a plan")
    val_parser.add_argument("plan_id", help="Plan ID")

    # track command
    track_parser = subparsers.add_parser("track", help="Track plan progress")
    track_parser.add_argument("plan_id", help="Plan ID")

    # adjust command
    adjust_parser = subparsers.add_parser("adjust", help="Adjust a plan")
    adjust_parser.add_argument("plan_id", help="Plan ID")
    adjust_parser.add_argument("--reason", required=True, help="Reason for adjustment")
    adjust_parser.add_argument("--adjustments", help="Adjustments JSON")

    # complete command
    complete_parser = subparsers.add_parser("complete", help="Complete a step")
    complete_parser.add_argument("plan_id", help="Plan ID")
    complete_parser.add_argument("step_index", type=int, help="Step index")
    complete_parser.add_argument("--result", default="", help="Step result")

    # start-step command
    start_parser = subparsers.add_parser("start-step", help="Start a step")
    start_parser.add_argument("plan_id", help="Plan ID")
    start_parser.add_argument("step_index", type=int, help="Step index")

    # history command
    history_parser = subparsers.add_parser("history", help="Get plan history")
    history_parser.add_argument("plan_id", help="Plan ID")

    # status command (快捷命令：获取会话当前计划状态)
    status_parser = subparsers.add_parser("status", help="Get current plan status for session")
    status_parser.add_argument("session_id", help="Session ID")

    args = parser.parse_args()
    generator = PlanGenerator()

    if args.command == "generate":
        constraints = json.loads(args.constraints) if args.constraints else {}
        plan = generator.generate(args.session_id, args.goal, constraints)
        print(json.dumps(plan.to_dict(), indent=2))

    elif args.command == "validate":
        result = generator.validate(args.plan_id)
        print(json.dumps(result.to_dict(), indent=2))

    elif args.command == "track":
        tracking = generator.track(args.plan_id)
        print(json.dumps(tracking, indent=2))

    elif args.command == "adjust":
        adjustments = json.loads(args.adjustments) if args.adjustments else {}
        plan = generator.adjust(args.plan_id, args.reason, adjustments)
        print(json.dumps(plan.to_dict(), indent=2))

    elif args.command == "complete":
        step = generator.complete_step(args.plan_id, args.step_index, args.result)
        if step:
            print(json.dumps(step.to_dict(), indent=2))
        else:
            print("Step not found")

    elif args.command == "start-step":
        step = generator.start_step(args.plan_id, args.step_index)
        if step:
            print(json.dumps(step.to_dict(), indent=2))
        else:
            print("Step not found")

    elif args.command == "history":
        history = generator.get_plan_history(args.plan_id)
        print(json.dumps(history, indent=2))

    elif args.command == "status":
        # 查找会话当前活动的计划
        with generator._get_conn() as conn:
            plan_row = conn.execute("""
                SELECT * FROM plans
                WHERE session_id = ? AND status IN ('pending', 'in_progress')
                ORDER BY created_at DESC
                LIMIT 1
            """, (args.session_id,)).fetchone()

            if plan_row:
                plan = generator._row_to_plan(plan_row)
                # 计算进度
                total_steps = len(plan.steps)
                completed = sum(1 for s in plan.steps if s.status == "completed")
                in_progress = sum(1 for s in plan.steps if s.status == "in_progress")

                # 找到当前步骤
                current_step = None
                for s in plan.steps:
                    if s.status == "in_progress":
                        current_step = s
                        break
                    elif s.status == "pending" and current_step is None:
                        current_step = s

                # 输出简洁状态
                status_line = f"[plan] 进度: {completed}/{total_steps}"
                if current_step:
                    status_line += f" | 当前: {current_step.description[:40]}"
                print(status_line)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
