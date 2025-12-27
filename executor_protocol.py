#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Executor Protocol (Agent-of-Agent)
解析被监控执行器（Codex / Claude Code）输出中的状态块，并用于闭环推进：
- 让执行器以稳定格式回传：当前阶段/已完成/下一步/阻塞/计划步骤事件
- 监工侧可据此更新 plan_generator 的 step 状态（start/complete/block/skip）

协议（推荐 JSON 块）：
<<<AGENT_STATUS_JSON>>>
{"stage":"coding","done":"...","next":"...","blockers":[],"plan_step":{"event":"none|started|completed|blocked|skipped","index":0,"result":"..."}}
<<<END_AGENT_STATUS_JSON>>>

Usage:
    # 从 stdin 提取并输出一行摘要（用于注入 LLM 上下文）
    python3 executor_protocol.py summary

    # 从文本中解析并更新会话状态（计划 + 记忆；文本可从 argv 或 stdin 读取）
    python3 executor_protocol.py update <session_id> [text]
"""

from __future__ import print_function

import argparse
import json
import sys
from typing import Any, Dict, Optional


START_MARKER = "<<<AGENT_STATUS_JSON>>>"
END_MARKER = "<<<END_AGENT_STATUS_JSON>>>"


def _read_stdin_text() -> str:
    data = sys.stdin.buffer.read()
    try:
        return data.decode("utf-8", "replace")
    except Exception:
        return (data or b"").decode(errors="replace")


def _extract_latest_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None

    start = text.rfind(START_MARKER)
    if start < 0:
        return None
    end = text.find(END_MARKER, start + len(START_MARKER))
    if end < 0:
        # 容错：capture-lines 可能截断了 END_MARKER，尽量从剩余文本中解析出 JSON。
        end = len(text)

    payload = text[start + len(START_MARKER):end].strip()
    if not payload:
        return None

    # 容错：去掉可能的 Markdown fence
    if payload.startswith("```"):
        lines = payload.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        payload = "\n".join(lines).strip()

    payload_candidates = []
    first_obj = _extract_first_json_object(payload)
    if first_obj:
        payload_candidates.append(first_obj)
    payload_candidates.append(payload)

    for candidate in payload_candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue

    return None


def _extract_first_json_object(text: str) -> Optional[str]:
    """
    从文本中提取第一个“看起来完整”的 JSON object（{}）。
    设计目标：在 END_MARKER 缺失或 JSON 后跟随其它输出时仍尽量可解析。
    """
    if not text:
        return None

    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_str = False
    escape = False

    for idx in range(start, len(text)):
        ch = text[idx]
        if in_str:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == "\"":
                in_str = False
                continue
            continue

        if ch == "\"":
            in_str = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]

    return None


def _one_line(text: str, max_len: int = 120) -> str:
    t = (text or "").replace("\n", " ").replace("\r", " ").strip()
    if max_len > 0 and len(t) > max_len:
        return t[:max_len] + "..."
    return t


def build_summary(status: Dict[str, Any]) -> str:
    if not status:
        return ""

    stage = _one_line(str(status.get("stage", "") or ""), max_len=30)
    done = _one_line(str(status.get("done", "") or ""), max_len=120)
    next_action = _one_line(str(status.get("next", "") or ""), max_len=120)
    blockers = status.get("blockers") or []
    if isinstance(blockers, str):
        blockers = [blockers]
    blockers_text = _one_line("; ".join([str(b) for b in blockers if str(b).strip()]), max_len=160)

    parts = []
    if stage:
        parts.append(f"stage={stage}")
    if done:
        parts.append(f"done={done}")
    if next_action:
        parts.append(f"next={next_action}")
    if blockers_text:
        parts.append(f"blockers={blockers_text}")

    if not parts:
        return ""
    return "[executor] " + " | ".join(parts)


def _update_plan_from_status(session_id: str, status: Dict[str, Any]) -> None:
    plan_step = status.get("plan_step") or status.get("plan") or {}
    if not isinstance(plan_step, dict):
        return

    event = (plan_step.get("event") or "").strip().lower()
    if not event or event == "none":
        return

    try:
        step_index = int(plan_step.get("index"))
    except Exception:
        return

    result = str(plan_step.get("result") or "").strip()

    # Lazy import to avoid importing sqlite-heavy modules on summary path.
    from plan_generator import PlanGenerator  # noqa: E402

    generator = PlanGenerator()
    active = generator.get_active_plans(session_id)
    if not active:
        return

    plan = active[0]

    if event == "started":
        generator.start_step(plan.plan_id, step_index)
        return
    if event == "completed":
        generator.complete_step(plan.plan_id, step_index, result=result, success=True)
        return
    if event == "blocked":
        generator.block_step(plan.plan_id, step_index, reason=result or "blocked")
        return
    if event == "skipped":
        generator.skip_step(plan.plan_id, step_index, reason=result or "skipped")
        return


def _record_memory(session_id: str, status: Dict[str, Any]) -> None:
    """
    将关键字段写入 working_memory，便于后续上下文注入（阻塞/错误优先）。
    """
    blockers = status.get("blockers") or []
    if isinstance(blockers, str):
        blockers = [blockers]
    blockers = [str(b).strip() for b in blockers if str(b).strip()]

    # Lazy import: working_memory.py 会创建表（已在项目内既有使用）
    from working_memory import WorkingMemory, MemoryType, Importance  # noqa: E402

    wm = WorkingMemory()
    for b in blockers[:5]:
        wm.add(session_id=session_id, memory_type=MemoryType.BLOCKER, content=b, importance=Importance.HIGH)

    summary = build_summary(status)
    if summary:
        wm.add(session_id=session_id, memory_type=MemoryType.CONTEXT, content=summary, importance=Importance.LOW)


def cmd_summary() -> int:
    text = _read_stdin_text()
    status = _extract_latest_json(text)
    if not status:
        return 0
    out = build_summary(status)
    if out:
        print(out)
    return 0


def cmd_extract() -> int:
    """
    从 stdin 的最新状态块提取 stage 与 plan_step.event，并分别以两行输出：
    - 第 1 行：stage（小写；若缺失则空行）
    - 第 2 行：event（小写；若 blockers 非空则强制输出 blocked）
    """
    text = _read_stdin_text()
    status = _extract_latest_json(text)
    if not status:
        return 0

    stage = str(status.get("stage", "") or "").strip().lower()
    plan_step = status.get("plan_step") or status.get("plan") or {}
    if not isinstance(plan_step, dict):
        plan_step = {}
    event = str(plan_step.get("event", "") or "").strip().lower()

    blockers = status.get("blockers") or []
    if isinstance(blockers, str):
        blockers = [blockers]
    blockers = [str(b).strip() for b in blockers if str(b).strip()]
    if blockers:
        event = "blocked"

    print(stage)
    print(event)
    return 0


def cmd_update(session_id: str, text: str) -> int:
    status = _extract_latest_json(text)
    if not status:
        return 0

    try:
        _update_plan_from_status(session_id, status)
    except Exception:
        # 更新失败不应影响主循环
        pass

    try:
        _record_memory(session_id, status)
    except Exception:
        pass

    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Executor protocol parser")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    subparsers.add_parser("summary", help="Parse latest status block from stdin and print summary")
    subparsers.add_parser("extract", help="Extract stage and plan_step.event from latest status block in stdin")

    p_update = subparsers.add_parser("update", help="Update session state from output text")
    p_update.add_argument("session_id", help="Session ID")
    p_update.add_argument("text", nargs="?", default="", help="Output text (or read from stdin)")

    args = parser.parse_args(argv)

    if args.command == "summary":
        return cmd_summary()

    if args.command == "extract":
        return cmd_extract()

    if args.command == "update":
        text = args.text
        if not text:
            text = _read_stdin_text()
        return cmd_update(args.session_id, text)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
