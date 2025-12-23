#!/bin/bash

# ============================================
# tmux 监控脚本管理工具
# ============================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMART_SCRIPT="${SCRIPT_DIR}/smart-monitor.sh"
LOG_DIR="$HOME/.tmux-monitor"
CMD="${CLAUDE_MONITOR_CMD:-$(basename "$0")}"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

show_help() {
    cat << EOF
用法: ${CMD} {run|stop|restart|status|logs|tail|list|clean|install|test} [参数]

命令:
  run <target> [opts]   - 🧠 启动 LLM 监工监控（默认命令）
  stop [target]         - 停止监控（不指定则停止所有）
  restart <target> [opts] - 重启监控
  status                - 查看所有运行中的监控
  list                  - 列出所有 tmux 会话和面板
  logs [target]         - 查看日志
  tail [target]         - 实时查看日志
  clean                 - 清理旧日志
  install [name]        - 安装到 ~/.local/bin（默认命令名: cm）
  test                  - 测试 LLM 配置与连通性（不启动监控）

参数格式:
  target: 会话:窗口.面板 (例如: 2:mon.0)

快捷方式:
  - 直接传 target：${CMD} "2:mon.0"      # 等同于 run
  - 交互选择：${CMD}                    # 直接进入选择并启动 run
  - 别名：r=run, s=run, st=status, ls=list, t=tail, k=stop

LLM 监工参数（传给 run / 默认 target 调用）:
  --model <model>
  --base-url <url>         # OpenAI 兼容接口（如 Ollama: http://localhost:11434/v1）
  --api-key <key>
  --timeout <sec>
  --system-prompt-file <file>

示例:
  ${CMD} list                      # 查看所有可监控的面板
  ${CMD} run 2:mon.0               # 🧠 LLM 监工监控
  ${CMD} 2:mon.0 --base-url "http://localhost:11434/v1" --model "qwen2.5:7b-instruct"
  ${CMD} test                      # 测试 LLM 是否可用（返回一行 continue/WAIT 等）
  ${CMD} status                    # 查看运行状态
  ${CMD} tail 2:mon.0              # 实时查看该面板的日志
  ${CMD} stop 2:mon.0              # 停止该面板的监控
  ${CMD} stop                      # 停止所有监控
  ${CMD} install                   # 安装命令（默认 cm）
EOF
}

is_target() {
    local value="${1:-}"
    [[ "$value" =~ ^([^:]+):([^.]+)\.([0-9]+)$ ]]
}

prompt_target() {
    echo "📋 可用的 tmux 会话:"
    echo "----------------------------------------"
    tmux list-sessions 2>/dev/null || {
        echo -e "${RED}❌ 没有运行中的 tmux 会话${NC}"
        exit 1
    }
    echo ""
    echo -n "输入会话名称或编号: "
    read -r session

    echo ""
    echo "📋 该会话可用窗口:"
    tmux list-windows -t "$session" -F "#{window_index}:#{window_name}" 2>/dev/null || true
    echo -n "输入窗口名称或编号: "
    read -r window

    echo ""
    echo "📋 该窗口可用面板:"
    tmux list-panes -t "$session:$window" -F "#{pane_index}: #{pane_current_command}" 2>/dev/null || true
    echo -n "输入面板编号 [默认:0]: "
    read -r pane
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
    tmux list-sessions -F "#{session_name}" 2>/dev/null | while read session; do
        echo -e "${GREEN}会话: $session${NC}"
        tmux list-windows -t "$session" -F "#{window_index}:#{window_name}" 2>/dev/null | while read window; do
            window_index=$(echo $window | cut -d: -f1)
            window_name=$(echo $window | cut -d: -f2)
            echo -e "  ${BLUE}窗口: $window_name ($window_index)${NC}"
            
            tmux list-panes -t "$session:$window_index" -F "#{pane_index}: #{pane_current_command}" 2>/dev/null | while read pane; do
                pane_index=$(echo $pane | cut -d: -f1)
                pane_cmd=$(echo $pane | cut -d: -f2-)
                
                # 高亮显示可能是 Claude Code 的面板
                if echo "$pane_cmd" | grep -qi "claude"; then
                    echo -e "    ${YELLOW}→ 面板 $pane_index: $pane_cmd ⭐${NC}"
                    echo -e "      ${YELLOW}监控命令: ${CMD} \"$session:$window_name.$pane_index\"${NC}"
                else
                    echo "    → 面板 $pane_index: $pane_cmd"
                    echo "      监控命令: ${CMD} \"$session:$window_name.$pane_index\""
                fi
            done
        done
        echo ""
    done
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
        session="${BASH_REMATCH[1]}"
        window="${BASH_REMATCH[2]}"
        pane="${BASH_REMATCH[3]}"
        pid_file="$LOG_DIR/smart_${session}_${window}_${pane}.pid"

        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            if ps -p $pid > /dev/null 2>&1; then
                echo -e "${YELLOW}该面板已在 LLM 监工监控中 (PID: $pid)${NC}"
                return
            fi
        fi

        # 后台启动 LLM 监工监控
        shift
        nohup bash "$smart_script" "$target" "$@" > /dev/null 2>&1 &
        sleep 1

        echo -e "${GREEN}✓ 已启动 LLM 监工监控 🧠${NC}"
        echo "  目标: $target"
        echo "  模式: LLM 监工（OpenAI 兼容接口）"
        echo "  日志: $LOG_DIR/smart_${session}_${window}_${pane}.log"
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
                    pid=$(cat "$pid_file")
                    if ps -p $pid > /dev/null 2>&1; then
                        kill $pid
                        echo -e "${GREEN}✓ 已停止 $(basename ${pid_file%.pid})${NC}"
                        stopped=1
                    fi
                    rm -f "$pid_file"
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
                pid=$(cat "$pid_file")
                if ps -p $pid > /dev/null 2>&1; then
                    kill $pid
                    echo -e "${GREEN}✓ 已停止旧版本监控 $target${NC}"
                    stopped=1
                fi
                rm -f "$pid_file"
            fi

            # 当前：smart_*.pid（LLM 监工）
            smart_pid_file="$LOG_DIR/smart_${session}_${window}_${pane}.pid"
            if [ -f "$smart_pid_file" ]; then
                pid=$(cat "$smart_pid_file")
                if ps -p $pid > /dev/null 2>&1; then
                    kill $pid
                    echo -e "${GREEN}✓ 已停止 LLM 监工监控 $target${NC}"
                    stopped=1
                fi
                rm -f "$smart_pid_file"
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

    if [ ! -d "$LOG_DIR" ] || [ -z "$(ls -A $LOG_DIR/*.pid 2>/dev/null)" ]; then
        echo -e "${YELLOW}没有运行中的监控${NC}"
        echo ""
        echo "使用 '${CMD} list' 查看可监控的面板"
        echo "使用 '${CMD} run <target>' 启动 LLM 监工监控"
        return
    fi

    for pid_file in "$LOG_DIR"/*.pid; do
        if [ -f "$pid_file" ]; then
            filename=$(basename "$pid_file" .pid)
            # 解析文件名: smart_session_window_pane 或旧版本 monitor_session_window_pane
            if [[ $filename =~ ^(smart|monitor)_(.+)_(.+)_([0-9]+)$ ]]; then
                mode="${BASH_REMATCH[1]}"
                session="${BASH_REMATCH[2]}"
                window="${BASH_REMATCH[3]}"
                pane="${BASH_REMATCH[4]}"
                target="$session:$window.$pane"

                pid=$(cat "$pid_file")
                if ps -p $pid > /dev/null 2>&1; then
                    log_file="${pid_file%.pid}.log"

                    if [ "$mode" = "smart" ]; then
                        echo -e "${GREEN}✓ 运行中${NC} 🧠 - $target ${BLUE}[LLM 监工]${NC}"
                    else
                        echo -e "${YELLOW}✓ 运行中${NC} - $target [旧版本监控：建议 stop]${NC}"
                    fi
                    echo "  PID: $pid"
                    echo "  日志: $log_file"
                    if [ -f "$log_file" ]; then
                        echo "  大小: $(du -h "$log_file" | cut -f1)"
                        # 显示最后一行日志
                        last_log=$(tail -1 "$log_file" 2>/dev/null)
                        if [ -n "$last_log" ]; then
                            echo "  最后: $last_log"
                        fi
                    fi
                    echo ""
                else
                    echo -e "${RED}✗ 已停止${NC} - $target (陈旧的 PID: $pid)"
                    rm -f "$pid_file"
                    echo ""
                fi
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
            smart_log="$LOG_DIR/smart_${session}_${window}_${pane}.log"
            legacy_log="$LOG_DIR/monitor_${session}_${window}_${pane}.log"

            if [ -f "$smart_log" ]; then
                log_file="$smart_log"
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
            smart_log="$LOG_DIR/smart_${session}_${window}_${pane}.log"
            normal_log="$LOG_DIR/monitor_${session}_${window}_${pane}.log"

            if [ -f "$smart_log" ]; then
                log_file="$smart_log"
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
        rm -rf "$LOG_DIR"/*.log
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
    *)
        show_help
        exit 1
        ;;
esac
