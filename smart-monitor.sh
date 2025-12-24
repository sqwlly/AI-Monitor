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
REQUERY_SAME_OUTPUT_AFTER="${AI_MONITOR_LLM_REQUERY_SAME_OUTPUT_AFTER:-0}"  # 同一面板输出快照允许再次请求 LLM 的最小间隔（秒）；0=永不重复请求

CURRENT_STAGE="unknown"
STAGE_HISTORY=""
AUTO_ROLE_CURRENT="monitor"
AUTO_ROLE_LAST_SWITCH_TIME=0
AUTO_ROLE_COOLDOWN_S="${AI_MONITOR_AUTO_ROLE_COOLDOWN_S:-60}"
AUTO_ROLE_STABLE_COUNT="${AI_MONITOR_AUTO_ROLE_STABLE_COUNT:-2}"
LAST_DETECTED_STAGE="unknown"
STAGE_STABLE_COUNT=0
UNKNOWN_STAGE_STREAK=0
STAGE_SCORE_THRESHOLD="${AI_MONITOR_STAGE_SCORE_THRESHOLD:-3}"
STAGE_SCORE_MARGIN="${AI_MONITOR_STAGE_SCORE_MARGIN:-1}"
LAST_STAGE_DETECTED="unknown"
LAST_STAGE_SCORE=0
STAGE_HINT_LAST=""
STAGE_HINT_STABLE_COUNT=0
STAGE_HINT_LAST_APPLIED_AT=0
STAGE_HINT_STABLE_REQUIRED="${AI_MONITOR_STAGE_HINT_STABLE_REQUIRED:-2}"
STAGE_HINT_COOLDOWN_S="${AI_MONITOR_STAGE_HINT_COOLDOWN_S:-30}"
LLM_STAGE_HINT=""

if ! [[ "$LOG_MAX_BYTES" =~ ^[0-9]+$ ]]; then
    LOG_MAX_BYTES=10485760
fi
if ! [[ "$REQUERY_SAME_OUTPUT_AFTER" =~ ^[0-9]+$ ]]; then
    REQUERY_SAME_OUTPUT_AFTER=0
fi
if ! [[ "$AUTO_ROLE_COOLDOWN_S" =~ ^[0-9]+$ ]]; then
    AUTO_ROLE_COOLDOWN_S=60
fi
if ! [[ "$AUTO_ROLE_STABLE_COUNT" =~ ^[0-9]+$ ]]; then
    AUTO_ROLE_STABLE_COUNT=2
fi
if ! [[ "$STAGE_SCORE_THRESHOLD" =~ ^[0-9]+$ ]]; then
    STAGE_SCORE_THRESHOLD=3
fi
if ! [[ "$STAGE_SCORE_MARGIN" =~ ^[0-9]+$ ]]; then
    STAGE_SCORE_MARGIN=1
fi
if ! [[ "$STAGE_HINT_STABLE_REQUIRED" =~ ^[0-9]+$ ]]; then
    STAGE_HINT_STABLE_REQUIRED=2
fi
if ! [[ "$STAGE_HINT_COOLDOWN_S" =~ ^[0-9]+$ ]]; then
    STAGE_HINT_COOLDOWN_S=30
fi

# 日志配置
LOG_DIR="$HOME/.tmux-monitor"
TARGET_ID=""
START_TIME="$(date +%s)"

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
        log_size="$(wc -c < "$LOG_FILE" 2>/dev/null | tr -d '[:space:]' || echo 0)"
        if [ "$log_size" -gt "$LOG_MAX_BYTES" ]; then
            local tmp
            tmp="$(mktemp "${LOG_FILE}.tmp.XXXXXX" 2>/dev/null || mktemp "${LOG_DIR}/smart-monitor.tmp.XXXXXX" 2>/dev/null || mktemp -t smart-monitor 2>/dev/null || echo "")"
            if [ -z "$tmp" ]; then
                echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️  无法创建临时文件，跳过日志截断" >&2
            else
                tail -c "$LOG_MAX_BYTES" "$LOG_FILE" > "$tmp" 2>/dev/null || true
                mv "$tmp" "$LOG_FILE"
            fi
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

is_dangerous_command() {
    local cmd="$1"

    printf "%s" "$cmd" | grep -qiE '(^|[[:space:]])rm[[:space:]]+-[[:alnum:]-]*r[[:alnum:]-]*f([[:space:]]|$)' && return 0
    printf "%s" "$cmd" | grep -qiE '(^|[[:space:]])rm[[:space:]]+-[[:alnum:]-]*f[[:alnum:]-]*r([[:space:]]|$)' && return 0
    printf "%s" "$cmd" | grep -qiE '(^|[[:space:]])git[[:space:]]+reset[[:space:]]+--hard([[:space:]]|$)' && return 0
    printf "%s" "$cmd" | grep -qiE '(^|[[:space:]])git[[:space:]]+clean([[:space:]]|$).*-[[:alnum:]]*(fdx|xdf)([[:space:]]|$)' && return 0
    printf "%s" "$cmd" | grep -qiE '(^|[[:space:]])git[[:space:]]+push([[:space:]]|$).*--force(-with-lease)?([[:space:]]|$)' && return 0
    printf "%s" "$cmd" | grep -qiE '(^|[[:space:]])mkfs(\\.|[[:space:]])' && return 0
    printf "%s" "$cmd" | grep -qiE '(^|[[:space:]])wipefs([[:space:]]|$)' && return 0
    printf "%s" "$cmd" | grep -qiE '(^|[[:space:]])dd([[:space:]]|$).*([[:space:]]|^)if=' && return 0

    return 1
}

validate_response() {
    local response="${1:-}"

    response="$(printf "%s" "$response" | head -1 | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

    if [ -z "$response" ] || [ "$response" = "WAIT" ]; then
        echo "WAIT"
        return 0
    fi

    if is_dangerous_command "$response"; then
        log "⛔️ 命中危险命令黑名单，已强制替换为 WAIT: $response"
        echo "WAIT"
        return 0
    fi

    echo "$response"
}

hash_text() {
    local input="$1"
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

PANE_ID="$(tmux display-message -p -t "$TMUX_SESSION:$TMUX_WINDOW.$TMUX_PANE" "#{pane_id}" 2>/dev/null || echo "")"
PANE_ID="${PANE_ID#%}"
if [ -n "$PANE_ID" ]; then
    TARGET_ID="$(hash_text "$PANE_ID" 2>/dev/null || echo "")"
fi
if [ -z "$TARGET_ID" ]; then
    TARGET_ID="$(hash_text "$TARGET" 2>/dev/null || echo "")"
fi
if [ -z "$TARGET_ID" ]; then
    echo "❌ 无法生成目标 ID（缺少哈希工具）"
    exit 1
fi

LOG_FILE="$LOG_DIR/smart_${TARGET_ID}.log"
PID_FILE="$LOG_DIR/smart_${TARGET_ID}.pid"

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

score_stage_from_output() {
    local text_lower
    text_lower="$(printf "%s" "$1" | tr '[:upper:]' '[:lower:]')"

    local score_blocked=0
    local score_fixing=0
    local score_testing=0
    local score_coding=0
    local score_refining=0
    local score_planning=0
    local score_documenting=0
    local score_release=0
    local score_done=0
    local score_reviewing=0
    local score_waiting=0

    if printf "%s" "$text_lower" | grep -qE "(pending approval|on hold|blocked|waiting for input|press (enter|any key)|hit enter to continue|输入以继续|等待输入|请确认|确认\\s*\\(|\\[y/n\\]|\\(y/n\\))"; then
        score_blocked=$((score_blocked + 3))
    fi

    # waiting: 等待用户输入或外部响应
    if printf "%s" "$text_lower" | grep -qE "(waiting for|awaiting|pending|等待中|挂起)"; then
        score_waiting=$((score_waiting + 2))
    fi

    if printf "%s" "$text_lower" | grep -qE "(traceback|stack trace|segmentation fault|segfault|panic|assertion failed)"; then
        score_fixing=$((score_fixing + 5))
    fi
    if printf "%s" "$text_lower" | grep -qE "(error|exception|failed|failure|cannot|unable to|fatal)"; then
        score_fixing=$((score_fixing + 3))
    fi
    if printf "%s" "$text_lower" | grep -qE '(^|[^[:alnum:]_])bug([^[:alnum:]_]|$)'; then
        score_fixing=$((score_fixing + 2))
    fi
    if printf "%s" "$text_lower" | grep -qE "(错误|异常|失败|崩溃|回溯)"; then
        score_fixing=$((score_fixing + 3))
    fi

    if printf "%s" "$text_lower" | grep -qE "(pytest|jest|go test|cargo test|npm test|pnpm test|yarn test|unit test|integration test|coverage|e2e)"; then
        score_testing=$((score_testing + 4))
    fi
    if printf "%s" "$text_lower" | grep -qE "(tests pass|test pass|passed|\\bpass\\b)"; then
        score_testing=$((score_testing + 1))
    fi
    if printf "%s" "$text_lower" | grep -qE "(测试|单测|用例|回归|覆盖率|集成测试|端到端)"; then
        score_testing=$((score_testing + 3))
    fi

    if printf "%s" "$text_lower" | grep -qE "(apply_patch|git diff|git status|create file|created file|update file|writing|implemented|implementing)"; then
        score_coding=$((score_coding + 3))
    fi
    if printf "%s" "$text_lower" | grep -qE "(function|class|def |public |private |interface |type |struct )"; then
        score_coding=$((score_coding + 1))
    fi
    if printf "%s" "$text_lower" | grep -qE "(实现|编码|写代码|新增|添加功能|修复代码)"; then
        score_coding=$((score_coding + 2))
    fi

    if printf "%s" "$text_lower" | grep -qE "(refactor|optimi|cleanup|polish|format|lint|prettier|gofmt|ruff|eslint|black)"; then
        score_refining=$((score_refining + 3))
    fi
    if printf "%s" "$text_lower" | grep -qE "(重构|优化|整理|格式化|静态检查)"; then
        score_refining=$((score_refining + 2))
    fi

    if printf "%s" "$text_lower" | grep -qE "(plan|todo|design|spec|architecture|requirement|explain this codebase)"; then
        score_planning=$((score_planning + 2))
    fi
    if printf "%s" "$text_lower" | grep -qE "(计划|设计|需求|架构|方案|拆分)"; then
        score_planning=$((score_planning + 2))
    fi

    if printf "%s" "$text_lower" | grep -qE "(readme|documentation|docs|guide|changelog)"; then
        score_documenting=$((score_documenting + 2))
    fi
    if printf "%s" "$text_lower" | grep -qE "(文档|说明|使用方法|指南|更新日志)"; then
        score_documenting=$((score_documenting + 2))
    fi

    if printf "%s" "$text_lower" | grep -qE "(deploy|release|publish|ship|delivery)"; then
        score_release=$((score_release + 2))
    fi
    if printf "%s" "$text_lower" | grep -qE "(上线|发布|提测|发版)"; then
        score_release=$((score_release + 2))
    fi

    if printf "%s" "$text_lower" | grep -qE "(done|complete|all tasks completed|ready to ship|finalized)"; then
        score_done=$((score_done + 2))
    fi
    if printf "%s" "$text_lower" | grep -qE "(已完成|完成|结束|收尾)"; then
        score_done=$((score_done + 2))
    fi

    # reviewing: 代码审查阶段
    if printf "%s" "$text_lower" | grep -qE "(review|pr|pull request|merge request|code review|审查|评审|cr)"; then
        score_reviewing=$((score_reviewing + 3))
    fi
    if printf "%s" "$text_lower" | grep -qE "(lgtm|approve|approved|request changes)"; then
        score_reviewing=$((score_reviewing + 2))
    fi

    local best_stage="unknown"
    local best_score=0
    local second_score=0

    local stage score
    for stage in blocked fixing testing coding refining planning documenting release done reviewing waiting; do
        score=0
        case "$stage" in
            blocked) score="$score_blocked" ;;
            fixing) score="$score_fixing" ;;
            testing) score="$score_testing" ;;
            coding) score="$score_coding" ;;
            refining) score="$score_refining" ;;
            planning) score="$score_planning" ;;
            documenting) score="$score_documenting" ;;
            release) score="$score_release" ;;
            done) score="$score_done" ;;
            reviewing) score="$score_reviewing" ;;
            waiting) score="$score_waiting" ;;
        esac

        if [ "$score" -gt "$best_score" ]; then
            second_score="$best_score"
            best_score="$score"
            best_stage="$stage"
        elif [ "$score" -gt "$second_score" ]; then
            second_score="$score"
        fi
    done

    if [ "$best_score" -lt "$STAGE_SCORE_THRESHOLD" ]; then
        best_stage="unknown"
    elif [ $((best_score - second_score)) -lt "$STAGE_SCORE_MARGIN" ]; then
        best_stage="unknown"
    fi

    printf "%s\t%s\n" "$best_stage" "$best_score"
}

is_valid_stage_label() {
    case "${1:-}" in
        planning|coding|testing|fixing|refining|reviewing|documenting|release|done|blocked|waiting|unknown) return 0 ;;
        *) return 1 ;;
    esac
}

parse_llm_structured_output() {
    local raw="${1:-}"
    raw="$(printf "%s" "$raw" | head -1 | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    LLM_STAGE_HINT=""

    if [ -z "$raw" ]; then
        echo "WAIT"
        return 0
    fi
    if [ "$raw" = "WAIT" ]; then
        echo "WAIT"
        return 0
    fi

    local stage_hint=""
    local cmd=""
    local re='^[Ss][Tt][Aa][Gg][Ee][=:][[:space:]]*([a-z-]+)[[:space:]]*;[[:space:]]*[Cc][Mm][Dd][=:][[:space:]]*(.*)$'
    if [[ "$raw" =~ $re ]]; then
        stage_hint="$(printf "%s" "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')"
        cmd="${BASH_REMATCH[2]}"
        cmd="$(printf "%s" "$cmd" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        if is_valid_stage_label "$stage_hint"; then
            LLM_STAGE_HINT="$stage_hint"
        fi
        if [ -z "$cmd" ]; then
            cmd="WAIT"
        fi
        echo "$cmd"
        return 0
    fi

    echo "$raw"
}

apply_stage_hint_if_needed() {
    local now_s="${1:-0}"
    local hint="${2:-}"

    hint="$(printf "%s" "$hint" | tr '[:upper:]' '[:lower:]')"
    if [ -z "$hint" ] || ! is_valid_stage_label "$hint" || [ "$hint" = "unknown" ]; then
        STAGE_HINT_LAST=""
        STAGE_HINT_STABLE_COUNT=0
        return 0
    fi

    if [ "${LAST_STAGE_DETECTED:-unknown}" != "unknown" ]; then
        STAGE_HINT_LAST=""
        STAGE_HINT_STABLE_COUNT=0
        return 0
    fi

    if [ "$hint" = "$STAGE_HINT_LAST" ]; then
        STAGE_HINT_STABLE_COUNT=$((STAGE_HINT_STABLE_COUNT + 1))
    else
        STAGE_HINT_LAST="$hint"
        STAGE_HINT_STABLE_COUNT=1
    fi

    if [ "$STAGE_HINT_STABLE_COUNT" -lt "$STAGE_HINT_STABLE_REQUIRED" ]; then
        return 0
    fi
    if [ "$STAGE_HINT_LAST_APPLIED_AT" -ne 0 ] && [ $((now_s - STAGE_HINT_LAST_APPLIED_AT)) -lt "$STAGE_HINT_COOLDOWN_S" ]; then
        return 0
    fi

    if [ "$CURRENT_STAGE" != "$hint" ]; then
        CURRENT_STAGE="$hint"
        append_stage_history "$CURRENT_STAGE"
        STAGE_HINT_LAST_APPLIED_AT="$now_s"
        log "🧭 采用 LLM 阶段建议 -> $CURRENT_STAGE"
    fi
}

auto_role_candidate_for_stage() {
    local stage="${1:-unknown}"
    case "$stage" in
        fixing) echo "senior-engineer" ;;
        testing) echo "test-manager" ;;
        planning) echo "architect" ;;
        coding|refining) echo "senior-engineer" ;;
        reviewing) echo "senior-engineer" ;;
        documenting) echo "monitor" ;;
        release|done|blocked|waiting) echo "monitor" ;;
        *) echo "monitor" ;;
    esac
}

choose_effective_role() {
    local now_s="${1:-0}"
    local configured="${LLM_ROLE:-monitor}"

    configured="$(printf "%s" "$configured" | tr '[:upper:]' '[:lower:]')"
    if [ -z "$configured" ]; then
        configured="monitor"
    fi

    if [ "$configured" != "auto" ]; then
        echo "$configured"
        return 0
    fi

    local candidate
    candidate="$(auto_role_candidate_for_stage "$CURRENT_STAGE")"

    if [ -z "$AUTO_ROLE_CURRENT" ]; then
        AUTO_ROLE_CURRENT="monitor"
    fi

    if [ "$STAGE_STABLE_COUNT" -ge "$AUTO_ROLE_STABLE_COUNT" ] && [ "$candidate" != "$AUTO_ROLE_CURRENT" ]; then
        if [ "$AUTO_ROLE_LAST_SWITCH_TIME" -eq 0 ] || [ $((now_s - AUTO_ROLE_LAST_SWITCH_TIME)) -ge "$AUTO_ROLE_COOLDOWN_S" ]; then
            AUTO_ROLE_CURRENT="$candidate"
            AUTO_ROLE_LAST_SWITCH_TIME="$now_s"
            log "🎭 auto 选角切换 -> ${AUTO_ROLE_CURRENT} (stage=${CURRENT_STAGE}, stage_stable=${STAGE_STABLE_COUNT}, cooldown=${AUTO_ROLE_COOLDOWN_S}s)"
        fi
    fi

    echo "$AUTO_ROLE_CURRENT"
}

update_stage_tracker() {
    local detected_stage detected_score
    local score_line
    score_line="$(score_stage_from_output "$1")"
    IFS=$'\t' read -r detected_stage detected_score <<< "$score_line"

    LAST_STAGE_DETECTED="${detected_stage:-unknown}"
    LAST_STAGE_SCORE="${detected_score:-0}"

    if [ -z "$detected_stage" ] || [ "$detected_stage" = "unknown" ]; then
        UNKNOWN_STAGE_STREAK=$((UNKNOWN_STAGE_STREAK + 1))
        return
    fi
    UNKNOWN_STAGE_STREAK=0

    if [ "$detected_stage" = "$LAST_DETECTED_STAGE" ]; then
        STAGE_STABLE_COUNT=$((STAGE_STABLE_COUNT + 1))
    else
        LAST_DETECTED_STAGE="$detected_stage"
        STAGE_STABLE_COUNT=1
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
    local idle_seconds="${4:-0}"
    local now_s="${5:-0}"

    local recent_output
    recent_output="$(echo "$output" | tail -n 10)"

    # 仍然保留关键安全/打断保护逻辑（避免无意义请求与危险操作）
    local output_lower
    output_lower=$(echo "$recent_output" | tr '[:upper:]' '[:lower:]')

    if echo "$recent_output" | grep -qE '(⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏|Running|Executing|Loading|Compiling|Building|Installing|Downloading)'; then
        log "⏸️ 检测到任务仍在运行中，返回 WAIT"
        echo "WAIT"
        return
    fi

    if echo "$output_lower" | grep -qE '(do you want to|would you like to|should i|shall i|confirm|are you sure|proceed\?|continue\?|\[y/n\]|\(y/n\)|yes/no)'; then
        if echo "$output_lower" | grep -qE '(delete|remove|drop|reset|force|overwrite|replace all|destructive|rm -rf|wipe)'; then
            log "⏸️ 检测到危险确认提示，返回 WAIT"
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

    local effective_role
    effective_role="$(choose_effective_role "$now_s")"

    local llm_args=(--base-url "$LLM_BASE_URL" --model "$LLM_MODEL" --role "$effective_role")
    if [ -n "$LLM_TIMEOUT" ]; then
        llm_args+=(--timeout "$LLM_TIMEOUT")
    fi
    if [ -n "$LLM_SYSTEM_PROMPT_FILE" ]; then
        llm_args+=(--system-prompt-file "$LLM_SYSTEM_PROMPT_FILE")
    fi

    local total_lines preview_limit preview_lines
    total_lines="$(printf "%s" "$output" | wc -l | tr -d ' ')"
    preview_limit=10
    preview_lines="$(printf "%s" "$output" | tail -n "$preview_limit")"
    if [ -n "$preview_lines" ]; then
        log "🧾 LLM 输入片段 (共 ${total_lines:-0} 行，展示末尾 $preview_limit 行)："
        while IFS= read -r preview_line; do
            log "   $preview_line"
        done <<< "$preview_lines"
        log " "
    fi
    log "🤖 正在请求 LLM (role_configured=${LLM_ROLE:-unknown}, role_effective=${effective_role:-unknown}, stage=${CURRENT_STAGE:-unknown})"

    local llm_input="$output"
    local meta_block=""
    if [ -n "$last_response" ]; then
        meta_block+="[monitor-meta] last_response: ${last_response}"$'\n'
    fi
    meta_block+="[monitor-meta] last_response_sent_at: ${LAST_RESPONSE_SENT_AT:-0}"$'\n'
    meta_block+="[monitor-meta] same_response_count: ${same_response_count}"$'\n'
    meta_block+="[monitor-meta] idle_seconds: ${idle_seconds}"$'\n'
    meta_block+="[monitor-meta] role_configured: ${LLM_ROLE:-unknown}"$'\n'
    meta_block+="[monitor-meta] role_effective: ${effective_role:-unknown}"$'\n'
    meta_block+="[monitor-meta] stage_stable_count: ${STAGE_STABLE_COUNT:-0}"$'\n'
    if [ -n "$CURRENT_STAGE" ] && [ "$CURRENT_STAGE" != "unknown" ]; then
        meta_block+="[monitor-meta] stage: ${CURRENT_STAGE}"$'\n'
    fi
    if [ -n "$STAGE_HISTORY" ]; then
        meta_block+="[monitor-meta] stage_history: ${STAGE_HISTORY}"$'\n'
    fi

    # 主动采集项目上下文（增强主观能动性）
    local project_context_script="${script_dir}/project_context.sh"
    if [ -f "$project_context_script" ] && [ "${AI_MONITOR_ENABLE_PROJECT_CONTEXT:-1}" = "1" ]; then
        local pane_cwd
        pane_cwd="$(tmux display-message -p -t "$TMUX_SESSION:$TMUX_WINDOW.$TMUX_PANE" '#{pane_current_path}' 2>/dev/null || echo "")"
        if [ -n "$pane_cwd" ] && [ -d "$pane_cwd" ]; then
            local project_ctx
            project_ctx="$(bash "$project_context_script" "$pane_cwd" 2>/dev/null | head -20)"
            if [ -n "$project_ctx" ]; then
                meta_block+=$'\n'"${project_ctx}"$'\n'
                log "📊 项目上下文已采集 (cwd=$pane_cwd)"
            fi
        else
            log "⚠️  无法获取面板工作目录，跳过项目上下文采集"
        fi
    fi

    if [ -n "$meta_block" ]; then
        llm_input="${llm_input}"$'\n\n'"${meta_block}"
    fi

    local response raw_response
    if [ -n "$LLM_API_KEY" ]; then
        response=$(AI_MONITOR_LLM_API_KEY="$LLM_API_KEY" python3 "$llm_script" "${llm_args[@]}" 2>>"$LOG_FILE" <<<"$llm_input") || response=""
    else
        response=$(python3 "$llm_script" "${llm_args[@]}" 2>>"$LOG_FILE" <<<"$llm_input") || response=""
    fi

    raw_response=$(echo "$response" | head -1 | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    if [ -z "$raw_response" ]; then
        log "⚠️  LLM 调用失败或返回空内容，本轮不发送"
        raw_response="WAIT"
    fi

    response="$(parse_llm_structured_output "$raw_response")"
    log " "
    if [ -n "${LLM_STAGE_HINT:-}" ]; then
        log "✨ LLM 输出: ${raw_response}  (stage_hint=${LLM_STAGE_HINT})"
    else
        log "✨ LLM 输出: $raw_response"
    fi
    if [ "$response" = "WAIT" ]; then
        log "⏸️ LLM 回复 WAIT，本轮不发送命令"
    fi
    log " "
    echo "$response"
}

# ============================================
# 主逻辑
# ============================================

previous_output=""
last_change_time=$(date +%s)
last_response=""
same_response_count=0
last_llm_output_hash=""
last_llm_output_hash_time=0
last_llm_skip_log_hash=""
LAST_RESPONSE_SENT_AT=0

log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "🧠 Claude Code LLM 监工脚本已启动"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "📍 监控目标: $TMUX_SESSION:$TMUX_WINDOW.$TMUX_PANE"
log "⏱️  检查间隔: ${CHECK_INTERVAL}秒"
log "⏳ 空闲阈值: ${MIN_IDLE_TIME}秒"
log "🧠 模式: LLM 监工 (model=$LLM_MODEL, role_configured=$LLM_ROLE)"
log "🌐 base-url: $LLM_BASE_URL"
log "🔁 同输出重请求: ${REQUERY_SAME_OUTPUT_AFTER}秒（0=不重复请求）"
if [ -n "$LLM_API_KEY" ]; then
    log "🔑 api-key: set"
else
    log "🔑 api-key: not set"
fi
log "📝 日志文件: $LOG_FILE"
log "🆔 进程PID: $$"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 保存 PID
{
    echo "$$"
    printf "target=%s\n" "$TARGET"
    printf "mode=smart\n"
    printf "start_time=%s\n" "$START_TIME"
} > "$PID_FILE"

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
        last_llm_output_hash=""
        last_llm_output_hash_time=0
        last_llm_skip_log_hash=""
    else
        idle_duration=$((current_time - last_change_time))

        if [ $idle_duration -ge $MIN_IDLE_TIME ]; then
            current_output_hash="$(hash_text "$current_output" 2>/dev/null || echo "")"
            if [ -n "$current_output_hash" ] && [ "$current_output_hash" = "$last_llm_output_hash" ]; then
                if [ "$REQUERY_SAME_OUTPUT_AFTER" -gt 0 ] && [ $((current_time - last_llm_output_hash_time)) -ge $REQUERY_SAME_OUTPUT_AFTER ]; then
                    :
                else
                    if [ "$last_llm_skip_log_hash" != "$current_output_hash" ]; then
                        log "⏭️ 输出未变化，已对该快照请求过 LLM，跳过重复请求"
                        last_llm_skip_log_hash="$current_output_hash"
                    fi
                    sleep $CHECK_INTERVAL
                    continue
                fi
            fi

            response=$(decide_response_llm "$current_output" "$last_response" "$same_response_count" "$idle_duration" "$current_time")
            response="$(validate_response "$response")"
            if [ -n "${LLM_STAGE_HINT:-}" ]; then
                apply_stage_hint_if_needed "$current_time" "$LLM_STAGE_HINT"
            fi
            if [ -n "$current_output_hash" ]; then
                last_llm_output_hash="$current_output_hash"
                last_llm_output_hash_time=$current_time
                last_llm_skip_log_hash=""
            fi

            # 检查是否需要等待
            if [ "$response" != "WAIT" ]; then
                # 防止重复发送相同回复（避免在无变化的交互界面里“刷屏/连发”）
                if [ "$response" = "$last_response" ]; then
                    ((same_response_count++))
                    if [ $same_response_count -ge $MAX_RETRY_SAME ]; then
                        log "⚠️  LLM 连续给出相同回复 ${same_response_count} 次，已停止重复发送，建议人工介入或调整提示词/阈值"
                    else
                        log "⏭️ 与上次发送相同，已跳过重复发送: '$response'"
                    fi
                else
                    same_response_count=0
                    log "🔄 空闲 ${idle_duration}秒，LLM 回复: '$response'"
                    send_command "$response"
                    last_response="$response"
                    LAST_RESPONSE_SENT_AT="$current_time"
                fi
            fi
        else
            :
        fi
    fi

    sleep $CHECK_INTERVAL
done
