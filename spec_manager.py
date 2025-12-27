#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Spec Manager
会话级 Goal / DoD / Constraints 管理（Agent-of-Agent 入口）

设计目标：
- 不新增 DB schema（避免对现有 memory/schema.sql 产生侵入性变更）
- 支持把“目标/验收/约束”固化到磁盘，并可注入到 smart-monitor 的决策上下文
- 支持从 spec 文件读取（存路径 + 摘要），避免把大段文档直接塞进 LLM 上下文

Usage:
    python3 spec_manager.py set <session_id> --goal <text> [--dod <text> ...]
                               [--constraint <text> ...] [--out-of-scope <text> ...]
                               [--spec-file <path>] [--replace]
    python3 spec_manager.py show <session_id>
    python3 spec_manager.py context <session_id> [--max-chars 1200]
    python3 spec_manager.py clear <session_id>
    python3 spec_manager.py ensure-plan <session_id> [--force]
"""

from __future__ import print_function

import argparse
import json
import os
import time
from pathlib import Path


DEFAULT_STATE_DIR = Path.home() / ".tmux-monitor" / "spec"
STATE_DIR = Path(os.environ.get("AI_MONITOR_SPEC_DIR", str(DEFAULT_STATE_DIR)))


def _now_ts() -> int:
    return int(time.time())


def _spec_path(session_id: str) -> Path:
    safe = (session_id or "").strip()
    return STATE_DIR / (safe + ".json")


def _read_text_file(path: Path, max_chars: int = 8000) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        data = path.read_text(errors="replace")
    data = (data or "").strip()
    if max_chars > 0 and len(data) > max_chars:
        return data[:max_chars] + "\n... (truncated)"
    return data


def _load_spec(session_id: str) -> dict:
    path = _spec_path(session_id)
    if not path.exists():
        return {
            "session_id": session_id,
            "created_at": _now_ts(),
            "updated_at": _now_ts(),
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        try:
            return json.loads(path.read_text(errors="replace"))
        except Exception:
            # Corrupted file: start fresh but keep a trace.
            return {
                "session_id": session_id,
                "created_at": _now_ts(),
                "updated_at": _now_ts(),
                "error": "failed_to_parse_existing_spec",
            }


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _normalize_list(values) -> list:
    out = []
    for v in values or []:
        v = (v or "").strip()
        if not v:
            continue
        if v not in out:
            out.append(v)
    return out


def _merge_list(existing, incoming, replace: bool) -> list:
    if replace:
        return _normalize_list(incoming)
    merged = list(existing or [])
    for v in _normalize_list(incoming):
        if v not in merged:
            merged.append(v)
    return merged


def set_spec(
    session_id: str,
    goal: str = "",
    dod=None,
    constraints=None,
    out_of_scope=None,
    spec_file: str = "",
    replace: bool = False,
) -> dict:
    spec = _load_spec(session_id)
    spec["session_id"] = session_id
    spec.setdefault("created_at", _now_ts())

    if goal:
        spec["goal"] = goal.strip()

    spec["dod"] = _merge_list(spec.get("dod", []), dod or [], replace)
    spec["constraints"] = _merge_list(spec.get("constraints", []), constraints or [], replace)
    spec["out_of_scope"] = _merge_list(spec.get("out_of_scope", []), out_of_scope or [], replace)

    if spec_file:
        p = Path(spec_file).expanduser()
        spec["spec_file"] = str(p)
        if p.exists() and p.is_file():
            spec["spec_excerpt"] = _read_text_file(p, max_chars=4000)
        else:
            spec["spec_excerpt"] = ""
            spec["spec_file_error"] = "not_found_or_not_a_file"

    spec["updated_at"] = _now_ts()
    _atomic_write_json(_spec_path(session_id), spec)
    return spec


def clear_spec(session_id: str) -> bool:
    path = _spec_path(session_id)
    if not path.exists():
        return False
    # 删除是用户显式调用 clear 才会发生；这里不做额外“自动清理”。
    path.unlink()
    return True


def _format_bullets(items, max_items: int) -> str:
    items = _normalize_list(items)[:max_items]
    if not items:
        return ""
    return "\n".join("- " + i for i in items)


def build_context(session_id: str, max_chars: int = 1200) -> str:
    spec = _load_spec(session_id)
    goal = (spec.get("goal") or "").strip()
    dod = spec.get("dod") or []
    constraints = spec.get("constraints") or []
    out_of_scope = spec.get("out_of_scope") or []
    spec_file = (spec.get("spec_file") or "").strip()
    excerpt = (spec.get("spec_excerpt") or "").strip()

    if not goal and not dod and not constraints and not out_of_scope and not spec_file:
        return ""

    parts = []
    if goal:
        parts.append("[spec] Goal: " + goal)
    if dod:
        parts.append("[spec] DoD:\n" + _format_bullets(dod, max_items=6))
    if constraints:
        parts.append("[spec] Constraints:\n" + _format_bullets(constraints, max_items=6))
    if out_of_scope:
        parts.append("[spec] Out-of-scope:\n" + _format_bullets(out_of_scope, max_items=6))
    if spec_file:
        parts.append("[spec] Spec-File: " + spec_file)
        if excerpt:
            # 只取一个较短摘要，避免把大文档注入上下文
            excerpt_lines = [ln.rstrip() for ln in excerpt.splitlines() if ln.strip()]
            excerpt_preview = "\n".join(excerpt_lines[:12])
            if excerpt_preview:
                parts.append("[spec] Spec-Excerpt (head):\n" + excerpt_preview)

    text = "\n".join(parts).strip()
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + "\n... (truncated)"
    return text


def ensure_plan(session_id: str, force: bool = False) -> dict:
    """
    基于 spec.goal 创建/复用一个 active plan，并把 plan_id 回写到 spec 文件。
    """
    spec = _load_spec(session_id)
    goal = (spec.get("goal") or "").strip()

    # 缺省 goal：尝试从 intent 自动推导（减少手动步骤，适用于 Agent-of-Agent 模式）
    if not goal:
        try:
            from intent_parser import IntentParser  # noqa: E402

            parser = IntentParser()
            intent = parser.get_active_intent(session_id)
            if intent:
                action_zh = {
                    "implement": "实现",
                    "fix": "修复",
                    "refactor": "重构",
                    "test": "测试",
                    "deploy": "部署",
                    "config": "配置",
                    "doc": "文档",
                    "review": "审查",
                    "investigate": "调查",
                    "unknown": "处理",
                }.get(getattr(intent, "action", "unknown") or "unknown", "处理")

                target = (getattr(intent, "target", "") or "").strip()
                raw_text = (getattr(intent, "raw_text", "") or "").strip()

                derived_goal = ""
                if target:
                    derived_goal = (action_zh + " " + target).strip()
                elif raw_text:
                    derived_goal = raw_text

                if derived_goal:
                    spec["goal"] = derived_goal
                    if not _normalize_list(spec.get("dod") or []):
                        inferred = parser.infer_success_criteria(intent) or []
                        spec["dod"] = _normalize_list(inferred)
                    spec["autofilled_from_intent"] = {
                        "intent_id": getattr(intent, "intent_id", ""),
                        "confidence": float(getattr(intent, "confidence", 0.0) or 0.0),
                        "created_at": _now_ts(),
                    }
                    spec["updated_at"] = _now_ts()
                    _atomic_write_json(_spec_path(session_id), spec)
                    goal = derived_goal
        except Exception:
            pass

    if not goal:
        raise ValueError("spec.goal is required to generate a plan")

    existing_plan_id = ""
    plan_meta = spec.get("plan") or {}
    if isinstance(plan_meta, dict):
        existing_plan_id = (plan_meta.get("plan_id") or "").strip()

    if existing_plan_id and not force:
        return spec

    # Lazy import to keep this module standalone.
    from plan_generator import PlanGenerator  # noqa: E402

    generator = PlanGenerator()
    plan = generator.generate(
        session_id=session_id,
        goal=goal,
        constraints={
            "dod": _normalize_list(spec.get("dod") or []),
            "constraints": _normalize_list(spec.get("constraints") or []),
            "out_of_scope": _normalize_list(spec.get("out_of_scope") or []),
            "spec_file": (spec.get("spec_file") or "").strip(),
        },
    )
    generator.validate(plan.plan_id)

    spec["plan"] = {
        "plan_id": plan.plan_id,
        "created_at": _now_ts(),
    }
    spec["updated_at"] = _now_ts()
    _atomic_write_json(_spec_path(session_id), spec)
    return spec


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Claude Monitor Spec Manager")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    p_set = subparsers.add_parser("set", help="Set session spec (goal/DoD/constraints)")
    p_set.add_argument("session_id", help="Session ID")
    p_set.add_argument("--goal", default="", help="Goal / objective (required for plan generation)")
    p_set.add_argument("--dod", action="append", default=[], help="Definition of Done (repeatable)")
    p_set.add_argument("--constraint", action="append", default=[], help="Constraint (repeatable)")
    p_set.add_argument("--out-of-scope", action="append", default=[], help="Out of scope (repeatable)")
    p_set.add_argument("--spec-file", default="", help="Path to spec/requirements doc")
    p_set.add_argument("--replace", action="store_true", help="Replace list fields instead of merge")

    p_show = subparsers.add_parser("show", help="Show spec JSON")
    p_show.add_argument("session_id", help="Session ID")

    p_context = subparsers.add_parser("context", help="Render injected context string")
    p_context.add_argument("session_id", help="Session ID")
    p_context.add_argument("--max-chars", type=int, default=1200)

    p_clear = subparsers.add_parser("clear", help="Clear spec file for session")
    p_clear.add_argument("session_id", help="Session ID")

    p_plan = subparsers.add_parser("ensure-plan", help="Generate/activate plan for session spec.goal")
    p_plan.add_argument("session_id", help="Session ID")
    p_plan.add_argument("--force", action="store_true", help="Regenerate plan even if exists")

    args = parser.parse_args(argv)

    if args.command == "set":
        if not args.goal and not args.dod and not args.constraint and not args.out_of_scope and not args.spec_file:
            raise SystemExit("No fields provided. Use --goal/--dod/--constraint/--out-of-scope/--spec-file.")
        spec = set_spec(
            session_id=args.session_id,
            goal=args.goal,
            dod=args.dod,
            constraints=args.constraint,
            out_of_scope=args.out_of_scope,
            spec_file=args.spec_file,
            replace=args.replace,
        )
        print(json.dumps(spec, indent=2, ensure_ascii=False))
        return 0

    if args.command == "show":
        spec = _load_spec(args.session_id)
        print(json.dumps(spec, indent=2, ensure_ascii=False))
        return 0

    if args.command == "context":
        print(build_context(args.session_id, max_chars=args.max_chars))
        return 0

    if args.command == "clear":
        removed = clear_spec(args.session_id)
        print("cleared" if removed else "not_found")
        return 0

    if args.command == "ensure-plan":
        spec = ensure_plan(args.session_id, force=args.force)
        print(json.dumps(spec.get("plan") or {}, indent=2, ensure_ascii=False))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
