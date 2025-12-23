# AI Monitor（Codex / Claude 监工）

一个基于 `tmux` 的“AI 监工/保活”小工具：持续读取目标面板的输出，在空闲时自动向该面板发送一条回复，让面板里的 AI（如 OpenAI Codex TUI、Claude Code、Cursor 等）继续推进任务。

## 功能

- **LLM 监工**：把最近输出交给另一个模型（OpenAI 兼容接口）生成“单行回复”，并发送回目标面板
- **多角色提示词**：内置 `monitor / architect / ui-designer / algo-engineer / senior-engineer / test-manager`，并支持 `--role auto` 让 LLM 自行挑选合适 persona 推进任务
- **研发阶段感知**：实时解析面板输出得到 `planning / coding / testing / release / done / fixing` 等阶段，并通过 `[monitor-meta] stage*` 提供给 LLM，auto 角色会据此判断是否继续推进、切换角色或暂停
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

`cm list` 的输出会在每个会话下方附带 `tmux attach -t <session>`，方便你随时进入对应的 tmux 会话。

2) 启动 LLM 监工监控：

```bash
./claude-monitor run "2:mon.0" --model "gpt-4o-mini"
```

若未通过 `--role` 或环境变量指定 persona，CLI 会在启动前提示选择（默认 `auto`，可直接回车）。

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

### 4) 切换 LLM 角色（多角色协作）

通过 `--role <role>` 或 `export AI_MONITOR_LLM_ROLE=<role>` 可以让监工模型扮演不同的角色，以 SOLID/KISS/DRY/YAGNI 原则自驱动推进任务：

1. `monitor`：默认的监工，保持任务推进并避免危险操作。
2. `architect`：软件架构师，专注模块划分、技术选型与骨架搭建。
3. `ui-designer`：产品/UI 设计师，输出交互线框、视觉规范与资产需求。
4. `algo-engineer`：算法工程师，聚焦算法实现、性能优化与验证。
5. `senior-engineer`：高级软件开发工程师，统筹编码、调试、性能与质量管控，适合通用研发推进。
6. `test-manager`：测试经理 / 质量负责人，负责安排自动化测试、回归验证、缺陷复现与质量评估。
`auto`：特殊模式，LLM 会在上述角色中自行挑选最合适的一位并以其视角作答。

示例：

```bash
cm "5:node.0" --role architect
export AI_MONITOR_LLM_ROLE="ui-designer"   # 设置默认角色
cm "0:node.0" --role auto                  # 让 LLM 自动挑选角色
cm "2:mon.0" --role test-manager           # 专注测试/质量任务
```

当启用 `auto` 时，脚本会把 `[monitor-meta] stage` 与 `stage_history` 注入到提示词里，帮助 LLM 判断当前研发阶段（planning/coding/testing/release/done 等）与推进程度，从而自动决定继续开发、切换职责或输出 `WAIT`。
使用 `cm run <target>` 且未指定角色时，CLI 会先弹出角色选择列表（默认 `auto`），便于随手切换语气。

提示词位于 `prompts/<role>.txt`，可按需修改或新增同名文件来自定义 persona。

### 环境变量（可选）

`llm_supervisor.py` 支持以下环境变量：

- `AI_MONITOR_LLM_BASE_URL`
- `AI_MONITOR_LLM_API_KEY`
- `AI_MONITOR_LLM_MODEL`
- `AI_MONITOR_LLM_ROLE`（可填 `auto` 让监工模型从内置 persona 中自行择优）
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
- 仅在 LLM 实际发送命令或触发异常时写入日志（不会为每次 `WAIT` 再刷屏），方便快速定位哪些回复已经下发

## 安全提示

- 会产生网络请求，并把面板输出发给第三方/本地服务；请自行评估敏感信息风险。
- 对“危险确认提示”（delete/drop/reset/rm -rf 等）默认选择 `WAIT`，避免自动确认破坏性操作。
