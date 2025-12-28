#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Agent Orchestrator
多Agent编排系统 - 投票模式实现（含Token优化）

Usage:
    python3 agent_orchestrator.py run --context <context> [--pipeline vote|default] [--stage <stage>]
    python3 agent_orchestrator.py list-pipelines
    python3 agent_orchestrator.py config show

Pipeline Modes:
    default  - 单Agent模式（使用默认监工）
    vote     - 投票模式（并行调用多个Agent，多数投票决策）
    tiered   - 分层模式（快速预判 + 按需完整分析）

Environment Variables:
    AI_MONITOR_ORCHESTRATOR_ENABLED  - 启用编排器 (0/1)
    AI_MONITOR_PIPELINE              - 默认流水线 (default/vote/tiered)
    AI_MONITOR_PIPELINE_CONFIG       - 流水线配置文件路径
    AI_MONITOR_TOKEN_OPTIMIZE        - 启用token优化 (0/1, 默认1)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

try:
    from typing import Optional, List, Dict, Any, Tuple
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
except ImportError:
    pass

# 默认配置
DEFAULT_CONFIG_DIR = Path.home() / ".tmux-monitor" / "config"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "pipelines.json"
SCRIPT_DIR = Path(__file__).parent

# 环境变量
CONFIG_PATH = Path(os.environ.get("AI_MONITOR_PIPELINE_CONFIG", str(DEFAULT_CONFIG_PATH)))
ORCHESTRATOR_ENABLED = os.environ.get("AI_MONITOR_ORCHESTRATOR_ENABLED", "0") == "1"
DEFAULT_PIPELINE = os.environ.get("AI_MONITOR_PIPELINE", "tiered")  # 默认使用分层模式
TOKEN_OPTIMIZE_ENABLED = os.environ.get("AI_MONITOR_TOKEN_OPTIMIZE", "1") == "1"

# Token优化组件（延迟导入）
_tiered_executor = None
_output_filter = None
_response_cache = None


def _get_tiered_executor():
    global _tiered_executor
    if _tiered_executor is None:
        try:
            from token_optimizer import get_tiered_executor
            _tiered_executor = get_tiered_executor()
        except ImportError:
            _tiered_executor = False
    return _tiered_executor if _tiered_executor else None


def _get_output_filter():
    global _output_filter
    if _output_filter is None:
        try:
            from token_optimizer import get_output_filter
            _output_filter = get_output_filter()
        except ImportError:
            _output_filter = False
    return _output_filter if _output_filter else None


def _get_response_cache():
    global _response_cache
    if _response_cache is None:
        try:
            from token_optimizer import get_response_cache
            _response_cache = get_response_cache()
        except ImportError:
            _response_cache = False
    return _response_cache if _response_cache else None


class AgentResponse:
    """Agent 响应"""
    def __init__(self, agent_id, role, response, stage_hint=None, latency_ms=0, error=None):
        self.agent_id = agent_id
        self.role = role
        self.response = response
        self.stage_hint = stage_hint
        self.latency_ms = latency_ms
        self.error = error
        self.is_wait = response.upper() == 'WAIT' if response else True

    def to_dict(self):
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "response": self.response,
            "stage_hint": self.stage_hint,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "is_wait": self.is_wait
        }


class Agent:
    """单个Agent封装"""

    def __init__(self, config):
        self.id = config.get('id', 'agent-1')
        self.role = config.get('role', 'monitor')
        self.model = config.get('model')  # 可选，覆盖默认模型
        self.priority = config.get('priority', 50)
        self.enabled = config.get('enabled', True)

    def invoke(self, context, timeout=15):
        """调用 llm_supervisor.py 获取响应"""
        if not self.enabled:
            return AgentResponse(self.id, self.role, None, error="Agent disabled")

        start_time = time.time()

        try:
            # 构建命令
            cmd = [
                sys.executable,
                str(SCRIPT_DIR / "llm_supervisor.py"),
                "--role", self.role
            ]

            if self.model:
                cmd.extend(["--model", self.model])

            # 调用 llm_supervisor.py
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(SCRIPT_DIR)
            )

            stdout, stderr = proc.communicate(
                input=context.encode('utf-8'),
                timeout=timeout
            )

            latency_ms = int((time.time() - start_time) * 1000)

            if proc.returncode != 0:
                return AgentResponse(
                    self.id, self.role, None,
                    latency_ms=latency_ms,
                    error="Exit code: {}".format(proc.returncode)
                )

            # 解析响应
            response = stdout.decode('utf-8').strip()
            stage_hint = None

            # 检查是否有 STAGE= 前缀
            if response.startswith("STAGE="):
                parts = response.split(";", 1)
                if len(parts) >= 2:
                    stage_hint = parts[0].replace("STAGE=", "").strip()
                    response = parts[1].replace("CMD=", "").strip()

            return AgentResponse(
                self.id, self.role, response,
                stage_hint=stage_hint,
                latency_ms=latency_ms
            )

        except subprocess.TimeoutExpired:
            proc.kill()
            return AgentResponse(
                self.id, self.role, None,
                latency_ms=int((time.time() - start_time) * 1000),
                error="Timeout after {}s".format(timeout)
            )
        except Exception as e:
            return AgentResponse(
                self.id, self.role, None,
                latency_ms=int((time.time() - start_time) * 1000),
                error=str(e)
            )


class Pipeline:
    """Agent流水线"""

    def __init__(self, config):
        self.name = config.get('name', 'default')
        self.mode = config.get('mode', 'single')  # single/vote/sequential/tiered
        self.agents = [Agent(a) for a in config.get('agents', [])]
        self.timeout = config.get('timeout_per_agent_s', 15)

    def execute(self, context):
        """执行流水线"""
        # Token优化：先过滤输出
        if TOKEN_OPTIMIZE_ENABLED:
            output_filter = _get_output_filter()
            if output_filter:
                context = output_filter.filter(context)
                context = output_filter.fold_repetitive(context)

        if self.mode == 'tiered':
            return self._execute_tiered(context)
        elif self.mode == 'vote':
            return self._execute_vote(context)
        elif self.mode == 'sequential':
            return self._execute_sequential(context)
        else:  # single
            return self._execute_single(context)

    def _execute_tiered(self, context):
        """分层模式：快速预判 + 按需完整分析"""
        # 1. 尝试快速分类
        if TOKEN_OPTIMIZE_ENABLED:
            tiered = _get_tiered_executor()
            if tiered:
                need_full, quick_response = tiered.should_invoke_full_agent(context)
                if not need_full and quick_response:
                    return PipelineResult(
                        [],
                        quick_response,
                        "Tiered: quick classification (no LLM call)"
                    )

        # 2. 检查缓存
        if TOKEN_OPTIMIZE_ENABLED and self.agents:
            cache = _get_response_cache()
            if cache:
                cached = cache.get(context, self.agents[0].role)
                if cached:
                    response, stage = cached
                    return PipelineResult(
                        [],
                        response,
                        "Tiered: cache hit"
                    )

        # 3. 需要完整分析，使用单agent模式
        result = self._execute_single(context)

        # 4. 缓存结果
        if TOKEN_OPTIMIZE_ENABLED and result.final_response:
            cache = _get_response_cache()
            if cache and self.agents:
                cache.set(context, self.agents[0].role, result.final_response)

        return result

    def _execute_single(self, context):
        """单Agent模式"""
        if not self.agents:
            return PipelineResult([], "WAIT", "No agents configured")

        agent = self.agents[0]
        response = agent.invoke(context, self.timeout)

        return PipelineResult(
            [response],
            response.response or "WAIT",
            "Single agent: {}".format(agent.role)
        )

    def _execute_sequential(self, context):
        """串行模式：依次调用，第一个非WAIT响应胜出"""
        responses = []

        for agent in self.agents:
            if not agent.enabled:
                continue

            response = agent.invoke(context, self.timeout)
            responses.append(response)

            if not response.is_wait and not response.error:
                return PipelineResult(
                    responses,
                    response.response,
                    "Sequential winner: {} ({})".format(agent.id, agent.role)
                )

        # 所有都是WAIT
        return PipelineResult(
            responses,
            "WAIT",
            "All agents returned WAIT"
        )

    def _execute_vote(self, context):
        """投票模式：并行调用，多数投票"""
        if not self.agents:
            return PipelineResult([], "WAIT", "No agents configured")

        enabled_agents = [a for a in self.agents if a.enabled]
        if not enabled_agents:
            return PipelineResult([], "WAIT", "No enabled agents")

        responses = []

        # 并行执行
        try:
            with ThreadPoolExecutor(max_workers=len(enabled_agents)) as executor:
                futures = {
                    executor.submit(a.invoke, context, self.timeout): a
                    for a in enabled_agents
                }

                for future in as_completed(futures, timeout=self.timeout + 5):
                    try:
                        response = future.result()
                        responses.append(response)
                    except Exception as e:
                        agent = futures[future]
                        responses.append(AgentResponse(
                            agent.id, agent.role, None, error=str(e)
                        ))

        except TimeoutError:
            pass  # 部分超时也继续

        # 投票聚合
        final_response, reason = self._aggregate_vote(responses)

        return PipelineResult(responses, final_response, reason)

    def _aggregate_vote(self, responses):
        """投票聚合逻辑"""
        # 过滤有效响应（非WAIT且无错误）
        valid_responses = [
            r for r in responses
            if r.response and not r.is_wait and not r.error
        ]

        if not valid_responses:
            # 所有都是WAIT或错误
            wait_count = sum(1 for r in responses if r.is_wait)
            error_count = sum(1 for r in responses if r.error)
            return "WAIT", "No valid responses (WAIT:{}, Error:{})".format(wait_count, error_count)

        # 统计投票
        votes = Counter(r.response for r in valid_responses)
        most_common = votes.most_common(1)[0]
        winner_response = most_common[0]
        winner_count = most_common[1]

        # 找到获胜者的agent信息
        winners = [r for r in valid_responses if r.response == winner_response]
        winner_roles = [r.role for r in winners]

        return winner_response, "Vote: {} ({}/{} agents: {})".format(
            winner_response[:30],
            winner_count,
            len(responses),
            ", ".join(winner_roles)
        )


class PipelineResult:
    """流水线执行结果"""

    def __init__(self, responses, final_response, reason):
        self.responses = responses
        self.final_response = final_response
        self.reason = reason
        self.timestamp = int(time.time())

    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "final_response": self.final_response,
            "reason": self.reason,
            "agent_count": len(self.responses),
            "responses": [r.to_dict() for r in self.responses]
        }


class Orchestrator:
    """编排调度器"""

    def __init__(self, config_path=None):
        self.config_path = config_path or CONFIG_PATH
        self.pipelines = self._load_pipelines()

    def _load_pipelines(self):
        """加载流水线配置"""
        if self.config_path.exists():
            try:
                with open(str(self.config_path), 'r') as f:
                    config = json.load(f)
                    return {
                        name: Pipeline(cfg)
                        for name, cfg in config.items()
                    }
            except Exception as e:
                print("[orchestrator] Error loading config: {}".format(e), file=sys.stderr)

        return self._get_default_pipelines()

    def _get_default_pipelines(self):
        """获取默认流水线配置"""
        default_config = {
            "default": {
                "name": "single-monitor",
                "mode": "single",
                "agents": [{"id": "monitor-1", "role": "monitor"}],
                "timeout_per_agent_s": 15
            },
            "tiered": {
                "name": "tiered-optimized",
                "mode": "tiered",
                "agents": [{"id": "monitor-1", "role": "monitor"}],
                "timeout_per_agent_s": 15
            },
            "vote": {
                "name": "multi-vote",
                "mode": "vote",
                "agents": [
                    {"id": "arch-1", "role": "architect", "priority": 80},
                    {"id": "eng-1", "role": "senior-engineer", "priority": 70},
                    {"id": "test-1", "role": "test-manager", "priority": 60}
                ],
                "timeout_per_agent_s": 15
            },
            "sequential": {
                "name": "sequential-fallback",
                "mode": "sequential",
                "agents": [
                    {"id": "eng-1", "role": "senior-engineer", "priority": 100},
                    {"id": "arch-1", "role": "architect", "priority": 50},
                    {"id": "mon-1", "role": "monitor", "priority": 30}
                ],
                "timeout_per_agent_s": 15
            }
        }

        return {name: Pipeline(cfg) for name, cfg in default_config.items()}

    def select_pipeline(self, pipeline_name=None, stage=None):
        """选择流水线"""
        name = pipeline_name or DEFAULT_PIPELINE

        # 基于阶段的自动选择（可扩展）
        if name == "auto" and stage:
            stage_mapping = {
                "reviewing": "vote",
                "testing": "sequential",
            }
            name = stage_mapping.get(stage, "default")

        return self.pipelines.get(name, self.pipelines.get('default'))

    def run(self, context, pipeline_name=None, stage=None):
        """主入口：选择流水线并执行"""
        pipeline = self.select_pipeline(pipeline_name, stage)
        if not pipeline:
            return PipelineResult([], "WAIT", "No pipeline found")

        return pipeline.execute(context)

    def run_with_primary_role(self, context, pipeline_name=None, stage=None, primary_role=None):
        """
        主入口（增强版）：在所选流水线基础上插入一个“主角色”Agent。
        目的：当外部显式选择了 persona（如 ui-designer）时，即使启用 vote/sequential 也能让该 persona 参与决策。
        """
        pipeline = self.select_pipeline(pipeline_name, stage)
        if not pipeline:
            return PipelineResult([], "WAIT", "No pipeline found")

        role = (primary_role or "").strip().lower()
        if not role:
            return pipeline.execute(context)

        agents_cfg = []
        agents_cfg.append(
            {
                "id": "primary-{}".format(role.replace(" ", "_")[:24] or "role"),
                "role": role,
                "priority": 100,
                "enabled": True,
            }
        )

        for a in pipeline.agents:
            a_role = (getattr(a, "role", "") or "").strip().lower()
            if a_role == role:
                continue
            agents_cfg.append(
                {
                    "id": getattr(a, "id", None) or "agent",
                    "role": getattr(a, "role", None) or "monitor",
                    "model": getattr(a, "model", None),
                    "priority": getattr(a, "priority", 50),
                    "enabled": getattr(a, "enabled", True),
                }
            )

        injected = Pipeline(
            {
                "name": "{}+primary({})".format(pipeline.name, role),
                "mode": pipeline.mode,
                "agents": agents_cfg,
                "timeout_per_agent_s": pipeline.timeout,
            }
        )
        return injected.execute(context)

    def list_pipelines(self):
        """列出所有流水线"""
        result = []
        for name, pipeline in self.pipelines.items():
            result.append({
                "name": name,
                "mode": pipeline.mode,
                "agents": len(pipeline.agents),
                "timeout": pipeline.timeout
            })
        return result

    def save_config(self):
        """保存配置"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        config = {}
        for name, pipeline in self.pipelines.items():
            config[name] = {
                "name": pipeline.name,
                "mode": pipeline.mode,
                "agents": [
                    {
                        "id": a.id,
                        "role": a.role,
                        "model": a.model,
                        "priority": a.priority,
                        "enabled": a.enabled
                    }
                    for a in pipeline.agents
                ],
                "timeout_per_agent_s": pipeline.timeout
            }

        with open(str(self.config_path), 'w') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)


def _read_stdin_context() -> str:
    data = sys.stdin.buffer.read()
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Agent Orchestrator',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # run
    p_run = subparsers.add_parser('run', help='Run orchestrator')
    p_run.add_argument('--context', help='Context string (or read from stdin)')
    p_run.add_argument('--pipeline', default=DEFAULT_PIPELINE, help='Pipeline name')
    p_run.add_argument('--stage', help='Current stage (for auto pipeline selection)')
    p_run.add_argument('--primary-role', help='Inject a primary role agent into selected pipeline')
    p_run.add_argument('--output', choices=['full', 'response'], default='response',
                      help='Output format')

    # list-pipelines
    subparsers.add_parser('list-pipelines', help='List available pipelines')

    # config
    p_config = subparsers.add_parser('config', help='Manage configuration')
    config_sub = p_config.add_subparsers(dest='config_cmd')
    config_sub.add_parser('show', help='Show current config')
    config_sub.add_parser('init', help='Create default config file')

    args = parser.parse_args(argv)

    orchestrator = Orchestrator()

    try:
        if args.command == 'run':
            # 获取上下文
            if args.context:
                context = args.context
            else:
                context = _read_stdin_context()

            # 执行
            if args.primary_role:
                result = orchestrator.run_with_primary_role(context, args.pipeline, args.stage, args.primary_role)
            else:
                result = orchestrator.run(context, args.pipeline, args.stage)

            if args.output == 'full':
                print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
            else:
                # 只输出最终响应（兼容现有脚本）
                print(result.final_response)

        elif args.command == 'list-pipelines':
            pipelines = orchestrator.list_pipelines()
            for p in pipelines:
                print("{}: mode={}, agents={}, timeout={}s".format(
                    p['name'], p['mode'], p['agents'], p['timeout']
                ))

        elif args.command == 'config':
            if args.config_cmd == 'show':
                # 显示当前配置
                pipelines = orchestrator.list_pipelines()
                print(json.dumps(pipelines, indent=2, ensure_ascii=False))

            elif args.config_cmd == 'init':
                orchestrator.save_config()
                print("Default config created at: {}".format(orchestrator.config_path))

            else:
                p_config.print_help()

        else:
            parser.print_help()
            return 1

    except Exception as e:
        print("Error: {}".format(e), file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
