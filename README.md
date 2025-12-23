# AI Monitor（Codex / Claude 监工）

一个基于 `tmux` 的“AI 监工/保活”小工具：持续读取目标面板的输出，在空闲时自动向该面板发送一条回复，让面板里的 AI（如 OpenAI Codex TUI、Claude Code、Cursor 等）继续推进任务。

## 功能

- **LLM 监工**：把最近输出交给另一个模型（OpenAI 兼容接口）生成“单行回复”，并发送回目标面板
- **支持千问（Qwen）**：可用本地 Ollama（OpenAI 兼容接口）运行 `qwen2.5*` 作为监工模型
- **安全默认**：检测到“进行中/危险确认”时输出 `WAIT`，避免自动确认破坏性操作

## 依赖

- `tmux`
- `bash`
- `python3`：用于调用 OpenAI 兼容接口

## 快速开始

1) 列出可监控的面板：

```bash
./claude-monitor list
```

2) 启动 LLM 监工监控：

```bash
./claude-monitor run "2:mon.0" --model "gpt-4o-mini"
```

3) 查看状态 / 跟踪日志：

```bash
./claude-monitor status
./claude-monitor tail "2:mon.0"
```

4) 停止：

```bash
./claude-monitor stop "2:mon.0"
./claude-monitor stop   # 停止所有
```

5) 想直接进入被监控的 tmux 面板进行人工干预，可先附着会话再选窗口/面板：

```bash
tmux attach -t 2        # 进入会话 2（或使用名字）
Ctrl+b w                # 在 tmux 中列出窗口，选择 mon
Ctrl+b q                # 显示面板编号，选择目标面板
```

如需重新回到背景运行的监控，只需在 tmux 中 `Ctrl+b d` 即可 detach。

## 安装成命令（建议）

把 `claude-monitor` 放进你的 `PATH`（推荐软链接到 `~/.local/bin`）。你也可以安装成更短的命令名（例如 `cm`）：

```bash
./claude-monitor install cm
```

确保 `~/.local/bin` 在 `PATH` 中，然后即可在任意目录使用：

```bash
cm ls
cm              # 交互选择并启动 run
cm "2:mon.0"    # 直接对这个 target 启动 run
```

## LLM 监工配置

### 1) 使用 OpenAI（示例）

```bash
export OPENAI_API_KEY="***"
cm "2:mon.0" --model "gpt-4o-mini"
```

可选参数：

- `--base-url <url>`：默认 `https://api.openai.com/v1`
- `--api-key <key>`：默认读取 `AI_MONITOR_LLM_API_KEY` 或 `OPENAI_API_KEY`
- `--timeout <sec>`
- `--system-prompt-file <file>`：覆盖默认监工提示词

### 2) 使用云端千问（DashScope OpenAI 兼容模式）

如果你的千问 Key 来自阿里云 DashScope（灵积），可以直接按 OpenAI 兼容模式配置：

```bash
export DASHSCOPE_API_KEY="***"
export AI_MONITOR_LLM_MODEL="qwen-max"
cm "2:mon.0"
```

可选：先快速测试连通性（不启动监控）：

```bash
cm test
```

说明：

- 本项目会在检测到 `DASHSCOPE_API_KEY` 且未设置 `AI_MONITOR_LLM_BASE_URL` 时，自动使用 `https://dashscope.aliyuncs.com/compatible-mode/v1`
- 你也可以显式指定（等价）：
  - `export AI_MONITOR_LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"`

### 3) 使用本地 Ollama（示例）

Ollama 提供 OpenAI 兼容接口（通常是 `http://localhost:11434/v1`）。示例：

```bash
cm "2:mon.0" --base-url "http://localhost:11434/v1" --model "qwen2.5:7b-instruct"
```

这也是使用 **千问（Qwen）** 做“监工回复”的最简单方式（本地跑模型，不依赖云端）。

如果你想把命令再缩短一点，建议把 LLM 参数写到环境变量里：

```bash
export AI_MONITOR_LLM_BASE_URL="http://localhost:11434/v1"
export AI_MONITOR_LLM_MODEL="qwen2.5:7b-instruct"
cm "2:mon.0"
```

### 环境变量（可选）

`llm_supervisor.py` 支持以下环境变量：

- `AI_MONITOR_LLM_BASE_URL`
- `AI_MONITOR_LLM_API_KEY`
- `AI_MONITOR_LLM_MODEL`
- `DASHSCOPE_API_KEY`（可选：用于云端千问，且可触发默认 base-url）
- `AI_MONITOR_LLM_TIMEOUT`
- `AI_MONITOR_LLM_MAX_TOKENS`
- `AI_MONITOR_LLM_TEMPERATURE`
- `AI_MONITOR_LLM_SYSTEM_PROMPT_FILE`

日志控制（可选）：

- `AI_MONITOR_LOG_MAX_BYTES`：单个日志文件最大字节数，默认 `10485760`（10MB），超过则截断保留末尾

提示词控制（避免“一直 continue”）：

- 默认系统提示词会严格要求**优先输出 `WAIT`**，只有在明确需要推进任务时才给出具体指令；若想进一步自定义，可创建 `prompt.txt` 并使用 `--system-prompt-file prompt.txt`（或设置 `AI_MONITOR_LLM_SYSTEM_PROMPT_FILE`）。
- 建议在提示词中明确写出：“除非非常确定下一步，否则输出 WAIT，不要机械地回复 continue”。

## 日志与 PID

- 日志目录：`~/.tmux-monitor/`
- PID 文件：`*.pid`（用于 stop/status）

## 安全提示

- 会产生网络请求，并把面板输出发给第三方/本地服务；请自行评估敏感信息风险。
- 对“危险确认提示”（delete/drop/reset/rm -rf 等）默认选择 `WAIT`，避免自动确认破坏性操作。
