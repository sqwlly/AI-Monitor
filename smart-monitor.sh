#!/bin/bash

# ============================================
# Claude Code LLM 监工脚本
# 通过 OpenAI 兼容接口，根据面板输出生成单行回复
# ============================================

# 使用方法:
#   ./smart-monitor.sh 2:mon.0 --model "gpt-4o-mini"
#   ./smart-monitor.sh 2:mon.0 --base-url "http://localhost:11434/v1" --model "qwen2.5:7b-instruct"

# ============================================
# 参数解析
# ============================================

if [ -z "${1:-}" ]; then
    echo "📋 可用的 tmux 会话:"
    echo "----------------------------------------"
    tmux list-sessions 2>/dev/null || {
        echo "❌ 没有运行中的 tmux 会话"
        exit 1
    }
    echo ""
    echo "用法: ./smart-monitor.sh <会话:窗口.面板> [--model <model>] [--base-url <url>] [--api-key <key>] [--role <role>]"
    echo "例如: ./smart-monitor.sh 2:mon.0"
    exit 1
fi

TARGET="$1"
shift

LLM_BASE_URL=""
LLM_API_KEY=""
LLM_MODEL=""
LLM_TIMEOUT=""
LLM_SYSTEM_PROMPT_FILE=""
LLM_ROLE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --llm)
            # 兼容旧参数：LLM 已是唯一模式
            shift
            ;;
        --base-url)
            LLM_BASE_URL="${2:-}"
            shift 2
            ;;
        --api-key)
            LLM_API_KEY="${2:-}"
            shift 2
            ;;
        --model)
            LLM_MODEL="${2:-}"
            shift 2
            ;;
        --role)
            LLM_ROLE="${2:-}"
            shift 2
            ;;
        --timeout)
            LLM_TIMEOUT="${2:-}"
            shift 2
            ;;
        --system-prompt-file)
            LLM_SYSTEM_PROMPT_FILE="${2:-}"
            shift 2
            ;;
        -h|--help)
            echo "用法: ./smart-monitor.sh <会话:窗口.面板> [--model <model>] [--base-url <url>] [--api-key <key>] [--role <role>] [--timeout <sec>] [--system-prompt-file <file>]"
            exit 0
            ;;
        *)
            echo "❌ 未知参数: $1"
            exit 1
            ;;
    esac
done

# 统一计算最终配置（用于日志与传参；不打印 key）
if [ -z "$LLM_BASE_URL" ]; then
    if [ -n "${AI_MONITOR_LLM_BASE_URL:-}" ]; then
        LLM_BASE_URL="$AI_MONITOR_LLM_BASE_URL"
    elif [ -n "${OPENAI_BASE_URL:-}" ]; then
        LLM_BASE_URL="$OPENAI_BASE_URL"
    elif [ -n "${OPENAI_API_BASE:-}" ]; then
        LLM_BASE_URL="$OPENAI_API_BASE"
    elif [ -n "${DASHSCOPE_API_KEY:-}" ]; then
        LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
    else
        LLM_BASE_URL="https://api.openai.com/v1"
    fi
fi

if [ -z "$LLM_API_KEY" ]; then
    LLM_API_KEY="${AI_MONITOR_LLM_API_KEY:-${OPENAI_API_KEY:-${DASHSCOPE_API_KEY:-}}}"
fi

if [ -z "$LLM_MODEL" ]; then
    if [ -n "${AI_MONITOR_LLM_MODEL:-}" ]; then
        LLM_MODEL="$AI_MONITOR_LLM_MODEL"
    elif echo "$LLM_BASE_URL" | grep -q "dashscope.aliyuncs.com/compatible-mode"; then
        LLM_MODEL="qwen-max"
    else
        LLM_MODEL="gpt-4o-mini"
    fi
fi

if [ -z "$LLM_ROLE" ]; then
    if [ -n "${AI_MONITOR_LLM_ROLE:-}" ]; then
        LLM_ROLE="$AI_MONITOR_LLM_ROLE"
    else
        LLM_ROLE="monitor"
    fi
fi

# 解析格式: session:window.pane
if [[ $TARGET =~ ^([^:]+):([^.]+)\.([0-9]+)$ ]]; then
    TMUX_SESSION="${BASH_REMATCH[1]}"
    TMUX_WINDOW="${BASH_REMATCH[2]}"
    TMUX_PANE="${BASH_REMATCH[3]}"
else
    echo "❌ 格式错误！请使用: 会话:窗口.面板"
    exit 1
fi

# ============================================
# 配置
# ============================================

CHECK_INTERVAL=8          # 检查间隔（秒）
MIN_IDLE_TIME=12          # 空闲阈值（秒）
MAX_RETRY_SAME=3          # 同一回复最大重试次数
LOG_MAX_BYTES="${AI_MONITOR_LOG_MAX_BYTES:-10485760}"  # 默认 10MB（超过则截断保留末尾）
MAX_STAGE_HISTORY=6       # 记录最近阶段变更

CURRENT_STAGE="unknown"
STAGE_HISTORY=""

if ! [[ "$LOG_MAX_BYTES" =~ ^[0-9]+$ ]]; then
    LOG_MAX_BYTES=10485760
fi

# 日志配置
LOG_DIR="$HOME/.tmux-monitor"
LOG_FILE="$LOG_DIR/smart_${TMUX_SESSION}_${TMUX_WINDOW}_${TMUX_PANE}.log"
PID_FILE="$LOG_DIR/smart_${TMUX_SESSION}_${TMUX_WINDOW}_${TMUX_PANE}.pid"

mkdir -p "$LOG_DIR"

# ============================================
# 验证目标面板
# ============================================

if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "❌ tmux 会话 '$TMUX_SESSION' 不存在"
    exit 1
fi

if ! tmux list-panes -t "$TMUX_SESSION:$TMUX_WINDOW" 2>/dev/null | grep -q "^${TMUX_PANE}:"; then
    echo "❌ 面板 '$TMUX_SESSION:$TMUX_WINDOW.$TMUX_PANE' 不存在"
    exit 1
fi

# ============================================
# 工具函数
# ============================================

log() {
    if [ -n "$LOG_MAX_BYTES" ] && [ "$LOG_MAX_BYTES" -gt 0 ] && [ -f "$LOG_FILE" ]; then
        local log_size
        log_size="$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)"
        if [ "$log_size" -gt "$LOG_MAX_BYTES" ]; then
            local tmp
            tmp="$(mktemp "${LOG_FILE}.tmp.XXXXXX")"
            tail -c "$LOG_MAX_BYTES" "$LOG_FILE" > "$tmp" 2>/dev/null || true
            mv "$tmp" "$LOG_FILE"
        fi
    fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE" >&2
}

send_command() {
    local cmd="$1"
    tmux send-keys -t "$TMUX_SESSION:$TMUX_WINDOW.$TMUX_PANE" "$cmd"
    sleep 0.3
    tmux send-keys -t "$TMUX_SESSION:$TMUX_WINDOW.$TMUX_PANE" C-m
}

append_stage_history() {
    local stage="$1"
    if [ -z "$stage" ]; then
        return
    fi
    local -a entries=()
    if [ -n "$STAGE_HISTORY" ]; then
        local IFS='>'
        read -r -a entries <<< "$STAGE_HISTORY"
    fi
    entries+=("$stage")
    while [ "${#entries[@]}" -gt "$MAX_STAGE_HISTORY" ]; do
        entries=("${entries[@]:1}")
    done
    local IFS='>'
    STAGE_HISTORY="${entries[*]}"
}

detect_stage_from_output() {
    local text_lower stage
    text_lower="$(printf "%s" "$1" | tr '[:upper:]' '[:lower:]')"

    if echo "$text_lower" | grep -qE "(blocked|waiting for|pending approval|on hold)"; then
        stage="blocked"
    elif echo "$text_lower" | grep -qE "(error|exception|traceback|failed|panic|stack trace|bug)"; then
        stage="fixing"
    elif echo "$text_lower" | grep -qE "(deploy|release|publish|ship|delivery)"; then
        stage="release"
    elif echo "$text_lower" | grep -qE "(test pass|tests pass|pytest|jest|unit test|integration test|coverage|e2e)"; then
        if echo "$text_lower" | grep -qE "(fail|error|exception)"; then
            stage="fixing"
        else
            stage="testing"
        fi
    elif echo "$text_lower" | grep -qE "(refactor|optimi|cleanup|polish)"; then
        stage="refining"
    elif echo "$text_lower" | grep -qE "(implement|coding|write code|create file|function|class|generate code|apply_patch)"; then
        stage="coding"
    elif echo "$text_lower" | grep -qE "(plan|todo|design|spec|architecture|requirement)"; then
        stage="planning"
    elif echo "$text_lower" | grep -qE "(doc|documentation|readme|guide|write docs|changelog)"; then
        stage="documenting"
    elif echo "$text_lower" | grep -qE "(done|complete|all tasks completed|ready to ship|finalized)"; then
        stage="done"
    else
        stage="unknown"
    fi

    printf "%s" "$stage"
}

update_stage_tracker() {
    local detected_stage
    detected_stage="$(detect_stage_from_output "$1")"
    if [ -z "$detected_stage" ] || [ "$detected_stage" = "unknown" ]; then
        return
    fi
    if [ "$detected_stage" = "$CURRENT_STAGE" ]; then
        return
    fi
    CURRENT_STAGE="$detected_stage"
    append_stage_history "$CURRENT_STAGE"
    log "🧭 阶段切换 -> $CURRENT_STAGE"
}

# 通过 OpenAI 兼容接口让“监工模型”决定要发送的单行回复
decide_response_llm() {
    local output="$1"
    local last_response="${2:-}"
    local same_response_count="${3:-0}"

    local recent_output
    recent_output="$(echo "$output" | tail -n 10)"

    # 仍然保留关键安全/打断保护逻辑（避免无意义请求与危险操作）
    local output_lower
    output_lower=$(echo "$recent_output" | tr '[:upper:]' '[:lower:]')

    if echo "$recent_output" | grep -qE '(⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏|Running|Executing|Loading|Compiling|Building|Installing|Downloading)'; then
        echo "WAIT"
        return
    fi

    if echo "$output_lower" | grep -qE '(do you want to|would you like to|should i|shall i|confirm|are you sure|proceed\?|continue\?|\[y/n\]|\(y/n\)|yes/no)'; then
        if echo "$output_lower" | grep -qE '(delete|remove|drop|reset|force|overwrite|replace all|destructive|rm -rf|wipe)'; then
            echo "WAIT"
            return
        fi
    fi

    local script_dir llm_script
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    llm_script="${script_dir}/llm_supervisor.py"

    if [ ! -f "$llm_script" ]; then
        log "❌ 未找到 LLM 适配脚本: $llm_script"
        echo "WAIT"
        return
    fi

    if ! command -v python3 >/dev/null 2>&1; then
        log "❌ 未找到 python3，无法启用 LLM 监工"
        echo "WAIT"
        return
    fi

    local llm_args=(--base-url "$LLM_BASE_URL" --model "$LLM_MODEL")
    if [ -n "$LLM_ROLE" ]; then
        llm_args+=(--role "$LLM_ROLE")
    fi
    if [ -n "$LLM_TIMEOUT" ]; then
        llm_args+=(--timeout "$LLM_TIMEOUT")
    fi
    if [ -n "$LLM_SYSTEM_PROMPT_FILE" ]; then
        llm_args+=(--system-prompt-file "$LLM_SYSTEM_PROMPT_FILE")
    fi

    local total_lines preview_limit preview_lines
    total_lines="$(printf "%s" "$output" | wc -l | tr -d ' ')"
    preview_limit=15
    preview_lines="$(printf "%s" "$output" | tail -n "$preview_limit")"
    if [ -n "$preview_lines" ]; then
        log "🧾 LLM 输入片段 (共 ${total_lines:-0} 行，展示末尾 $preview_limit 行)："
        while IFS= read -r preview_line; do
            log "   $preview_line"
        done <<< "$preview_lines"
    fi
    log "🤖 正在请求 LLM (role=${LLM_ROLE:-unknown}, stage=${CURRENT_STAGE:-unknown})"

    local llm_input="$output"
    local meta_block=""
    if [ -n "$last_response" ]; then
        meta_block+="[monitor-meta] last_response: ${last_response}"$'\n'
    fi
    meta_block+="[monitor-meta] same_response_count: ${same_response_count}"$'\n'
    if [ -n "$CURRENT_STAGE" ] && [ "$CURRENT_STAGE" != "unknown" ]; then
        meta_block+="[monitor-meta] stage: ${CURRENT_STAGE}"$'\n'
    fi
    if [ -n "$STAGE_HISTORY" ]; then
        meta_block+="[monitor-meta] stage_history: ${STAGE_HISTORY}"$'\n'
    fi
    if [ -n "$meta_block" ]; then
        llm_input="${llm_input}"$'\n\n'"${meta_block}"
    fi

    local response
    if [ -n "$LLM_API_KEY" ]; then
        response=$(AI_MONITOR_LLM_API_KEY="$LLM_API_KEY" python3 "$llm_script" "${llm_args[@]}" 2>>"$LOG_FILE" <<<"$llm_input") || response=""
    else
        response=$(python3 "$llm_script" "${llm_args[@]}" 2>>"$LOG_FILE" <<<"$llm_input") || response=""
    fi

    response=$(echo "$response" | head -1 | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    if [ -z "$response" ]; then
        log "⚠️  LLM 调用失败或返回空内容，本轮不发送"
        response="WAIT"
    fi
    echo "$response"
}

# ============================================
# 主逻辑
# ============================================

previous_output=""
last_change_time=$(date +%s)
last_response=""
same_response_count=0

log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "🧠 Claude Code LLM 监工脚本已启动"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "📍 监控目标: $TMUX_SESSION:$TMUX_WINDOW.$TMUX_PANE"
log "⏱️  检查间隔: ${CHECK_INTERVAL}秒"
log "⏳ 空闲阈值: ${MIN_IDLE_TIME}秒"
log "🧠 模式: LLM 监工 (model=$LLM_MODEL, role=$LLM_ROLE)"
log "🌐 base-url: $LLM_BASE_URL"
if [ -n "$LLM_API_KEY" ]; then
    log "🔑 api-key: set"
else
    log "🔑 api-key: not set"
fi
log "📝 日志文件: $LOG_FILE"
log "🆔 进程PID: $$"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 保存 PID
echo $$ > "$PID_FILE"

# 清理函数
cleanup() {
    log "🛑 收到终止信号，正在退出..."
    rm -f "$PID_FILE"
    exit 0
}

trap cleanup SIGTERM SIGINT

while true; do
    # 检查 tmux 会话是否存在
    if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        log "❌ tmux 会话 '$TMUX_SESSION' 不存在，退出监控"
        rm -f "$PID_FILE"
        exit 1
    fi

    # 捕获当前面板输出（最近50行以获取更多上下文）
    current_output=$(tmux capture-pane -t "$TMUX_SESSION:$TMUX_WINDOW.$TMUX_PANE" -p -S -50 2>/dev/null)

    if [ $? -ne 0 ]; then
        log "❌ 无法访问面板，退出监控"
        rm -f "$PID_FILE"
        exit 1
    fi

    update_stage_tracker "$current_output"

    current_time=$(date +%s)

    # 检查输出是否有变化
    if [ "$current_output" != "$previous_output" ]; then
        last_change_time=$current_time
        previous_output="$current_output"
        same_response_count=0  # 重置计数器
    else
        idle_duration=$((current_time - last_change_time))

        if [ $idle_duration -ge $MIN_IDLE_TIME ]; then
            response=$(decide_response_llm "$current_output" "$last_response" "$same_response_count")

            # 检查是否需要等待
            if [ "$response" != "WAIT" ]; then
                # 防止重复发送相同回复
                if [ "$response" = "$last_response" ]; then
                    ((same_response_count++))
                else
                    same_response_count=0
                fi

                if [ $same_response_count -ge $MAX_RETRY_SAME ]; then
                    log "⚠️  相同回复已连续发送 ${same_response_count} 次，建议人工介入或调整提示词/阈值"
                fi

                log "🔄 空闲 ${idle_duration}秒，LLM 回复: '$response'"
                send_command "$response"

                last_response="$response"
            fi
            last_change_time=$current_time
        else
            :
        fi
    fi

    sleep $CHECK_INTERVAL
done
