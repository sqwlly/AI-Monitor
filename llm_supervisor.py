#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


DEFAULT_SYSTEM_PROMPT = u"""你是一个“AI 监工/督导”，负责监管 Codex、Claude Code 等开发环境，确保它们在 tmux 面板里持续推进任务。

请根据近期输出（多行文本）决定是否要发送“一条单行命令”。你必须谨慎克制，除非明确需要，否则宁可回复 WAIT。

输出要求（非常重要）：
1) 只能输出一行纯文本，不要 Markdown、不要代码块、不要多余解释。
2) 如果当前 AI 仍在执行、等待更多上下文、或者你并不确定下一步，输出：WAIT。
3) 遇到危险/破坏性操作（delete/remove/reset/drop/overwrite/force 等）必须输出：WAIT。
4) 若发现错误/失败/异常，请给出一句简洁指令，提醒它诊断并修复。
5) 只有在对下一步有明确、具体的指令时才输出该指令；避免使用“continue”“keep going”这类空泛回复，除非输出里明确要求输入 continue。
"""


def _normalize_base_url(base_url):
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return ""
    if base_url.endswith("/v1"):
        return base_url
    return base_url + "/v1"


def _read_stdin_text():
    data = sys.stdin.read()
    if isinstance(data, bytes):
        try:
            return data.decode("utf-8", "replace")
        except Exception:
            return data.decode(errors="replace")
    return data


def _first_non_empty_line(text):
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _strip_fences_and_quotes(text):
    text = (text or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 3:
            text = "\n".join(parts[1:-1]).strip()
    text = _first_non_empty_line(text)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"', "`"):
        text = text[1:-1].strip()
    return text


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

    parser.add_argument("--base-url", default=base_url_default)
    parser.add_argument("--api-key", default=api_key_default)
    parser.add_argument("--model", default=model_default)
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("AI_MONITOR_LLM_TIMEOUT") or "20"))
    parser.add_argument("--max-tokens", type=int, default=int(os.environ.get("AI_MONITOR_LLM_MAX_TOKENS") or "80"))
    parser.add_argument("--temperature", type=float, default=float(os.environ.get("AI_MONITOR_LLM_TEMPERATURE") or "0.2"))
    parser.add_argument("--system-prompt-file", default=os.environ.get("AI_MONITOR_LLM_SYSTEM_PROMPT_FILE") or "")
    args = parser.parse_args(argv)

    system_prompt = DEFAULT_SYSTEM_PROMPT
    if args.system_prompt_file:
        with open(args.system_prompt_file, "r", encoding="utf-8") as f:
            system_prompt = f.read()

    pane_output = _read_stdin_text()
    user_content = u"被监控 AI 最近输出如下（原样）：\n\n" + pane_output

    data = _chat_completions(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        system_prompt=system_prompt,
        user_content=user_content,
        timeout_s=args.timeout,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
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
