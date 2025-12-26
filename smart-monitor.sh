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

# 脚本目录（供后续调用同目录下的 *.py / *.sh）
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ============================================
# 配置
# ============================================

CHECK_INTERVAL=8          # 检查间隔（秒）
MIN_IDLE_TIME=12          # 空闲阈值（秒）
MAX_RETRY_SAME=3          # 同一回复最大重试次数
LOG_MAX_BYTES="${AI_MONITOR_LOG_MAX_BYTES:-10485760}"  # 默认 10MB（超过则截断保留末尾）
MAX_STAGE_HISTORY=6       # 记录最近阶段变更
CAPTURE_LINES="${AI_MONITOR_CAPTURE_LINES:-120}"  # capture-pane 最近 N 行（越大上下文越充分，但会增加 LLM 输入）
BUSY_GRACE_S="${AI_MONITOR_BUSY_GRACE_S:-90}"  # 运行中关键词/Spinner 的“宽限期”（秒）；超过后视为可能卡住，允许询问 LLM
REQUERY_SAME_OUTPUT_AFTER="${AI_MONITOR_LLM_REQUERY_SAME_OUTPUT_AFTER:-30}"  # 同一面板输出快照再次请求 LLM 的最小间隔（秒）；0=永不重复请求
REQUERY_ON_REPEAT_AFTER="${AI_MONITOR_LLM_REQUERY_ON_REPEAT_AFTER:-16}"  # LLM 重复给出同一指令时的加速重试间隔（秒）；0=禁用

# 多Agent编排 / 决策仲裁（默认关闭，避免默认多倍调用成本）
ORCHESTRATOR_ENABLED="${AI_MONITOR_ORCHESTRATOR_ENABLED:-0}"
ARBITER_ENABLED="${AI_MONITOR_ARBITER_ENABLED:-0}"
ORCHESTRATOR_PIPELINE="${AI_MONITOR_PIPELINE:-vote}"

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
if ! [[ "$CAPTURE_LINES" =~ ^[0-9]+$ ]] || [ "$CAPTURE_LINES" -lt 10 ]; then
    CAPTURE_LINES=50
fi
if ! [[ "$BUSY_GRACE_S" =~ ^[0-9]+$ ]]; then
    BUSY_GRACE_S=90
fi
if ! [[ "$REQUERY_SAME_OUTPUT_AFTER" =~ ^[0-9]+$ ]]; then
    REQUERY_SAME_OUTPUT_AFTER=30
fi
if ! [[ "$REQUERY_ON_REPEAT_AFTER" =~ ^[0-9]+$ ]]; then
    REQUERY_ON_REPEAT_AFTER=16
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

    # 防御性处理：如果上游仍返回结构化输出，尝试再次解析出 CMD
    if printf "%s" "$response" | grep -qiE '^stage[=:]'; then
        response="$(parse_llm_structured_output "$response")"
        response="$(printf "%s" "$response" | head -1 | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    fi

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
    # 容错：允许 `;`/`,`/空格 作为 STAGE 与 CMD 的分隔符
    local re='^[Ss][Tt][Aa][Gg][Ee][=:][[:space:]]*([a-z-]+)[[:space:]]*([;,]|[[:space:]]+)[[:space:]]*[Cc][Mm][Dd][=:][[:space:]]*(.*)$'
    if [[ "$raw" =~ $re ]]; then
        stage_hint="$(printf "%s" "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')"
        cmd="${BASH_REMATCH[3]}"
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

# 关键安全/打断保护逻辑（避免无意义请求与危险操作）
should_force_wait_for_safety() {
    local recent_output="${1:-}"
    local idle_seconds="${2:-0}"
    local output_lower=""
    output_lower=$(printf "%s" "$recent_output" | tr '[:upper:]' '[:lower:]')

    # 注意：tmux capture-pane 捕获的是“屏幕快照”，历史的 Running/Spinner 文本可能会残留；
    # 这里使用“宽限期”判断：空闲时间较短时认为仍在跑，空闲时间过长则允许继续决策（可能已卡住/在等输入）。
    if printf "%s" "$recent_output" | grep -qE '(⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏|Running|Executing|Loading|Compiling|Building|Installing|Downloading)'; then
        if [ "$idle_seconds" -lt "$BUSY_GRACE_S" ]; then
            log "⏸️ 检测到运行中关键词/Spinner（idle=${idle_seconds}s < grace=${BUSY_GRACE_S}s），返回 WAIT"
            return 0
        fi
        log "⚠️ 检测到运行中关键词/Spinner但已空闲 ${idle_seconds}s（>= ${BUSY_GRACE_S}s），可能卡住，继续决策"
    fi

    if printf "%s" "$output_lower" | grep -qE '(do you want to|would you like to|should i|shall i|confirm|are you sure|proceed\?|continue\?|\[y/n\]|\(y/n\)|yes/no)'; then
        if printf "%s" "$output_lower" | grep -qE '(delete|remove|drop|reset|force|overwrite|replace all|destructive|rm -rf|wipe)'; then
            log "⏸️ 检测到危险确认提示，返回 WAIT"
            return 0
        fi
    fi

    return 1
}

build_decision_context() {
    local output="$1"
    local last_response="${2:-}"
    local same_response_count="${3:-0}"
    local idle_seconds="${4:-0}"
    local now_s="${5:-0}"
    local effective_role="${6:-monitor}"

    local llm_input="$output"
    local meta_block=""
    if [ -n "$last_response" ]; then
        meta_block+="[monitor-meta] last_response: ${last_response}"$'\n'
    fi
    meta_block+="[monitor-meta] last_response_sent_at: ${LAST_RESPONSE_SENT_AT:-0}"$'\n'
    meta_block+="[monitor-meta] same_response_count: ${same_response_count}"$'\n'
    meta_block+="[monitor-meta] idle_seconds: ${idle_seconds}"$'\n'
    meta_block+="[monitor-meta] consecutive_wait_count: ${consecutive_wait_count:-0}"$'\n'
    meta_block+="[monitor-meta] requery_same_output_after: ${REQUERY_SAME_OUTPUT_AFTER}"$'\n'
    meta_block+="[monitor-meta] requery_on_repeat_after: ${REQUERY_ON_REPEAT_AFTER}"$'\n'
    meta_block+="[monitor-meta] role_configured: ${LLM_ROLE:-unknown}"$'\n'
    meta_block+="[monitor-meta] role_effective: ${effective_role:-unknown}"$'\n'
    meta_block+="[monitor-meta] stage_stable_count: ${STAGE_STABLE_COUNT:-0}"$'\n'
    if [ -n "$CURRENT_STAGE" ] && [ "$CURRENT_STAGE" != "unknown" ]; then
        meta_block+="[monitor-meta] stage: ${CURRENT_STAGE}"$'\n'
    fi
    if [ -n "$STAGE_HISTORY" ]; then
        meta_block+="[monitor-meta] stage_history: ${STAGE_HISTORY}"$'\n'
    fi

    # 输出停滞诊断：上次命令发送后是否出现新输出变化
    local seconds_since_last_command=0
    local no_output_change_since_last_command=0
    if [ "${LAST_RESPONSE_SENT_AT:-0}" -gt 0 ]; then
        seconds_since_last_command=$((now_s - LAST_RESPONSE_SENT_AT))
        if [ "$idle_seconds" -ge "$seconds_since_last_command" ]; then
            no_output_change_since_last_command=1
        fi
    fi
    meta_block+="[monitor-meta] seconds_since_last_command: ${seconds_since_last_command}"$'\n'
    meta_block+="[monitor-meta] no_output_change_since_last_command: ${no_output_change_since_last_command}"$'\n'

    if [ "$no_output_change_since_last_command" -eq 1 ] && [ -n "$last_response" ]; then
        meta_block+=$'\n'"[warning] 上次命令发送后输出未变化（可能无效/未被执行/在等输入），请勿重复 last_response；优先给出不同的、可验证的最小诊断/推进命令，或输出 WAIT 等待更多信息。"$'\n'
    fi
    if [ "${consecutive_wait_count:-0}" -ge 2 ]; then
        meta_block+=$'\n'"[warning] 你已连续 ${consecutive_wait_count} 次输出 WAIT；如果仍无新信息，请尝试给出一个最小可验证命令来获取更多上下文，或明确说明需要哪些信息。"$'\n'
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

    # ========== 理解层集成 ==========
    # 注入意图摘要（帮助 LLM 理解用户目标）
    if [ "${AI_MONITOR_UNDERSTANDING_ENABLED:-1}" = "1" ] && [ -n "${MEMORY_SESSION_ID:-}" ]; then
        local intent_summary
        intent_summary=$(python3 "${script_dir}/intent_parser.py" summary "$MEMORY_SESSION_ID" 2>/dev/null || echo "")
        if [ -n "$intent_summary" ]; then
            meta_block+=$'\n'"${intent_summary}"$'\n'
            log "🎯 意图上下文已注入"
        fi

        # 注入错误分析摘要（帮助 LLM 理解错误根因）
        local error_summary
        error_summary=$(python3 "${script_dir}/error_analyzer.py" summary "$MEMORY_SESSION_ID" 2>/dev/null || echo "")
        if [ -n "$error_summary" ]; then
            meta_block+=$'\n'"${error_summary}"$'\n'
            log "🔍 错误分析已注入"
        fi

        # 注入进度摘要（帮助 LLM 了解任务进展）
        local progress_summary
        progress_summary=$(python3 "${script_dir}/progress_monitor.py" summary "$MEMORY_SESSION_ID" 2>/dev/null || echo "")
        if [ -n "$progress_summary" ]; then
            meta_block+=$'\n'"${progress_summary}"$'\n'
            log "📈 进度状态已注入"
        fi

        # ========== Phase 1-3 新模块摘要注入 ==========
        # 注入目标分解状态（帮助 LLM 了解目标层次）
        local goal_summary
        goal_summary=$(python3 "${script_dir}/goal_decomposer.py" status "$MEMORY_SESSION_ID" 2>/dev/null || echo "")
        if [ -n "$goal_summary" ]; then
            meta_block+=$'\n'"${goal_summary}"$'\n'
            log "🎯 目标状态已注入"
        fi

        # 注入代码变更摘要（帮助 LLM 了解最近改动）
        local change_summary
        change_summary=$(python3 "${script_dir}/change_analyzer.py" summary "$MEMORY_SESSION_ID" 2>/dev/null || echo "")
        if [ -n "$change_summary" ]; then
            meta_block+=$'\n'"${change_summary}"$'\n'
            log "📝 变更分析已注入"
        fi

        # 注入工作记忆摘要（帮助 LLM 了解当前上下文）
        local memory_summary
        memory_summary=$(python3 "${script_dir}/working_memory.py" context "$MEMORY_SESSION_ID" 2>/dev/null || echo "")
        if [ -n "$memory_summary" ]; then
            meta_block+=$'\n'"${memory_summary}"$'\n'
            log "🧠 工作记忆已注入"
        fi

        # 注入跨会话知识推荐（帮助 LLM 利用历史经验）
        local knowledge_summary
        knowledge_summary=$(python3 "${script_dir}/session_linker.py" summary "$MEMORY_SESSION_ID" 2>/dev/null || echo "")
        if [ -n "$knowledge_summary" ]; then
            meta_block+=$'\n'"${knowledge_summary}"$'\n'
            log "📚 跨会话知识已注入"
        fi

        # ========== Phase 4 学习模块集成 ==========
        # 注入匹配的历史模式（帮助 LLM 参考历史成功经验）
        local pattern_summary
        pattern_summary=$(python3 "${script_dir}/pattern_learner.py" match "$MEMORY_SESSION_ID" "${output:0:500}" 2>/dev/null || echo "")
        if [ -n "$pattern_summary" ]; then
            meta_block+=$'\n'"${pattern_summary}"$'\n'
            log "🎓 历史模式已注入"
        fi

        # 注入策略建议（基于历史效果优化）
        local strategy_hint
        strategy_hint=$(python3 "${script_dir}/strategy_optimizer.py" suggest "${CURRENT_STAGE:-unknown}" 2>/dev/null || echo "")
        if [ -n "$strategy_hint" ]; then
            meta_block+=$'\n'"[strategy] ${strategy_hint}"$'\n'
            log "📈 策略建议已注入"
        fi

        # ========== Phase 5 主动规划模块集成 ==========
        # 检查主动干预建议
        local proactive_suggestion
        proactive_suggestion=$(python3 "${script_dir}/proactive_engine.py" check "$MEMORY_SESSION_ID" "${output:0:1000}" --stage "${CURRENT_STAGE:-unknown}" 2>/dev/null || echo "")
        if [ -n "$proactive_suggestion" ]; then
            meta_block+=$'\n'"${proactive_suggestion}"$'\n'
            log "🔮 主动干预建议已注入"
        fi

        # 注入当前计划状态（帮助 LLM 了解整体计划）
        local plan_status
        plan_status=$(python3 "${script_dir}/plan_generator.py" status "$MEMORY_SESSION_ID" 2>/dev/null || echo "")
        if [ -n "$plan_status" ]; then
            meta_block+=$'\n'"${plan_status}"$'\n'
            log "📋 计划状态已注入"
        fi
    fi

    # 注入历史决策（帮助 LLM 避免重复）
    if [ "${AI_MONITOR_MEMORY_ENABLED:-1}" = "1" ] && [ -n "${MEMORY_SESSION_ID:-}" ]; then
        local recent_decisions
        recent_decisions=$(python3 "${script_dir}/memory_manager.py" recent-decisions "$MEMORY_SESSION_ID" 5 2>/dev/null || echo "")
        if [ -n "$recent_decisions" ]; then
            meta_block+=$'\n'"[history] 最近5次决策（避免重复）:"$'\n'"${recent_decisions}"$'\n'
        fi
    fi

    # 智能建议：当重复次数高时，生成替代方案提示
    if [ "$same_response_count" -ge 2 ]; then
        local stage_specific_hint=""
        case "${CURRENT_STAGE:-unknown}" in
            testing)
                stage_specific_hint="尝试: 1)查看测试日志 2)运行单个失败用例 3)检查测试环境配置"
                ;;
            fixing)
                stage_specific_hint="尝试: 1)打印更多调试信息 2)检查相关依赖版本 3)搜索类似错误的解决方案"
                ;;
            coding)
                stage_specific_hint="尝试: 1)检查语法错误 2)查看 import/依赖 3)简化实现方案"
                ;;
            blocked)
                stage_specific_hint="尝试: 1)检查权限问题 2)查看系统资源 3)等待外部依赖"
                ;;
            *)
                stage_specific_hint="尝试完全不同的诊断命令或输出 WAIT"
                ;;
        esac
        meta_block+=$'\n'"[warning] ⚠️ 你的指令已重复 ${same_response_count} 次无效。${stage_specific_hint}"$'\n'
    fi

    if [ -n "$meta_block" ]; then
        llm_input="${llm_input}"$'\n\n'"${meta_block}"
    fi

    printf "%s" "$llm_input"
}

decide_response_orchestrated() {
    local output="$1"
    local last_response="${2:-}"
    local same_response_count="${3:-0}"
    local idle_seconds="${4:-0}"
    local now_s="${5:-0}"

    local orchestrator_script="${script_dir}/agent_orchestrator.py"
    if [ ! -f "$orchestrator_script" ]; then
        log "⚠️ 未找到多Agent编排器: $orchestrator_script，回退单Agent"
        decide_response_llm "$output" "$last_response" "$same_response_count" "$idle_seconds" "$now_s"
        return
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        log "❌ 未找到 python3，无法启用多Agent编排，返回 WAIT"
        echo "WAIT"
        return
    fi

    local effective_role
    effective_role="$(choose_effective_role "$now_s")"

    local total_lines preview_limit preview_lines
    total_lines="$(printf "%s" "$output" | wc -l | tr -d ' ')"
    preview_limit=10
    preview_lines="$(printf "%s" "$output" | tail -n "$preview_limit")"
    if [ -n "$preview_lines" ]; then
        log "🧾 编排输入片段 (共 ${total_lines:-0} 行，展示末尾 $preview_limit 行)："
        while IFS= read -r preview_line; do
            log "   $preview_line"
        done <<< "$preview_lines"
        log " "
    fi

    local context
    context="$(build_decision_context "$output" "$last_response" "$same_response_count" "$idle_seconds" "$now_s" "$effective_role")"

    log "🗳️ 正在请求多Agent编排 (pipeline=${ORCHESTRATOR_PIPELINE}, stage=${CURRENT_STAGE:-unknown})"
    local orch_json
    orch_json=$(python3 "$orchestrator_script" run --pipeline "$ORCHESTRATOR_PIPELINE" --stage "${CURRENT_STAGE:-unknown}" --output full 2>>"$LOG_FILE" <<<"$context") || orch_json=""
    if [ -z "$orch_json" ]; then
        log "⚠️ 多Agent编排返回空内容，回退单Agent"
        decide_response_llm "$output" "$last_response" "$same_response_count" "$idle_seconds" "$now_s"
        return
    fi

    local -a orch_lines
    mapfile -t orch_lines < <(
        python3 - "$ORCHESTRATOR_PIPELINE" <<'PY' <<<"$orch_json"
import json
import re
import sys
from collections import Counter

pipeline = sys.argv[1] if len(sys.argv) > 1 else "vote"
data = json.loads(sys.stdin.read() or "{}")
final_response = (data.get("final_response") or "").strip()
reason = (data.get("reason") or "").strip()
responses = data.get("responses") or []

def is_wait(text: str) -> bool:
    return (text or "").strip().upper() == "WAIT"

def action_type(text: str) -> str:
    return "wait" if is_wait(text) or not (text or "").strip() else "command"

danger_patterns = [
    r"(^|\s)rm\s+-[\w-]*r[\w-]*f(\s|$)",
    r"(^|\s)rm\s+-[\w-]*f[\w-]*r(\s|$)",
    r"(^|\s)git\s+reset\s+--hard(\s|$)",
    r"(^|\s)git\s+clean(\s|$).*-([\w-]*(fdx|xdf))(\s|$)",
    r"(^|\s)git\s+push(\s|$).*--force(-with-lease)?(\s|$)",
    r"(^|\s)mkfs(\.|\s)",
    r"(^|\s)wipefs(\s|$)",
    r"(^|\s)dd(\s|$).*(\s|^)if=",
]

def safety_score(text: str) -> float:
    t = (text or "").strip()
    for p in danger_patterns:
        if re.search(p, t, re.IGNORECASE):
            return 0.0
    return 1.0

valid_non_wait = []
stage_hints = []
for r in responses:
    resp = (r.get("response") or "").strip()
    if not resp:
        continue
    if not is_wait(resp) and not r.get("error"):
        valid_non_wait.append(resp)
    hint = (r.get("stage_hint") or "").strip().lower()
    if hint:
        stage_hints.append(hint)

stage_hint = ""
if stage_hints:
    stage_hint = Counter(stage_hints).most_common(1)[0][0]

suggestions = []

if final_response:
    base_conf = 0.75 if not is_wait(final_response) else 0.6
    if valid_non_wait:
        votes = Counter(valid_non_wait)
        _, count = votes.most_common(1)[0]
        consensus = count / max(1, len(valid_non_wait))
        base_conf = min(0.95, max(base_conf, 0.7 + 0.2 * consensus))
    suggestions.append({
        "source": "llm",
        "action_type": action_type(final_response),
        "content": final_response,
        "confidence": round(base_conf, 3),
        "priority": 1,
        "safety_score": safety_score(final_response),
        "reasoning": f"orchestrator(pipeline={pipeline}): {reason}" if reason else f"orchestrator(pipeline={pipeline})",
    })

for r in responses:
    resp = (r.get("response") or "").strip()
    if not resp:
        continue
    if r.get("error"):
        continue
    agent_id = (r.get("agent_id") or "").strip()
    role = (r.get("role") or "").strip()
    hint = (r.get("stage_hint") or "").strip()
    latency = r.get("latency_ms", 0)
    base_conf = 0.7 if not is_wait(resp) else 0.55
    suggestions.append({
        "source": "llm",
        "action_type": action_type(resp),
        "content": resp,
        "confidence": round(base_conf, 3),
        "priority": 0,
        "safety_score": safety_score(resp),
        "reasoning": f"agent={agent_id}, role={role}, stage_hint={hint}, latency_ms={latency}",
    })

print(final_response.replace("\n", " ").strip())
print(stage_hint)
print(json.dumps(suggestions, ensure_ascii=False))
print(reason.replace("\n", " ").strip())
PY
    )

    local orchestrator_final="${orch_lines[0]:-}"
    local orchestrator_stage_hint="${orch_lines[1]:-}"
    local suggestions_json="${orch_lines[2]:-[]}"
    local orchestrator_reason="${orch_lines[3]:-}"

    if [ -n "$orchestrator_reason" ]; then
        log "🗳️ 编排结果: ${orchestrator_reason}"
    fi

    if [ -n "$orchestrator_stage_hint" ]; then
        LLM_STAGE_HINT="$orchestrator_stage_hint"
    else
        LLM_STAGE_HINT=""
    fi

    if [ "${ARBITER_ENABLED:-0}" != "1" ]; then
        echo "${orchestrator_final:-WAIT}"
        return
    fi

    local arbiter_script="${script_dir}/decision_arbiter.py"
    if [ ! -f "$arbiter_script" ]; then
        log "⚠️ 未找到决策仲裁器: $arbiter_script，直接采用编排输出"
        echo "${orchestrator_final:-WAIT}"
        return
    fi

    local arb_session_id="${MEMORY_SESSION_ID:-${TARGET_ID:-session}}"
    local arb_json
    arb_json=$(python3 "$arbiter_script" arbitrate "$arb_session_id" --suggestions "$suggestions_json" 2>>"$LOG_FILE" || echo "")
    if [ -z "$arb_json" ]; then
        log "⚠️ 仲裁输出为空，直接采用编排输出"
        echo "${orchestrator_final:-WAIT}"
        return
    fi

    local -a arb_lines
    mapfile -t arb_lines < <(
        python3 - <<'PY' <<<"$arb_json"
import json
import sys

d = json.loads(sys.stdin.read() or "{}")
dec = d.get("decision") or {}
print(dec.get("action_type", "wait"))
print((dec.get("action_content") or "").replace("\n", " ").strip())
print(str(dec.get("confidence", 0.0)))
print((dec.get("explanation") or "").replace("\n", " ").strip())
PY
    )

    local action_type="${arb_lines[0]:-wait}"
    local action_content="${arb_lines[1]:-}"
    local action_conf="${arb_lines[2]:-0}"
    local action_expl="${arb_lines[3]:-}"

    if [ -n "$action_expl" ]; then
        log "⚖️ 仲裁选择: type=${action_type}, conf=${action_conf} | ${action_expl}"
    else
        log "⚖️ 仲裁选择: type=${action_type}, conf=${action_conf}"
    fi

    case "$action_type" in
        wait)
            echo "WAIT"
            return
            ;;
        notify|escalate|abort)
            # 不把“需要人工介入/安全失败”类文本直接塞给被监控 AI，转为 WAIT 并走通知
            if [ "${AI_MONITOR_NOTIFICATION_ENABLED:-1}" = "1" ]; then
                python3 "${script_dir}/smart_notifier.py" send "$arb_session_id" "仲裁器输出 ${action_type}：${action_content:0:80}" --priority urgent --category intervention --immediate 2>/dev/null || \
                python3 "${script_dir}/notification_hub.py" send "human_needed" "需要人工介入" "仲裁器输出 ${action_type}: ${action_content:0:120}" --force 2>/dev/null || true
            fi
            echo "WAIT"
            return
            ;;
        *)
            if [ -z "$action_content" ]; then
                echo "WAIT"
                return
            fi
            echo "$action_content"
            return
            ;;
    esac
}

decide_response() {
    local output="$1"
    local last_response="${2:-}"
    local same_response_count="${3:-0}"
    local idle_seconds="${4:-0}"
    local now_s="${5:-0}"

    LLM_STAGE_HINT=""
    local recent_output
    recent_output="$(printf "%s" "$output" | tail -n 10)"
    if should_force_wait_for_safety "$recent_output" "$idle_seconds"; then
        echo "WAIT"
        return
    fi

    if [ "${ORCHESTRATOR_ENABLED:-0}" = "1" ]; then
        decide_response_orchestrated "$output" "$last_response" "$same_response_count" "$idle_seconds" "$now_s"
        return
    fi

    decide_response_llm "$output" "$last_response" "$same_response_count" "$idle_seconds" "$now_s"
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
    if should_force_wait_for_safety "$recent_output" "$idle_seconds"; then
        echo "WAIT"
        return
    fi

    local llm_script
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
    # 传递“重复压力”，用于动态调整 temperature（避免机械式重复）
    local repeat_pressure=0
    repeat_pressure="$same_response_count"
    if [ "${consecutive_wait_count:-0}" -gt "$repeat_pressure" ]; then
        repeat_pressure="${consecutive_wait_count}"
    fi
    if [ "$repeat_pressure" -gt 0 ]; then
        llm_args+=(--same-response-count "$repeat_pressure")
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

    local llm_input
    llm_input="$(build_decision_context "$output" "$last_response" "$same_response_count" "$idle_seconds" "$now_s" "$effective_role")"

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
consecutive_wait_count=0
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
log "📎 capture-lines: ${CAPTURE_LINES}"
log "⏳ busy-grace: ${BUSY_GRACE_S}秒"
log "🧠 模式: LLM 监工 (model=$LLM_MODEL, role_configured=$LLM_ROLE)"
if [ "${ORCHESTRATOR_ENABLED:-0}" = "1" ]; then
    log "🗳️ 多Agent编排: 已启用 (pipeline=${ORCHESTRATOR_PIPELINE:-vote})"
else
    log "🗳️ 多Agent编排: 未启用"
fi
if [ "${ARBITER_ENABLED:-0}" = "1" ]; then
    log "⚖️ 决策仲裁: 已启用"
else
    log "⚖️ 决策仲裁: 未启用"
fi
log "🌐 base-url: $LLM_BASE_URL"
log "🔁 同输出重请求: ${REQUERY_SAME_OUTPUT_AFTER}秒（0=不重复请求），重复加速: ${REQUERY_ON_REPEAT_AFTER}秒（0=禁用）"
if [ -n "$LLM_API_KEY" ]; then
    log "🔑 api-key: set"
else
    log "🔑 api-key: not set"
fi
log "📝 日志文件: $LOG_FILE"
log "🆔 进程PID: $$"
if [ "${AI_MONITOR_UNDERSTANDING_ENABLED:-1}" = "1" ]; then
    log "🧩 理解层: 已启用 (意图检测+错误分析+进度追踪)"
else
    log "🧩 理解层: 已禁用"
fi
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
    # 结束会话记录
    if [ "${AI_MONITOR_MEMORY_ENABLED:-1}" = "1" ] && [ -n "${MEMORY_SESSION_ID:-}" ]; then
        python3 "${script_dir}/memory_manager.py" end-session "$MEMORY_SESSION_ID" "completed" "手动停止" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    exit 0
}

trap cleanup SIGTERM SIGINT

# ============================================
# 初始化扩展模块
# ============================================

# 任务记忆系统（默认启用）
MEMORY_SESSION_ID=""
if [ "${AI_MONITOR_MEMORY_ENABLED:-1}" = "1" ]; then
    pane_cwd="$(tmux display-message -p -t "$TMUX_SESSION:$TMUX_WINDOW.$TMUX_PANE" '#{pane_current_path}' 2>/dev/null || echo "")"
    MEMORY_SESSION_ID=$(python3 "${script_dir}/memory_manager.py" start-session "$TARGET" "$pane_cwd" 2>/dev/null || echo "")
    if [ -n "$MEMORY_SESSION_ID" ]; then
        log "📝 任务记忆已启用，会话ID: $MEMORY_SESSION_ID"
    fi
fi

# 评估系统轮次计数（默认启用）
ASSESSMENT_ROUND_COUNT=0
ASSESSMENT_INTERVAL="${AI_MONITOR_ASSESSMENT_INTERVAL:-5}"

while true; do
    # 检查 tmux 会话是否存在
    if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        log "❌ tmux 会话 '$TMUX_SESSION' 不存在，退出监控"
        rm -f "$PID_FILE"
        exit 1
    fi

    # 捕获当前面板输出（最近 N 行）
    current_output=$(tmux capture-pane -t "$TMUX_SESSION:$TMUX_WINDOW.$TMUX_PANE" -p -S "-${CAPTURE_LINES}" 2>/dev/null)

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
        consecutive_wait_count=0
        last_llm_output_hash=""
        last_llm_output_hash_time=0
        last_llm_skip_log_hash=""

        # ========== 理解层更新（输出变化时执行）==========
        if [ "${AI_MONITOR_UNDERSTANDING_ENABLED:-1}" = "1" ] && [ -n "${MEMORY_SESSION_ID:-}" ]; then
            # 检测意图（从用户输入/输出中提取）
            python3 "${script_dir}/intent_parser.py" detect "$MEMORY_SESSION_ID" "$current_output" >/dev/null 2>&1 || true

            # 分析错误（如果有错误信息）
            python3 "${script_dir}/error_analyzer.py" analyze "$MEMORY_SESSION_ID" "$current_output" >/dev/null 2>&1 || true

            # 更新进度（基于输出信号）
            python3 "${script_dir}/progress_monitor.py" update "$MEMORY_SESSION_ID" "$current_output" --stage "${CURRENT_STAGE:-unknown}" >/dev/null 2>&1 || true

            # ========== Phase 2-3 模块集成 ==========
            # 分析输出模式（识别进度条/状态/交互提示）
            python3 "${script_dir}/output_recognizer.py" parse "$current_output" >/dev/null 2>&1 || true

            # 记录因果事件（用于后续根因分析）
            python3 "${script_dir}/causal_tracker.py" record "$MEMORY_SESSION_ID" "output" "{\"content\":\"${current_output:0:500}\"}" >/dev/null 2>&1 || true

            # 更新工作记忆（短期上下文）
            python3 "${script_dir}/working_memory.py" add "$MEMORY_SESSION_ID" "output" "${current_output:0:1000}" >/dev/null 2>&1 || true

            # 分析代码变更（如果有 git diff 变化）
            if [ -d "${pane_cwd:-.}/.git" ]; then
                python3 "${script_dir}/change_analyzer.py" analyze "$MEMORY_SESSION_ID" >/dev/null 2>&1 || true
            fi
        fi
    else
        idle_duration=$((current_time - last_change_time))

        if [ $idle_duration -ge $MIN_IDLE_TIME ]; then
            current_output_hash="$(hash_text "$current_output" 2>/dev/null || echo "")"
            if [ -n "$current_output_hash" ] && [ "$current_output_hash" = "$last_llm_output_hash" ]; then
                elapsed_since_llm=$((current_time - last_llm_output_hash_time))
                if [ "$same_response_count" -gt 0 ] && [ "$REQUERY_ON_REPEAT_AFTER" -gt 0 ] && [ "$elapsed_since_llm" -ge "$REQUERY_ON_REPEAT_AFTER" ]; then
                    :
                elif [ "$REQUERY_SAME_OUTPUT_AFTER" -gt 0 ] && [ "$elapsed_since_llm" -ge "$REQUERY_SAME_OUTPUT_AFTER" ]; then
                    :
                else
                    if [ "$last_llm_skip_log_hash" != "$current_output_hash" ]; then
                        log "⏭️ 输出未变化（elapsed=${elapsed_since_llm}s），已对该快照请求过 LLM，跳过重复请求"
                        last_llm_skip_log_hash="$current_output_hash"
                    fi
                    sleep $CHECK_INTERVAL
                    continue
                fi
            fi

            response=$(decide_response "$current_output" "$last_response" "$same_response_count" "$idle_duration" "$current_time")
            response="$(validate_response "$response")"
            if [ "$response" = "WAIT" ]; then
                consecutive_wait_count=$((consecutive_wait_count + 1))
            else
                consecutive_wait_count=0
            fi
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
                # 防止重复发送相同回复（避免在无变化的交互界面里"刷屏/连发"）
                if [ "$response" = "$last_response" ]; then
                    ((same_response_count++))
                    if [ $same_response_count -ge $MAX_RETRY_SAME ]; then
                        log "⚠️  LLM 连续给出相同回复 ${same_response_count} 次，已停止重复发送，建议人工介入或调整提示词/阈值"
                        # 通知人类（使用智能通知系统）
                        if [ "${AI_MONITOR_NOTIFICATION_ENABLED:-1}" = "1" ]; then
                            python3 "${script_dir}/smart_notifier.py" send "$MEMORY_SESSION_ID" "监工卡住：连续${same_response_count}次相同回复 - ${response:0:50}" --priority high --category warning --immediate 2>/dev/null || \
                            python3 "${script_dir}/notification_hub.py" send "stuck" "监工卡住" "连续${same_response_count}次相同回复: ${response:0:50}" --force 2>/dev/null || true
                        fi
                    else
                        log "⏭️ 与上次发送相同，已跳过重复发送: '$response'"
                    fi
                else
                    same_response_count=0
                    log "🔄 空闲 ${idle_duration}秒，LLM 回复: '$response'"
                    send_command "$response"
                    last_response="$response"
                    LAST_RESPONSE_SENT_AT="$current_time"

                    # 记录决策到任务记忆
                    if [ "${AI_MONITOR_MEMORY_ENABLED:-1}" = "1" ] && [ -n "${MEMORY_SESSION_ID:-}" ]; then
                        python3 "${script_dir}/memory_manager.py" record "$MEMORY_SESSION_ID" "${CURRENT_STAGE:-unknown}" "${effective_role:-monitor}" "$response" "success" 2>/dev/null || true

                        # ========== Phase 4 学习模块：决策后学习 ==========
                        # 收集隐式反馈（基于决策结果）
                        python3 "${script_dir}/feedback_collector.py" collect "$MEMORY_SESSION_ID" "command_sent" "{\"command\":\"${response}\",\"stage\":\"${CURRENT_STAGE:-unknown}\"}" 2>/dev/null || true

                        # 学习成功模式
                        python3 "${script_dir}/pattern_learner.py" learn "$MEMORY_SESSION_ID" "${current_output:0:500}" "$response" "success" 2>/dev/null || true

                        # 评估策略效果
                        python3 "${script_dir}/strategy_optimizer.py" record "${CURRENT_STAGE:-unknown}" "$response" "success" 2>/dev/null || true
                    fi
                fi
            else
                # 记录 WAIT 决策
                if [ "${AI_MONITOR_MEMORY_ENABLED:-1}" = "1" ] && [ -n "${MEMORY_SESSION_ID:-}" ]; then
                    python3 "${script_dir}/memory_manager.py" record "$MEMORY_SESSION_ID" "${CURRENT_STAGE:-unknown}" "${effective_role:-monitor}" "WAIT" "wait" 2>/dev/null || true
                fi
            fi

            # 自我评估检查
            ((ASSESSMENT_ROUND_COUNT++))
            if [ "${AI_MONITOR_ASSESSMENT_ENABLED:-1}" = "1" ] && [ $((ASSESSMENT_ROUND_COUNT % ASSESSMENT_INTERVAL)) -eq 0 ]; then
                assessment_result=$(python3 "${script_dir}/quality_assessor.py" add-round --session "${MEMORY_SESSION_ID:-assess}" --stage "${CURRENT_STAGE:-unknown}" --role "${effective_role:-monitor}" --output "$response" --outcome "$([ "$response" = "WAIT" ] && echo "wait" || echo "success")" 2>/dev/null || echo "")
                assessment=$(python3 "${script_dir}/quality_assessor.py" assess --session "${MEMORY_SESSION_ID:-assess}" 2>/dev/null || echo "{}")
                assessment_action=$(echo "$assessment" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('recommendation',{}).get('action','continue'))" 2>/dev/null || echo "continue")

                if [ "$assessment_action" = "alert_human" ]; then
                    log "⚠️ 评估系统建议人工介入"
                    if [ "${AI_MONITOR_NOTIFICATION_ENABLED:-1}" = "1" ]; then
                        python3 "${script_dir}/smart_notifier.py" send "$MEMORY_SESSION_ID" "评估系统检测到问题，需要人工介入" --priority urgent --category intervention --immediate 2>/dev/null || \
                        python3 "${script_dir}/notification_hub.py" send "human_needed" "需要人工介入" "评估系统检测到问题" --force 2>/dev/null || true
                    fi
                elif [ "$assessment_action" = "switch_role" ]; then
                    suggested_role=$(echo "$assessment" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('recommendation',{}).get('suggested_role','monitor'))" 2>/dev/null || echo "monitor")
                    if [ -n "$suggested_role" ] && [ "$suggested_role" != "${AUTO_ROLE_CURRENT:-}" ]; then
                        log "🔄 评估系统建议切换角色: $suggested_role"
                        AUTO_ROLE_CURRENT="$suggested_role"
                    fi
                fi
            fi
        else
            :
        fi
    fi

    sleep $CHECK_INTERVAL
done
