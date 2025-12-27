# 执行器协议（Agent-of-Agent）

目标：让被监控的执行器（例如 Codex / Claude Code）以稳定、可解析的格式回传“状态”，从而让本项目可以：

- 可靠地判断当前阶段、已完成内容、下一步、阻塞点
- 在启用 plan（`plan_generator.py`）时，基于执行器回传自动推进步骤状态（started/completed/blocked/skipped）

## 推荐协议：JSON 状态块

在每次完成一个可验证动作后，在输出末尾追加下面的块（原样输出）：

```
<<<AGENT_STATUS_JSON>>>
{"stage":"coding","done":"实现了 xxx","next":"运行测试并贴失败摘要","blockers":[],"plan_step":{"event":"none|started|completed|blocked|skipped","index":0,"result":"可选：结果/原因/摘要"}}
<<<END_AGENT_STATUS_JSON>>>
```

### 字段说明

- `stage`：`planning|coding|testing|fixing|refining|reviewing|documenting|release|done|blocked|waiting|unknown`
- `done`：一句话概述本轮已完成
- `next`：一句话说明下一步打算做什么
- `blockers`：阻塞点数组（没有就 `[]`）
- `plan_step`（可选但强烈建议）：
  - `event`：`none|started|completed|blocked|skipped`
  - `index`：步骤索引（从 0 开始）
  - `result`：结果摘要（完成/阻塞原因/跳过原因）

## 与本项目的集成点

- 启用协议：
  - 启动监控时加 `--with-protocol`（`cm run <target> --with-protocol ...`）
  - 监工会在空闲时向执行器发送一次“协议握手”提示
- 解析与注入：
  - `smart-monitor.sh` 会在输出变化时调用 `executor_protocol.py update <session_id> ...`
  - 在构建 LLM 决策上下文时，会注入 `executor_protocol.py summary` 的一行摘要

## 最佳实践

- 每次只输出一个最新状态块（或确保最新块在输出末尾）
- `done/next` 尽量短（1 句话），避免长段解释
- 遇到需要人工确认/高风险操作时，把风险写进 `blockers` 或 `plan_step.result`，并暂停等待指示

