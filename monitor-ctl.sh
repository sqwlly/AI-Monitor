#!/bin/bash

# ============================================
# tmux 监控脚本管理工具
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMART_SCRIPT="${SCRIPT_DIR}/smart-monitor.sh"
PROMPTS_DIR="${SCRIPT_DIR}/prompts"
ROLES_MANIFEST="${PROMPTS_DIR}/roles.json"
LOG_DIR="$HOME/.tmux-monitor"
CMD="${CLAUDE_MONITOR_CMD:-$(basename "$0")}"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ROLE_CHOICES=()
ROLE_DESCS=()

hash_text() {
    local input="${1:-}"
    if command -v sha256sum >/dev/null 2>&1; then
        printf "%s" "$input" | sha256sum | awk '{print $1}'
        return 0
    fi
    if command -v shasum >/dev/null 2>&1; then
        printf "%s" "$input" | shasum -a 256 | awk '{print $1}'
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$input" <<'PY'
import hashlib
import sys
data = sys.argv[1].encode("utf-8", "replace")
print(hashlib.sha256(data).hexdigest())
PY
        return 0
    fi
    if command -v cksum >/dev/null 2>&1; then
        printf "%s" "$input" | cksum | awk '{print $1}'
        return 0
    fi
    return 1
}

resolve_target_id() {
    local target="${1:-}"
    local pane_id=""

    pane_id="$(tmux display-message -p -t "$target" "#{pane_id}" 2>/dev/null || true)"
    pane_id="${pane_id#%}"
    if [ -n "$pane_id" ]; then
        hash_text "$pane_id"
        return $?
    fi

    hash_text "$target"
}

read_pid_meta() {
    local pid_file="$1"
    local key="$2"
    local line

    line="$(grep -E "^${key}=" "$pid_file" 2>/dev/null | head -n 1 || true)"
    printf "%s" "${line#*=}"
}

is_numeric_pid() {
    local pid="${1:-}"
    [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -gt 1 ]
}

get_pid_cmdline() {
    local pid="${1:-}"
    if ! is_numeric_pid "$pid"; then
        return 1
    fi

    if [ -r "/proc/${pid}/cmdline" ]; then
        tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true
        return 0
    fi

    ps -o command= -p "$pid" 2>/dev/null || true
    return 0
}

pid_matches_monitor_process() {
    local pid="${1:-}"
    local expected_target="${2:-}"
    local cmdline

    if ! is_numeric_pid "$pid"; then
        return 1
    fi
    if ! ps -p "$pid" > /dev/null 2>&1; then
        return 1
    fi

    cmdline="$(get_pid_cmdline "$pid")"
    if [ -z "$cmdline" ]; then
        return 1
    fi

    if ! printf "%s" "$cmdline" | grep -Fq "smart-monitor.sh"; then
        return 1
    fi
    if [ -n "$expected_target" ] && ! printf "%s" "$cmdline" | grep -Fq "$expected_target"; then
        return 2
    fi
    return 0
}

role_choice_description() {
    case "$1" in
        auto) echo "自动择优（根据阶段切换角色）" ;;
        monitor) echo "默认监工，偏保守" ;;
        senior-engineer) echo "高级研发，主动推进编码/调试" ;;
        test-manager) echo "测试经理，侧重验证与风控" ;;
        architect) echo "架构师，负责拆分设计" ;;
        ui-designer) echo "产品/UI 设计师" ;;
        game-designer) echo "游戏策划/系统设计师（硬核玩家视角）" ;;
        algo-engineer) echo "算法工程师" ;;
        *) echo "" ;;
    esac
}

load_role_choices() {
    ROLE_CHOICES=("auto")
    ROLE_DESCS=("自动择优（根据阶段切换角色）")

    if [ -f "$ROLES_MANIFEST" ] && command -v python3 >/dev/null 2>&1; then
        local line role desc
        while IFS=$'\t' read -r role desc; do
            if [ -n "$role" ]; then
                ROLE_CHOICES+=("$role")
                ROLE_DESCS+=("$desc")
            fi
        done < <(python3 - "$ROLES_MANIFEST" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f) or {}

for role, desc in data.items():
    role = (role or "").strip()
    desc = (desc or "").strip()
    if role:
        sys.stdout.write(f"{role}\t{desc}\n")
PY
)
        if [ "${#ROLE_CHOICES[@]}" -gt 1 ]; then
            return 0
        fi
    fi

    ROLE_CHOICES=("auto" "monitor" "senior-engineer" "test-manager" "architect" "ui-designer" "game-designer" "algo-engineer")
    ROLE_DESCS=("自动择优（根据阶段切换角色）" "默认监工，偏保守" "高级研发，主动推进编码/调试" "测试经理，侧重验证与风控" "架构师，负责拆分设计" "产品/UI 设计师" "游戏策划/系统设计师（硬核玩家视角）" "算法工程师")
    return 0
}

# 功能选择提示
prompt_features_choice() {
    local input="/dev/stdin"
    local out="/dev/fd/2"
    if [ -r /dev/tty ]; then
        input="/dev/tty"
    fi
    if [ -w /dev/tty ]; then
        out="/dev/tty"
    fi

    printf "\n" > "$out"
    printf "启用扩展功能（多选，用空格分隔，回车跳过）：\n" > "$out"
    printf "  1) memory     任务记忆 - 记录决策历史，支持恢复\n" > "$out"
    printf "  2) notify     桌面通知 - 卡住/危险操作时提醒\n" > "$out"
    printf "  3) assess     自我评估 - 检测死循环，自动切换角色\n" > "$out"
    printf "  4) all        全部启用（推荐，含多Agent+仲裁）\n" > "$out"
    printf "  5) pipeline   多Agent编排 - 多角色并行/投票决策\n" > "$out"
    printf "  6) arbiter    决策仲裁 - 多源建议冲突消解\n" > "$out"
    printf "输入编号: " > "$out"

    local selection
    read -r selection < "$input"

    if [ -z "$selection" ]; then
        return
    fi

    # 解析选择
    local enable_memory=0
    local enable_notify=0
    local enable_assess=0
    local enable_orchestrator=0
    local enable_arbiter=0

    for item in $selection; do
        case "$item" in
            1|memory)  enable_memory=1 ;;
            2|notify)  enable_notify=1 ;;
            3|assess)  enable_assess=1 ;;
            4|all)     enable_memory=1; enable_notify=1; enable_assess=1; enable_orchestrator=1; enable_arbiter=1 ;;
            5|pipeline) enable_orchestrator=1 ;;
            6|arbiter)  enable_arbiter=1 ;;
        esac
    done

    # 设置环境变量
    if [ "$enable_memory" = "1" ]; then
        export AI_MONITOR_MEMORY_ENABLED=1
        printf "  ✅ 任务记忆已启用\n" > "$out"
    fi
    if [ "$enable_notify" = "1" ]; then
        export AI_MONITOR_NOTIFICATION_ENABLED=1
        # 确保配置文件存在
        if [ ! -f "${HOME}/.tmux-monitor/config/notification.json" ]; then
            python3 "${SCRIPT_DIR}/notification_hub.py" config init >/dev/null 2>&1 || true
        fi
        printf "  ✅ 桌面通知已启用\n" > "$out"
    fi
    if [ "$enable_assess" = "1" ]; then
        export AI_MONITOR_ASSESSMENT_ENABLED=1
        printf "  ✅ 自我评估已启用\n" > "$out"
    fi
    if [ "$enable_orchestrator" = "1" ]; then
        export AI_MONITOR_ORCHESTRATOR_ENABLED=1
        if [ -z "${AI_MONITOR_PIPELINE:-}" ]; then
            export AI_MONITOR_PIPELINE="vote"
        fi
        printf "  ✅ 多Agent编排已启用 (pipeline=%s)\n" "${AI_MONITOR_PIPELINE}" > "$out"
    fi
    if [ "$enable_arbiter" = "1" ]; then
        export AI_MONITOR_ARBITER_ENABLED=1
        printf "  ✅ 决策仲裁已启用\n" > "$out"
    fi
}

prompt_role_choice() {
    load_role_choices

    local default_role="${AI_MONITOR_LLM_ROLE:-auto}"
    local total="${#ROLE_CHOICES[@]}"

    local input="/dev/stdin"
    local out="/dev/fd/2"
    if [ -r /dev/tty ]; then
        input="/dev/tty"
    fi
    if [ -w /dev/tty ]; then
        out="/dev/tty"
    fi

    printf "%s\n" "" > "$out"
    printf "%s\n" "请选择 LLM 角色（回车默认为: $default_role）：" > "$out"
    local index=1
    for role in "${ROLE_CHOICES[@]}"; do
        local desc
        desc="${ROLE_DESCS[$((index - 1))]}"
        if [ -z "$desc" ]; then
            desc="$(role_choice_description "$role")"
        fi
        local marker=""
        if [ "$role" = "$default_role" ]; then
            marker="(默认)"
        fi
        printf "  %d) %-17s %s %s\n" "$index" "$role" "$desc" "$marker" > "$out"
        index=$((index + 1))
    done
    printf "%s" "输入编号或名称: " > "$out"
    read -r selection < "$input"

    if [ -z "$selection" ]; then
        echo "$default_role"
        return
    fi

    if [[ "$selection" =~ ^[0-9]+$ ]]; then
        local num=$selection
        if [ "$num" -ge 1 ] && [ "$num" -le "$total" ]; then
            echo "${ROLE_CHOICES[$((num - 1))]}"
            return
        fi
    fi

    for role in "${ROLE_CHOICES[@]}"; do
        if [ "$selection" = "$role" ]; then
            echo "$role"
            return
        fi
    done

    echo "$default_role"
}

show_help() {
    cat << EOF
用法: ${CMD} {run|stop|restart|status|logs|tail|list|clean|install|test|goal|pipeline|arbiter|memory|notify|assess} [参数]

命令:
  run <target> [opts]   - 🧠 启动 LLM 监工监控（默认命令）
  stop [target]         - 停止监控（不指定则停止所有）
  restart <target> [opts] - 重启监控
  status                - 查看所有运行中的监控
  list                  - 列出所有 tmux 会话和面板（TTY 下可交互选择并启动监控）
  logs [target]         - 查看日志
  tail [target]         - 实时查看日志
  clean                 - 清理旧日志
  install [name]        - 安装到 ~/.local/bin（默认命令名: cm）
  test                  - 测试 LLM 配置与连通性（不启动监控）
  goal                  - 🎯 设置/查看/清理会话 Goal/DoD/约束（Agent-of-Agent 入口）
  pipeline              - 多Agent编排（投票/串行）
  arbiter               - 决策仲裁（冲突消解/安全优先）
  memory / notify / assess - 扩展模块命令（任务记忆/通知/评估）

参数格式:
  target: 会话:窗口.面板（窗口可用编号或名称；推荐用编号以避免重名/歧义，例如: 2:1.0 或 2:mon.0）

快捷方式:
  - 直接传 target：${CMD} "2:mon.0"      # 等同于 run
  - 交互选择：${CMD}                    # 直接进入选择并启动 run
  - 别名：r=run, s=run, st=status, ls=list, t=tail, k=stop

LLM 监工参数（传给 run / 默认 target 调用）:
  --model <model>
  --base-url <url>         # OpenAI 兼容接口（如 Ollama: http://localhost:11434/v1）
  --api-key <key>
  --role <role>
  --timeout <sec>
  --system-prompt-file <file>
  --with-orchestrator      # 启用多Agent编排（默认 pipeline=vote）
  --with-arbiter           # 启用决策仲裁（多源建议冲突消解）
  --with-protocol          # 启用执行器协议握手/解析（Agent-of-Agent）
  --with-intelligence      # 启用智能增强（模式检测+自适应策略）
  --agent                  # Agent-of-Agent：协议化 + 计划闭环（等价于 --with-protocol + 开启闭环）
  --pipeline <name>        # 选择 pipeline: default|vote|sequential|auto
  --with-all               # 启用 memory+notify+assess+orchestrator+arbiter+intelligence+protocol+闭环（推荐）

交互模式默认：
  - 若未显式传上述扩展参数，则默认全量使能（可用 export AI_MONITOR_INTERACTIVE_DEFAULT_ALL=0 关闭）

示例:
  ${CMD} list                      # 查看所有可监控的面板
  ${CMD} run 2:mon.0               # 🧠 LLM 监工监控
  ${CMD} 2:mon.0 --base-url "http://localhost:11434/v1" --model "qwen2.5:7b-instruct"
  ${CMD} 2:mon.0 --agent --with-all # Agent-of-Agent：协议化 + 计划闭环 + 全部扩展
  ${CMD} goal set 2:mon.0 --goal "实现 xxx" --dod "测试通过" --dod "更新 README"
  ${CMD} goal plan 2:mon.0         # 基于 goal 生成并激活执行计划（plan）
  ${CMD} test                      # 测试 LLM 是否可用（返回一行 continue/WAIT 等）
  ${CMD} status                    # 查看运行状态
  ${CMD} tail 2:mon.0              # 实时查看该面板的日志
  ${CMD} stop 2:mon.0              # 停止该面板的监控
  ${CMD} stop                      # 停止所有监控
  ${CMD} install                   # 安装命令（默认 cm）
EOF
}

resolve_session_id_for_ref() {
    local ref="${1:-}"

    if [ -z "$ref" ]; then
        return 1
    fi

    # ref 可能是 session_id（8位）或 tmux target（2:mon.0）
    if is_target "$ref"; then
        local target_id pid_file sid
        target_id="$(resolve_target_id "$ref" 2>/dev/null || echo "")"
        if [ -z "$target_id" ]; then
            return 1
        fi
        pid_file="${LOG_DIR}/smart_${target_id}.pid"
        sid="$(read_pid_meta "$pid_file" "session_id")"
        if [ -n "$sid" ]; then
            printf "%s" "$sid"
            return 0
        fi
        # 回退：尝试从 memory db 里解析（可能监控异常退出但会话仍标记 active）
        sid="$(python3 "${SCRIPT_DIR}/memory_manager.py" resolve-session "$ref" 2>/dev/null || echo "")"
        sid="$(printf "%s" "$sid" | head -n 1 | tr -d '\r')"
        if [ -n "$sid" ]; then
            printf "%s" "$sid"
            return 0
        fi
        return 1
    fi

    printf "%s" "$ref"
    return 0
}

goal_cmd() {
    local action="${1:-}"
    shift || true

    case "$action" in
        set|show|context|clear|plan) ;;
        *)
            echo "用法: ${CMD} goal {set|show|context|clear|plan} <target|session_id> [args...]"
            return 1
            ;;
    esac

    local ref="${1:-}"
    if [ -z "$ref" ]; then
        echo -e "${RED}错误: 请指定 target 或 session_id${NC}"
        return 1
    fi
    shift || true

    local session_id
    session_id="$(resolve_session_id_for_ref "$ref" || echo "")"
    if [ -z "$session_id" ]; then
        echo -e "${RED}错误: 无法解析 session_id（请确认监控已启动，或直接传 session_id）${NC}"
        return 1
    fi

    case "$action" in
        set)
            python3 "${SCRIPT_DIR}/spec_manager.py" set "$session_id" "$@"
            ;;
        show)
            python3 "${SCRIPT_DIR}/spec_manager.py" show "$session_id"
            ;;
        context)
            python3 "${SCRIPT_DIR}/spec_manager.py" context "$session_id" "$@"
            ;;
        clear)
            python3 "${SCRIPT_DIR}/spec_manager.py" clear "$session_id"
            ;;
        plan)
            python3 "${SCRIPT_DIR}/spec_manager.py" ensure-plan "$session_id" "$@"
            ;;
    esac
}

is_target() {
    local value="${1:-}"
    [[ "$value" =~ ^([^:]+):([^.]+)\.([0-9]+)$ ]]
}

resolve_window_index() {
    local session="${1:-}"
    local selector="${2:-}"
    if [ -z "$session" ] || [ -z "$selector" ]; then
        echo "$selector"
        return 0
    fi
    if [[ "$selector" =~ ^[0-9]+$ ]]; then
        echo "$selector"
        return 0
    fi
    local idx
    idx="$(tmux list-windows -t "$session" -F "#{window_index}	#{window_name}" 2>/dev/null | awk -F'\t' -v name="$selector" '$2==name {print $1; exit}' || true)"
    if [ -n "$idx" ]; then
        echo "$idx"
        return 0
    fi
    echo "$selector"
}

prompt_target() {
    local input="/dev/stdin"
    local out="/dev/fd/2"
    if [ -r /dev/tty ]; then
        input="/dev/tty"
    fi
    if [ -w /dev/tty ]; then
        out="/dev/tty"
    fi

    printf "%s\n" "📋 可用的 tmux 会话:" > "$out"
    printf "%s\n" "----------------------------------------" > "$out"
    tmux list-sessions 2>/dev/null || {
        printf "%b\n" "${RED}❌ 没有运行中的 tmux 会话${NC}" > "$out"
        exit 1
    }
    printf "%s\n" "" > "$out"
    printf "%s" "输入会话名称或编号: " > "$out"
    read -r session < "$input"

    printf "%s\n" "" > "$out"
    printf "%s\n" "📋 该会话可用窗口:" > "$out"
    tmux list-windows -t "$session" -F "#{window_index}:#{window_name}" 2>/dev/null || true
    printf "%s" "输入窗口名称或编号: " > "$out"
    read -r window < "$input"
    window="$(resolve_window_index "$session" "$window")"

    printf "%s\n" "" > "$out"
    printf "%s\n" "📋 该窗口可用面板:" > "$out"
    tmux list-panes -t "$session:$window" -F "#{pane_index}: #{pane_current_command}" 2>/dev/null || true
    printf "%s" "输入面板编号 [默认:0]: " > "$out"
    read -r pane < "$input"
    pane="${pane:-0}"

    echo "${session}:${window}.${pane}"
}

list_tmux_panes() {
    echo "📋 可用的 tmux 会话和面板:"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    if ! tmux list-sessions 2>/dev/null; then
        echo -e "${RED}没有运行中的 tmux 会话${NC}"
        return
    fi
    
    echo ""
    local -a pane_targets=()
    local -a pane_labels=()

    while IFS= read -r session; do
        echo -e "${GREEN}会话: $session${NC}"
        echo "  进入命令: tmux attach -t $session"

        while IFS= read -r window; do
            local window_index="${window%%:*}"
            local window_name="${window#*:}"
            echo -e "  ${BLUE}窗口: $window_name ($window_index)${NC}"
            
            while IFS= read -r pane; do
                local pane_index="${pane%%:*}"
                local pane_cmd="${pane#*: }"
                local target="${session}:${window_index}.${pane_index}"

                pane_targets+=("$target")
                pane_labels+=("${target}  ${pane_cmd}  (window=${window_name})")
                
                # 高亮显示可能是 Claude Code 的面板
                if echo "$pane_cmd" | grep -qi "claude"; then
                    echo -e "    ${YELLOW}→ 面板 $pane_index: $pane_cmd ⭐${NC}"
                    echo -e "      ${YELLOW}监控命令: ${CMD} \"${target}\"  (window=${window_name})${NC}"
                else
                    echo "    → 面板 $pane_index: $pane_cmd"
                    echo "      监控命令: ${CMD} \"${target}\"  (window=${window_name})"
                fi
                if [ -z "${AI_MONITOR_LLM_ROLE:-}" ]; then
                    echo "      角色: 默认 auto（可用 --role 或 AI_MONITOR_LLM_ROLE 覆盖）"
                fi
            done < <(tmux list-panes -t "$session:$window_index" -F "#{pane_index}: #{pane_current_command}" 2>/dev/null || true)
        done < <(tmux list-windows -t "$session" -F "#{window_index}:#{window_name}" 2>/dev/null || true)
        echo ""
    done < <(tmux list-sessions -F "#{session_name}" 2>/dev/null || true)

    # 交互式选择（仅在 TTY 且 stdout 为终端时启用；避免影响脚本/管道场景）
    if [ -t 0 ] && [ -t 1 ] && [ "${#pane_targets[@]}" -gt 0 ]; then
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "选择要启动监控的面板（回车退出）："
        local idx=1
        while [ "$idx" -le "${#pane_targets[@]}" ]; do
            printf "  %2d) %s\n" "$idx" "${pane_labels[$((idx - 1))]}"
            idx=$((idx + 1))
        done
        echo -n "输入编号或 target: "
        local selection
        read -r selection

        if [ -z "$selection" ]; then
            return
        fi

        local chosen_target=""
        if [[ "$selection" =~ ^[0-9]+$ ]]; then
            if [ "$selection" -ge 1 ] && [ "$selection" -le "${#pane_targets[@]}" ]; then
                chosen_target="${pane_targets[$((selection - 1))]}"
            fi
        elif is_target "$selection"; then
            chosen_target="$selection"
        fi

        if [ -z "$chosen_target" ]; then
            echo -e "${YELLOW}无效选择，已退出。${NC}"
            return
        fi

        # 交互模式：默认全量使能（Agent-of-Agent + 全部扩展）
        start_llm_monitor "$chosen_target"
        # 启动后自动进入 tail 模式
        echo ""
        tail_logs "$chosen_target"
    fi
}

start_llm_monitor() {
    local target="$1"

    if [ -z "$target" ]; then
        if [ -t 0 ]; then
            target="$(prompt_target)"
        else
            echo -e "${RED}错误: 请指定要监控的面板${NC}"
            echo "使用 '${CMD} list' 查看可用面板"
            exit 1
        fi
    fi

    local smart_script="$SMART_SCRIPT"

    if [ ! -f "$smart_script" ]; then
        echo -e "${RED}错误: 找不到 $SMART_SCRIPT${NC}"
        exit 1
    fi

    # 解析目标
    if [[ $target =~ ^([^:]+):([^.]+)\.([0-9]+)$ ]]; then
        local target_id
        target_id="$(resolve_target_id "$target" 2>/dev/null || true)"
        if [ -z "$target_id" ]; then
            echo -e "${RED}错误: 无法生成 target ID（缺少哈希工具）${NC}"
            exit 1
        fi
        pid_file="$LOG_DIR/smart_${target_id}.pid"

        if [ -f "$pid_file" ]; then
            pid="$(head -n 1 "$pid_file" 2>/dev/null || true)"
            if pid_matches_monitor_process "$pid" ""; then
                echo -e "${YELLOW}该面板已在 LLM 监工监控中 (PID: $pid)${NC}"
                return
            fi
        fi

        # 后台启动 LLM 监工监控
        shift
        local extra_args=("$@")
        local configured_role=""
        local has_explicit_role=0
        local idx=0
        local args_count="${#extra_args[@]}"
        local filtered_args=()

        local interactive_mode=0
        if [ -t 0 ] && [ -t 1 ]; then
            interactive_mode=1
        fi
        local interactive_default_all="${AI_MONITOR_INTERACTIVE_DEFAULT_ALL:-1}"
        local has_feature_flags=0

        # 解析扩展功能参数
        while [ $idx -lt $args_count ]; do
            case "${extra_args[$idx]}" in
                --role)
                    has_explicit_role=1
                    if [ $((idx + 1)) -lt $args_count ]; then
                        configured_role="${extra_args[$((idx + 1))]}"
                    else
                        configured_role="(missing)"
                    fi
                    filtered_args+=("${extra_args[$idx]}" "${extra_args[$((idx + 1))]}")
                    idx=$((idx + 2))
                    ;;
                --with-memory)
                    has_feature_flags=1
                    export AI_MONITOR_MEMORY_ENABLED=1
                    idx=$((idx + 1))
                    ;;
                --agent)
                    # Agent-of-Agent：协议化 + 计划闭环（可与 --with-all 叠加）
                    has_feature_flags=1
                    export AI_MONITOR_AGENT_LOOP_ENABLED=1
                    export AI_MONITOR_EXECUTOR_PROTOCOL_ENABLED=1
                    idx=$((idx + 1))
                    ;;
                --with-notify)
                    has_feature_flags=1
                    export AI_MONITOR_NOTIFICATION_ENABLED=1
                    # 确保配置文件存在
                    if [ ! -f "${HOME}/.tmux-monitor/config/notification.json" ]; then
                        python3 "${SCRIPT_DIR}/notification_hub.py" config init >/dev/null 2>&1 || true
                    fi
                    idx=$((idx + 1))
                    ;;
                --with-assess)
                    has_feature_flags=1
                    export AI_MONITOR_ASSESSMENT_ENABLED=1
                    idx=$((idx + 1))
                    ;;
                --with-orchestrator)
                    has_feature_flags=1
                    export AI_MONITOR_ORCHESTRATOR_ENABLED=1
                    if [ -z "${AI_MONITOR_PIPELINE:-}" ]; then
                        export AI_MONITOR_PIPELINE="vote"
                    fi
                    idx=$((idx + 1))
                    ;;
                --with-arbiter)
                    has_feature_flags=1
                    export AI_MONITOR_ARBITER_ENABLED=1
                    idx=$((idx + 1))
                    ;;
                --with-protocol)
                    has_feature_flags=1
                    export AI_MONITOR_EXECUTOR_PROTOCOL_ENABLED=1
                    idx=$((idx + 1))
                    ;;
                --with-intelligence)
                    has_feature_flags=1
                    export AI_MONITOR_INTELLIGENCE_ENABLED=1
                    export AI_MONITOR_INTELLIGENT_ENGINE_AGGRESSIVENESS="${AI_MONITOR_INTELLIGENT_ENGINE_AGGRESSIVENESS:-0.8}"
                    idx=$((idx + 1))
                    ;;
                --pipeline)
                    has_feature_flags=1
                    if [ $((idx + 1)) -lt $args_count ]; then
                        export AI_MONITOR_PIPELINE="${extra_args[$((idx + 1))]}"
                        export AI_MONITOR_ORCHESTRATOR_ENABLED=1
                    fi
                    idx=$((idx + 2))
                    ;;
                --with-all)
                    has_feature_flags=1
                    export AI_MONITOR_MEMORY_ENABLED=1
                    export AI_MONITOR_NOTIFICATION_ENABLED=1
                    export AI_MONITOR_ASSESSMENT_ENABLED=1
                    export AI_MONITOR_ORCHESTRATOR_ENABLED=1
                    export AI_MONITOR_ARBITER_ENABLED=1
                    export AI_MONITOR_EXECUTOR_PROTOCOL_ENABLED=1
                    export AI_MONITOR_AGENT_LOOP_ENABLED=1
                    export AI_MONITOR_INTELLIGENCE_ENABLED=1
                    export AI_MONITOR_INTELLIGENT_ENGINE_AGGRESSIVENESS="${AI_MONITOR_INTELLIGENT_ENGINE_AGGRESSIVENESS:-0.8}"
                    if [ -z "${AI_MONITOR_PIPELINE:-}" ]; then
                        export AI_MONITOR_PIPELINE="vote"
                    fi
                    if [ ! -f "${HOME}/.tmux-monitor/config/notification.json" ]; then
                        python3 "${SCRIPT_DIR}/notification_hub.py" config init >/dev/null 2>&1 || true
                    fi
                    idx=$((idx + 1))
                    ;;
                *)
                    filtered_args+=("${extra_args[$idx]}")
                    idx=$((idx + 1))
                    ;;
            esac
        done
        extra_args=("${filtered_args[@]}")
        args_count="${#extra_args[@]}"

        # 交互模式：若用户未显式指定任何扩展参数，则默认全量使能（可用 AI_MONITOR_INTERACTIVE_DEFAULT_ALL=0 关闭）
        if [ "$interactive_mode" = "1" ] && [ "$interactive_default_all" = "1" ] && [ "$has_feature_flags" -eq 0 ]; then
            export AI_MONITOR_MEMORY_ENABLED=1
            export AI_MONITOR_NOTIFICATION_ENABLED=1
            export AI_MONITOR_ASSESSMENT_ENABLED=1
            export AI_MONITOR_ORCHESTRATOR_ENABLED=1
            export AI_MONITOR_ARBITER_ENABLED=1
            export AI_MONITOR_EXECUTOR_PROTOCOL_ENABLED=1
            export AI_MONITOR_AGENT_LOOP_ENABLED=1
            export AI_MONITOR_INTELLIGENCE_ENABLED=1
            export AI_MONITOR_INTELLIGENT_ENGINE_AGGRESSIVENESS="${AI_MONITOR_INTELLIGENT_ENGINE_AGGRESSIVENESS:-0.8}"
            if [ -z "${AI_MONITOR_PIPELINE:-}" ]; then
                export AI_MONITOR_PIPELINE="vote"
            fi
            if [ ! -f "${HOME}/.tmux-monitor/config/notification.json" ]; then
                python3 "${SCRIPT_DIR}/notification_hub.py" config init >/dev/null 2>&1 || true
            fi
        fi

        # 检查是否已设置角色
        if [ $has_explicit_role -eq 0 ]; then
            idx=0
            while [ $idx -lt $args_count ]; do
                if [ "${extra_args[$idx]}" = "--role" ]; then
                    has_explicit_role=1
                    break
                fi
                idx=$((idx + 1))
            done
        fi

        if [ $has_explicit_role -eq 0 ]; then
            if [ "$interactive_mode" = "1" ]; then
                local chosen_role
                chosen_role="$(prompt_role_choice)"
                if [ -z "$chosen_role" ]; then
                    chosen_role="${AI_MONITOR_LLM_ROLE:-auto}"
                fi
                extra_args=(--role "$chosen_role" "${extra_args[@]}")
                configured_role="$chosen_role"
            elif [ -n "${AI_MONITOR_LLM_ROLE:-}" ]; then
                extra_args=(--role "${AI_MONITOR_LLM_ROLE}" "${extra_args[@]}")
                configured_role="${AI_MONITOR_LLM_ROLE}"
            else
                extra_args=(--role "auto" "${extra_args[@]}")
                configured_role="auto"
            fi
        fi

        nohup bash "$smart_script" "$target" "${extra_args[@]}" > /dev/null 2>&1 &
        sleep 1

        echo -e "${GREEN}✓ 已启动 LLM 监工监控 🧠${NC}"
        echo "  目标: $target"
        echo "  模式: LLM 监工（OpenAI 兼容接口）"
        if [ -n "$configured_role" ]; then
            echo "  角色: $configured_role"
        fi
	        # 显示已启用的扩展功能
	        local features=""
	        [ "${AI_MONITOR_MEMORY_ENABLED:-1}" = "1" ] && features="${features}记忆 "
	        [ "${AI_MONITOR_NOTIFICATION_ENABLED:-1}" = "1" ] && features="${features}通知 "
	        [ "${AI_MONITOR_ASSESSMENT_ENABLED:-1}" = "1" ] && features="${features}评估 "
	        [ "${AI_MONITOR_EXECUTOR_PROTOCOL_ENABLED:-0}" = "1" ] && features="${features}协议 "
	        [ "${AI_MONITOR_AGENT_LOOP_ENABLED:-0}" = "1" ] && features="${features}闭环 "
	        [ "${AI_MONITOR_ORCHESTRATOR_ENABLED:-0}" = "1" ] && features="${features}多Agent(${AI_MONITOR_PIPELINE:-default}) "
	        [ "${AI_MONITOR_ARBITER_ENABLED:-0}" = "1" ] && features="${features}仲裁 "
	        [ "${AI_MONITOR_INTELLIGENCE_ENABLED:-0}" = "1" ] && features="${features}🧠智能 "
	        if [ -n "$features" ]; then
	            echo -e "  扩展: ${YELLOW}${features}${NC}"
	        fi
        echo "  日志: $LOG_DIR/smart_${target_id}.log"
        echo ""
        echo "使用 '${CMD} tail $target' 实时查看日志"
    else
        echo -e "${RED}格式错误！请使用: 会话:窗口.面板${NC}"
        echo "例如: ${CMD} run 2:mon.0"
        exit 1
    fi
}

stop_monitor() {
    local target="$1"

    if [ -z "$target" ]; then
        # 停止所有
        echo "停止所有监控进程..."
        stopped=0
        if [ -d "$LOG_DIR" ]; then
            for pid_file in "$LOG_DIR"/*.pid; do
                if [ -f "$pid_file" ]; then
                    pid="$(head -n 1 "$pid_file" 2>/dev/null || true)"
                    if ! is_numeric_pid "$pid" || ! ps -p "$pid" > /dev/null 2>&1; then
                        rm -f "$pid_file"
                        continue
                    fi
                    if pid_matches_monitor_process "$pid" ""; then
                        kill "$pid"
                        echo -e "${GREEN}✓ 已停止 $(basename ${pid_file%.pid})${NC}"
                        stopped=1
                        rm -f "$pid_file"
                    else
                        echo -e "${YELLOW}⚠️  跳过停止：PID 存在但不匹配 smart-monitor 进程 (PID: $pid, file: $pid_file)${NC}"
                    fi
                fi
            done
        fi

        if [ $stopped -eq 0 ]; then
            echo -e "${YELLOW}没有运行中的监控进程${NC}"
        fi
    else
        # 停止指定的（兼容旧版本 pid 文件）
        if [[ $target =~ ^([^:]+):([^.]+)\.([0-9]+)$ ]]; then
            session="${BASH_REMATCH[1]}"
            window="${BASH_REMATCH[2]}"
            pane="${BASH_REMATCH[3]}"

            stopped=0

            # 旧版本：monitor_*.pid（已废弃，但仍尝试停止）
            pid_file="$LOG_DIR/monitor_${session}_${window}_${pane}.pid"
            if [ -f "$pid_file" ]; then
                pid="$(head -n 1 "$pid_file" 2>/dev/null || true)"
                if ! is_numeric_pid "$pid" || ! ps -p "$pid" > /dev/null 2>&1; then
                    rm -f "$pid_file"
                elif pid_matches_monitor_process "$pid" "$target"; then
                    kill "$pid"
                    echo -e "${GREEN}✓ 已停止旧版本监控 $target${NC}"
                    stopped=1
                    rm -f "$pid_file"
                else
                    echo -e "${YELLOW}⚠️  跳过停止旧版本监控：PID 存在但不匹配 (PID: $pid, file: $pid_file)${NC}"
                fi
            fi

            # 当前：smart_<hash>.pid（LLM 监工）
            local target_id=""
            target_id="$(resolve_target_id "$target" 2>/dev/null || true)"
            if [ -n "$target_id" ]; then
                smart_pid_file="$LOG_DIR/smart_${target_id}.pid"
                if [ -f "$smart_pid_file" ]; then
                    pid="$(head -n 1 "$smart_pid_file" 2>/dev/null || true)"
                    if ! is_numeric_pid "$pid" || ! ps -p "$pid" > /dev/null 2>&1; then
                        rm -f "$smart_pid_file"
                    elif pid_matches_monitor_process "$pid" ""; then
                        kill "$pid"
                        echo -e "${GREEN}✓ 已停止 LLM 监工监控 $target${NC}"
                        stopped=1
                        rm -f "$smart_pid_file"
                    else
                        echo -e "${YELLOW}⚠️  跳过停止：PID 存在但不匹配 smart-monitor 进程 (PID: $pid, file: $smart_pid_file)${NC}"
                    fi
                fi
            fi

            # 兼容旧 smart pid 文件名：smart_${session}_${window}_${pane}.pid
            local legacy_smart_pid_file="$LOG_DIR/smart_${session}_${window}_${pane}.pid"
            if [ -f "$legacy_smart_pid_file" ]; then
                pid="$(head -n 1 "$legacy_smart_pid_file" 2>/dev/null || true)"
                if ! is_numeric_pid "$pid" || ! ps -p "$pid" > /dev/null 2>&1; then
                    rm -f "$legacy_smart_pid_file"
                elif pid_matches_monitor_process "$pid" ""; then
                    kill "$pid"
                    echo -e "${GREEN}✓ 已停止旧命名 LLM 监工监控 $target${NC}"
                    stopped=1
                    rm -f "$legacy_smart_pid_file"
                else
                    echo -e "${YELLOW}⚠️  跳过停止：PID 存在但不匹配 smart-monitor 进程 (PID: $pid, file: $legacy_smart_pid_file)${NC}"
                fi
            fi

            if [ $stopped -eq 0 ]; then
                echo -e "${YELLOW}该面板没有运行中的监控${NC}"
            fi
        fi
    fi
}

show_status() {
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "📊 监控状态"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [ ! -d "$LOG_DIR" ] || ! compgen -G "$LOG_DIR/*.pid" > /dev/null; then
        echo -e "${YELLOW}没有运行中的监控${NC}"
        echo ""
        echo "使用 '${CMD} list' 查看可监控的面板"
        echo "使用 '${CMD} run <target>' 启动 LLM 监工监控"
        return
    fi

    for pid_file in "$LOG_DIR"/*.pid; do
        if [ -f "$pid_file" ]; then
            filename=$(basename "$pid_file" .pid)
            pid="$(head -n 1 "$pid_file" 2>/dev/null || true)"
            if [ -z "$pid" ]; then
                continue
            fi

            mode="$(read_pid_meta "$pid_file" "mode")"
            if [ -z "$mode" ]; then
                if [[ $filename =~ ^(smart|monitor)_ ]]; then
                    mode="${BASH_REMATCH[1]}"
                else
                    mode="unknown"
                fi
            fi

            target="$(read_pid_meta "$pid_file" "target")"
            if [ -z "$target" ] && [[ $filename =~ ^(smart|monitor)_(.+)_(.+)_([0-9]+)$ ]]; then
                session="${BASH_REMATCH[2]}"
                window="${BASH_REMATCH[3]}"
                pane="${BASH_REMATCH[4]}"
                target="$session:$window.$pane"
            fi
            if [ -z "$target" ]; then
                target="(unknown)"
            fi

            if pid_matches_monitor_process "$pid" ""; then
                log_file="${pid_file%.pid}.log"

                if [ "$mode" = "smart" ]; then
                    echo -e "${GREEN}✓ 运行中${NC} 🧠 - $target ${BLUE}[LLM 监工]${NC}"
                else
                    echo -e "${YELLOW}✓ 运行中${NC} - $target [旧版本/未知模式]${NC}"
                fi
                echo "  PID: $pid"
                echo "  日志: $log_file"
                if [ -f "$log_file" ]; then
                    echo "  大小: $(du -h "$log_file" | cut -f1)"
                    last_log=$(tail -1 "$log_file" 2>/dev/null)
                    if [ -n "$last_log" ]; then
                        echo "  最后: $last_log"
                    fi
                fi
                echo ""
            elif ps -p "$pid" > /dev/null 2>&1; then
                echo -e "${YELLOW}⚠️  PID 存在但进程不匹配${NC} - $target"
                echo "  PID: $pid"
                echo "  PID 文件: $pid_file"
                echo "  建议: 可能是 PID 复用/非本工具进程；如需强制清理 pid 文件请手动删除"
                echo ""
            else
                echo -e "${RED}✗ 已停止${NC} - $target (陈旧的 PID: $pid)"
                rm -f "$pid_file"
                echo ""
            fi
        fi
    done
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

show_logs() {
    local target="$1"
    
    if [ -z "$target" ]; then
        # 显示所有日志
        if [ ! -d "$LOG_DIR" ]; then
            echo "没有日志文件"
            return
        fi
        
        echo "所有日志文件:"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        for log_file in "$LOG_DIR"/*.log; do
            if [ -f "$log_file" ]; then
                echo "📄 $(basename "$log_file")"
                echo "   大小: $(du -h "$log_file" | cut -f1)"
                echo "   路径: $log_file"
                echo ""
            fi
        done
    else
        # 显示指定日志
        if [[ $target =~ ^([^:]+):([^.]+)\.([0-9]+)$ ]]; then
            session="${BASH_REMATCH[1]}"
            window="${BASH_REMATCH[2]}"
            pane="${BASH_REMATCH[3]}"
            target_id="$(resolve_target_id "$target" 2>/dev/null || true)"
            smart_log="$LOG_DIR/smart_${target_id}.log"
            legacy_log="$LOG_DIR/monitor_${session}_${window}_${pane}.log"
            legacy_smart_log="$LOG_DIR/smart_${session}_${window}_${pane}.log"

            if [ -n "$target_id" ] && [ -f "$smart_log" ]; then
                log_file="$smart_log"
            elif [ -f "$legacy_smart_log" ]; then
                log_file="$legacy_smart_log"
            else
                log_file="$legacy_log"
            fi
            
            if [ -f "$log_file" ]; then
                echo "日志: $log_file"
                echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                tail -50 "$log_file"
            else
                echo -e "${RED}找不到日志文件: $log_file${NC}"
            fi
        fi
    fi
}

tail_logs() {
    local target="$1"

    if [ -z "$target" ]; then
        # 跟踪最新的日志
        latest_log=$(ls -t "$LOG_DIR"/*.log 2>/dev/null | head -1)
        if [ ! -f "$latest_log" ]; then
            echo -e "${RED}没有日志文件${NC}"
            exit 1
        fi
        log_file="$latest_log"
    else
        if [[ $target =~ ^([^:]+):([^.]+)\.([0-9]+)$ ]]; then
            session="${BASH_REMATCH[1]}"
            window="${BASH_REMATCH[2]}"
            pane="${BASH_REMATCH[3]}"

            # 优先查找当前日志，其次旧版本日志
            target_id="$(resolve_target_id "$target" 2>/dev/null || true)"
            smart_log="$LOG_DIR/smart_${target_id}.log"
            normal_log="$LOG_DIR/monitor_${session}_${window}_${pane}.log"
            legacy_smart_log="$LOG_DIR/smart_${session}_${window}_${pane}.log"

            if [ -n "$target_id" ] && [ -f "$smart_log" ]; then
                log_file="$smart_log"
            elif [ -f "$legacy_smart_log" ]; then
                log_file="$legacy_smart_log"
            elif [ -f "$normal_log" ]; then
                log_file="$normal_log"
            else
                echo -e "${RED}找不到日志文件${NC}"
                exit 1
            fi
        else
            echo -e "${RED}格式错误${NC}"
            exit 1
        fi
    fi

    if [ ! -f "$log_file" ]; then
        echo -e "${RED}找不到日志文件: $log_file${NC}"
        exit 1
    fi

    echo "实时查看: $log_file"
    echo "按 Ctrl+C 退出"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    tail -f "$log_file"
}

clean_logs() {
    if [ ! -d "$LOG_DIR" ]; then
        echo "没有日志需要清理"
        return
    fi
    
    echo -n "确定要清理所有日志吗？(y/N): "
    read -r response
    if [[ "$response" =~ ^[Yy]$ ]]; then
        rm -f "$LOG_DIR"/*.log
        echo -e "${GREEN}✓ 日志已清理${NC}"
    else
        echo "取消清理"
    fi
}

install_cmd() {
    local name="${1:-cm}"
    local bin_dir="$HOME/.local/bin"
    local link_path="${bin_dir}/${name}"
    local target="${SCRIPT_DIR}/claude-monitor"

    mkdir -p "$bin_dir"
    ln -sf "$target" "$link_path"

    echo -e "${GREEN}✓ 已安装命令${NC}"
    echo "  命令: $name"
    echo "  路径: $link_path"
    echo ""
    if echo ":$PATH:" | grep -q ":$bin_dir:"; then
        echo "当前 PATH 已包含：$bin_dir"
    else
        echo -e "${YELLOW}⚠️  当前 PATH 未包含：$bin_dir${NC}"
        echo "临时启用："
        echo "  export PATH=\"$bin_dir:\$PATH\""
        echo "持久化：把上面这一行写入 ~/.zshrc 或 ~/.bashrc"
    fi
}

test_llm() {
    local base_url="${AI_MONITOR_LLM_BASE_URL:-${OPENAI_BASE_URL:-${OPENAI_API_BASE:-}}}"
    local model="${AI_MONITOR_LLM_MODEL:-}"
    local timeout="${AI_MONITOR_LLM_TIMEOUT:-20}"

    while [ $# -gt 0 ]; do
        case "$1" in
            --base-url)
                base_url="${2:-}"
                shift 2
                ;;
            --model)
                model="${2:-}"
                shift 2
                ;;
            --timeout)
                timeout="${2:-}"
                shift 2
                ;;
            -h|--help)
                echo "用法: ${CMD} test [--base-url <url>] [--model <model>] [--timeout <sec>]"
                echo "说明: API key 请通过环境变量提供（DASHSCOPE_API_KEY/OPENAI_API_KEY/AI_MONITOR_LLM_API_KEY）"
                return 0
                ;;
            *)
                echo -e "${RED}未知参数: $1${NC}"
                return 1
                ;;
        esac
    done

    local api_key="${AI_MONITOR_LLM_API_KEY:-${OPENAI_API_KEY:-${DASHSCOPE_API_KEY:-}}}"
    if [ -z "$api_key" ]; then
        echo -e "${RED}错误: 未检测到 API key${NC}"
        echo "请设置其中之一：DASHSCOPE_API_KEY / OPENAI_API_KEY / AI_MONITOR_LLM_API_KEY"
        return 1
    fi

    if [ -z "$base_url" ]; then
        if [ -n "${DASHSCOPE_API_KEY:-}" ]; then
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        else
            base_url="https://api.openai.com/v1"
        fi
    fi

    if [ -z "$model" ]; then
        if echo "$base_url" | grep -q "dashscope.aliyuncs.com/compatible-mode"; then
            model="qwen-max"
        else
            model="gpt-4o-mini"
        fi
    fi

    local llm_script="${SCRIPT_DIR}/llm_supervisor.py"
    if [ ! -f "$llm_script" ]; then
        echo -e "${RED}错误: 找不到 $llm_script${NC}"
        return 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo -e "${RED}错误: 未找到 python3${NC}"
        return 1
    fi

    echo "base-url: $base_url"
    echo "model: $model"
    echo "api-key: set"
    echo ""
    echo "LLM 返回（应为单行 continue/WAIT/一句指令）："

    AI_MONITOR_LLM_API_KEY="$api_key" python3 "$llm_script" --base-url "$base_url" --model "$model" --timeout "$timeout" <<'EOF'
[dummy-output]
The monitored AI seems idle and is waiting for input. Please decide a single-line command to send.
EOF
}

normalize_cmd() {
    case "${1:-}" in
        r) echo "run" ;;
        s) echo "run" ;;
        smart) echo "run" ;;
        test) echo "test" ;;
        st) echo "status" ;;
        ls) echo "list" ;;
        t) echo "tail" ;;
        k) echo "stop" ;;
        *) echo "${1:-}" ;;
    esac
}

# 主逻辑
if [ -z "${1:-}" ]; then
    if [ -t 0 ]; then
        start_llm_monitor ""
        exit 0
    fi
    show_help
    exit 1
fi

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ] || [ "${1:-}" = "help" ]; then
    show_help
    exit 0
fi

if is_target "$1"; then
    target="$1"
    shift
    start_llm_monitor "$target" "$@"
    exit 0
fi

cmd="$(normalize_cmd "$1")"
shift

case "$cmd" in
    run)
        start_llm_monitor "${1:-}" "${@:2}"
        ;;
    stop)
        stop_monitor "${1:-}"
        ;;
    restart)
        if [ -z "${1:-}" ]; then
            echo -e "${RED}错误: 请指定要重启的 target${NC}"
            exit 1
        fi
        target="$1"
        shift
        stop_monitor "$target"
        sleep 1
        start_llm_monitor "$target" "$@"
        ;;
    status)
        show_status
        ;;
    list)
        list_tmux_panes
        ;;
    logs)
        show_logs "${1:-}"
        ;;
    tail)
        tail_logs "${1:-}"
        ;;
    clean)
        clean_logs
        ;;
    install)
        install_cmd "${1:-}"
        ;;
    test)
        test_llm "$@"
        ;;
    # ============================================
    # 扩展功能命令
    # ============================================
    memory)
        # 任务记忆管理
        python3 "${SCRIPT_DIR}/memory_manager.py" "$@"
        ;;
    notify)
        # 通知管理
        python3 "${SCRIPT_DIR}/notification_hub.py" "$@"
        ;;
    assess)
        # 质量评估
        python3 "${SCRIPT_DIR}/quality_assessor.py" "$@"
        ;;
    pipeline)
        # 多Agent编排
        python3 "${SCRIPT_DIR}/agent_orchestrator.py" "$@"
        ;;
	    arbiter)
	        # 决策仲裁
	        python3 "${SCRIPT_DIR}/decision_arbiter.py" "$@"
	        ;;
	    goal)
	        # 会话 Goal/DoD/约束（Agent-of-Agent 入口）
	        goal_cmd "$@"
	        ;;
	    *)
	        show_help
	        exit 1
	        ;;
	esac
