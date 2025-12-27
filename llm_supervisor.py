#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request


PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")
ROLES_MANIFEST = os.path.join(PROMPTS_DIR, "roles.json")
DEFAULT_ROLE = "monitor"
_FALLBACK_PROMPT = u"""你是一个"AI 监工/督导"，负责监管 Codex、Claude Code 等开发环境，确保它们在 tmux 面板里持续推进任务。

请根据近期输出（多行文本）决定是否要发送"一条单行命令"。你必须谨慎克制，除非明确需要，否则宁可回复 WAIT。

输出要求（非常重要）：
1) 只能输出一行纯文本，不要 Markdown、不要代码块、不要多余解释。
1.1) 推荐结构化输出（单行）：STAGE=<planning|coding|testing|fixing|refining|reviewing|documenting|release|done|blocked|waiting|unknown>; CMD=<WAIT 或可执行命令>。
2) 如果当前 AI 仍在执行、等待更多上下文、或者你并不确定下一步，输出：WAIT。
3) 遇到危险/破坏性操作（delete/remove/reset/drop/overwrite/force 等）必须输出：WAIT。
4) 若发现错误/失败/异常，请给出一句简洁指令，提醒它诊断并修复。
5) 只有在对下一步有明确、具体的指令时才输出该指令；避免使用"continue""keep going"这类空泛回复，除非输出里明确要求输入 continue。
6) 【重要】如果 [monitor-meta] same_response_count > 0，说明你之前的回复被重复发送了，请尝试不同的指令或输出 WAIT 等待更多信息。不要机械重复同一指令。
"""

_AGENT_APPENDIX = u"""
【Agent-of-Agent 约束（必须遵守）】
你可能会在输入里看到若干结构化块：
- [spec] ...：用户明确设定的 Goal / DoD / Constraints / Out-of-scope；这是最高优先级约束，必须严格遵守。
- [plan] ...：当前执行计划摘要（含 focus step）；优先推进 focus 所指的步骤，避免跑偏或做无关工作。
- [executor] ...：执行器回传的状态摘要（来自协议块），用来判断是否真的完成/被阻塞/下一步是什么。

执行策略（简化闭环）：
1) 若存在 [spec]，你的 CMD 必须朝向满足 DoD；若信息不足，先用最小命令获取关键上下文。
2) 若存在 [plan] 且有 focus step，优先给出推进该步骤的单行指令；若无法推进，输出 WAIT 并说明缺的关键信息。
3) 若启用了执行器协议但长时间未看到 [executor]，可指令执行器在输出末尾追加协议块（<<<AGENT_STATUS_JSON>>>...<<<END_AGENT_STATUS_JSON>>>），并在计划步骤状态变化时填写 plan_step.event/index/result。
"""


def _load_role_map():
    try:
        with open(ROLES_MANIFEST, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _load_role_prompt(role_name):
    role = (role_name or "monitor").strip().lower()
    role_file = os.path.join(PROMPTS_DIR, f"{role}.txt")
    if os.path.isfile(role_file):
        with open(role_file, "r", encoding="utf-8") as f:
            return f.read()
    available = ", ".join(sorted(_load_role_map().keys()))
    raise FileNotFoundError(
        "未找到角色提示词: %s。可用角色: %s" % (role, available or "（无，请检查 prompts/）")
    )


def _compose_auto_prompt():
    role_map = _load_role_map()
    if not role_map:
        return _FALLBACK_PROMPT

    role_lines = "\n".join(
        "- %s: %s" % (name, desc)
        for name, desc in sorted(role_map.items())
    )
    return u"""你是一名“AI 多角色指挥官”，负责根据 tmux 面板的最新输出为下游 AI 给出下一步指令。

在每次回复前，请先评估可用角色并选择最能解决当前问题的一位，然后站在该角色视角生成单行命令。必要时可短暂规划，但最终输出必须是单行文本。

可用角色：
%s

规则：
1) 无论选择哪个角色，都要遵循 SOLID / KISS / DRY / YAGNI，并优先考虑安全；
2) 如果任务仍在执行、上下文不足或需要更多信息，请输出 WAIT；
3) 严禁输出空泛的 continue/keep going，除非日志明确让你输入 continue；
4) 检测到危险操作（delete/remove/reset/drop/overwrite/force 等）时，必须返回 WAIT。
5) 推荐结构化输出（单行）：STAGE=<planning|coding|testing|fixing|refining|reviewing|documenting|release|done|blocked|waiting|unknown>; CMD=<WAIT 或可执行命令>。
6) 【重要】如果 [monitor-meta] same_response_count > 0，说明你之前的回复被重复发送了，请尝试不同的指令或输出 WAIT。不要机械重复。

附加上下文：
- 你会看到若干 `[monitor-meta] key: value` 行，其中包含 `stage / stage_history / last_response / same_response_count`；
- 结合 stage 判断当前研发进度（如 planning/coding/testing/release/done），再决定是继续推进、请求澄清、切换角色还是输出 WAIT；
- 如果 stage_history 显示已覆盖主要阶段或 same_response_count 持续增加，请优先考虑总结、测试或暂停，避免盲目继续。

输出：一行纯文本命令或 WAIT，不要 Markdown、不要多余解释。
""" % (role_lines,)


try:
    DEFAULT_SYSTEM_PROMPT = _load_role_prompt(DEFAULT_ROLE)
except Exception:
    DEFAULT_SYSTEM_PROMPT = _FALLBACK_PROMPT


def _normalize_base_url(base_url):
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return ""
    if base_url.endswith("/v1"):
        return base_url
    return base_url + "/v1"


def _read_stdin_text():
    # 直接读取原始字节，避免 sys.stdin.read() 自动解码失败
    data = sys.stdin.buffer.read()
    return data.decode("utf-8", "replace")


def _first_non_empty_line(text):
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _strip_fences_and_quotes(text):
    text = (text or "").strip()

    # Strip Markdown code fences (```lang ... ```) safely.
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Prefer structured output even if the model emits extra lines.
    stage_match = re.search(r"(?i)\bstage\s*[:=]\s*([a-z-]+)\b", text)
    cmd_match = re.search(r"(?i)\bcmd\s*[:=]\s*(.+)", text)
    if stage_match and cmd_match:
        stage = (stage_match.group(1) or "").strip().lower()
        cmd_raw = (cmd_match.group(1) or "").strip()
        cmd_line = _first_non_empty_line(cmd_raw)
        reconstructed = "STAGE=%s; CMD=%s" % (stage, cmd_line or "WAIT")
        return reconstructed.strip()

    # Otherwise, pick the first meaningful line.
    best_line = _first_non_empty_line(text)
    if len(best_line) >= 2 and best_line[0] == best_line[-1] and best_line[0] in ("'", '"', "`"):
        best_line = best_line[1:-1].strip()
    return best_line


def _chat_completions(base_url, api_key, model, system_prompt, user_content, timeout_s, max_tokens, temperature):
    if not base_url:
        raise ValueError("base_url is required")
    if not model:
        raise ValueError("model is required")

    url = _normalize_base_url(base_url) + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "n": 1,
    }

    req = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
        body = resp.read()
        if isinstance(body, bytes):
            body = body.decode("utf-8", "replace")
        return json.loads(body)


def main(argv):
    parser = argparse.ArgumentParser(description="LLM supervisor (OpenAI-compatible Chat Completions).")

    dashscope_api_key = os.environ.get("DASHSCOPE_API_KEY") or ""
    base_url_default = os.environ.get("AI_MONITOR_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or ""
    if not base_url_default and dashscope_api_key:
        base_url_default = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if not base_url_default:
        base_url_default = "https://api.openai.com/v1"

    api_key_default = os.environ.get("AI_MONITOR_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or dashscope_api_key or ""

    model_default = os.environ.get("AI_MONITOR_LLM_MODEL") or ""
    if not model_default and "dashscope.aliyuncs.com/compatible-mode" in base_url_default:
        model_default = "qwen-max"
    if not model_default:
        model_default = "gpt-4o-mini"

    role_default = os.environ.get("AI_MONITOR_LLM_ROLE") or DEFAULT_ROLE

    parser.add_argument("--base-url", default=base_url_default)
    parser.add_argument("--api-key", default=api_key_default)
    parser.add_argument("--model", default=model_default)
    parser.add_argument("--role", default=role_default)
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("AI_MONITOR_LLM_TIMEOUT") or "20"))
    parser.add_argument("--max-tokens", type=int, default=int(os.environ.get("AI_MONITOR_LLM_MAX_TOKENS") or "80"))
    parser.add_argument("--temperature", type=float, default=float(os.environ.get("AI_MONITOR_LLM_TEMPERATURE") or "0.5"))
    parser.add_argument("--same-response-count", type=int, default=0, help="用于动态调整 temperature")
    parser.add_argument("--system-prompt-file", default=os.environ.get("AI_MONITOR_LLM_SYSTEM_PROMPT_FILE") or "")
    args = parser.parse_args(argv)

    system_prompt = DEFAULT_SYSTEM_PROMPT
    requested_role = (args.role or role_default or DEFAULT_ROLE).strip()
    normalized_role = (requested_role or DEFAULT_ROLE).strip().lower()
    role_display = requested_role or normalized_role
    if normalized_role == "auto":
        role_display = "auto (LLM 自选角色)"

    if args.system_prompt_file:
        with open(args.system_prompt_file, "r", encoding="utf-8") as f:
            system_prompt = f.read()
    else:
        try:
            if normalized_role == "auto":
                system_prompt = _compose_auto_prompt()
            else:
                system_prompt = _load_role_prompt(normalized_role)
        except FileNotFoundError as e:
            sys.stderr.write("%s\n" % (e,))
            system_prompt = DEFAULT_SYSTEM_PROMPT

    # 统一追加 Agent-of-Agent 附录（可通过环境变量关闭）
    if os.environ.get("AI_MONITOR_PROMPT_APPENDIX_ENABLED", "1") != "0":
        if _AGENT_APPENDIX.strip() not in system_prompt:
            system_prompt = (system_prompt.rstrip() + u"\n\n" + _AGENT_APPENDIX.strip() + u"\n")

    pane_output = _read_stdin_text()
    user_content = u"当前角色: %s\n\n被监控 AI 最近输出如下（原样）：\n\n%s" % (role_display, pane_output)

    # 动态 Temperature：重复次数越多，随机性越高
    dynamic_temp = args.temperature
    if args.same_response_count > 0:
        # 每次重复增加 0.15，最高到 0.95
        dynamic_temp = min(0.95, args.temperature + args.same_response_count * 0.15)
        sys.stderr.write("[llm] Dynamic temperature: %.2f (base=%.2f, repeat=%d)\n" % (
            dynamic_temp, args.temperature, args.same_response_count
        ))

    data = _chat_completions(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        system_prompt=system_prompt,
        user_content=user_content,
        timeout_s=args.timeout,
        max_tokens=args.max_tokens,
        temperature=dynamic_temp,
    )

    choices = data.get("choices") or []
    if not choices:
        raise ValueError("no choices in response")

    choice0 = choices[0] or {}
    message = choice0.get("message") or {}
    content = message.get("content") or choice0.get("text") or ""
    content = _strip_fences_and_quotes(content)
    content = content.replace("\r", " ").strip()
    if not content:
        content = "WAIT"
    if len(content) > 400:
        content = content[:400].rstrip()

    print(content)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except urllib.error.HTTPError as e:
        sys.stderr.write("HTTPError: %s\n" % (e,))
        try:
            sys.stderr.write(e.read().decode("utf-8", "replace") + "\n")
        except Exception:
            pass
        sys.exit(2)
    except Exception as e:
        sys.stderr.write("Error: %s\n" % (e,))
        sys.exit(1)
