#!/bin/bash

# ============================================
# 项目状态采集模块 v4
# 优先读取 CLAUDE.md，降级到自动检测
# ============================================

# 从 CLAUDE.md 或 AGENTS.md 提取关键上下文
collect_from_project_docs() {
    local doc_file=""

    # 优先 CLAUDE.md，其次 AGENTS.md
    [ -f "CLAUDE.md" ] && doc_file="CLAUDE.md"
    [ -z "$doc_file" ] && [ -f "AGENTS.md" ] && doc_file="AGENTS.md"
    [ -z "$doc_file" ] && return 1

    echo "[${doc_file}] 已初始化"

    # 提取项目名/描述（第一个 # 标题）
    local title
    title="$(grep -m1 '^# ' "$doc_file" 2>/dev/null | sed 's/^# //')"
    [ -n "$title" ] && echo "[project] $title"

    # 提取技术栈
    local tech
    tech="$(grep -A3 -i '技术栈\|Tech Stack\|技术架构\|Stack' "$doc_file" 2>/dev/null | grep -v '^#' | grep -v '^-*$' | head -1 | sed 's/^[- ]*//')"
    [ -n "$tech" ] && echo "[tech] $tech"

    # 提取模块数量
    local modules
    modules="$(grep -c '^## \|^### ' "$doc_file" 2>/dev/null || echo 0)"
    [ "$modules" -gt 0 ] && echo "[modules] $modules 个主要模块"

    # 提取最近更新时间
    local updated
    updated="$(grep -i '更新\|updated\|timestamp' "$doc_file" 2>/dev/null | head -1 | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | head -1)"
    [ -n "$updated" ] && echo "[updated] $updated"

    return 0
}

# 检测项目类型和技术栈（降级方案）
collect_tech_stack() {
    local stack=()
    local build_system=""
    local test_cmd=""

    # === 构建系统检测 ===
    [ -f "CMakeLists.txt" ] && { stack+=("C/C++"); build_system="CMake"; }
    [ -f "Makefile" ] && build_system="${build_system:-Make}"
    [ -f "meson.build" ] && build_system="Meson"

    # === 语言/框架检测 ===
    [ -f "Cargo.toml" ] && stack+=("Rust")
    [ -f "go.mod" ] && stack+=("Go")
    [ -f "package.json" ] && stack+=("Node.js")
    [ -f "pyproject.toml" ] || [ -f "setup.py" ] || [ -f "requirements.txt" ] && stack+=("Python")
    [ -f "Gemfile" ] && stack+=("Ruby")
    [ -f "pom.xml" ] || [ -f "build.gradle" ] && stack+=("Java")

    # === 框架检测 ===
    if [ -f "package.json" ]; then
        grep -q '"react"' package.json 2>/dev/null && stack+=("React")
        grep -q '"vue"' package.json 2>/dev/null && stack+=("Vue")
        grep -q '"electron"' package.json 2>/dev/null && stack+=("Electron")
        grep -q '"tauri"' package.json 2>/dev/null && stack+=("Tauri")
    fi

    # === 测试命令推断 ===
    if [ -f "CMakeLists.txt" ]; then
        [ -d "build-tests" ] && test_cmd="ctest --test-dir build-tests"
        [ -d "build" ] && test_cmd="${test_cmd:-ctest --test-dir build}"
    fi
    [ -f "Cargo.toml" ] && test_cmd="${test_cmd:-cargo test}"
    [ -f "go.mod" ] && test_cmd="${test_cmd:-go test ./...}"
    [ -f "package.json" ] && grep -q '"test"' package.json 2>/dev/null && test_cmd="${test_cmd:-npm test}"
    ([ -f "pyproject.toml" ] || [ -d "tests" ]) && test_cmd="${test_cmd:-pytest}"

    # === 输出 ===
    if [ ${#stack[@]} -gt 0 ]; then
        local IFS='/'
        echo "[tech] ${stack[*]}${build_system:+ ($build_system)}"
    fi
    [ -n "$test_cmd" ] && echo "[test] $test_cmd"
}

# 检测特定领域
collect_domain() {
    local domain=""

    # 游戏开发检测
    if [ -d "assets" ] || [ -d "resources" ] || [ -d "res" ]; then
        if find . -maxdepth 3 \( -name '*.cpp' -o -name '*.hpp' \) 2>/dev/null | head -20 | xargs grep -lq 'SDL\|SFML\|raylib\|OpenGL' 2>/dev/null; then
            domain="游戏"
        fi
    fi

    # Web 应用检测
    [ -d "src/pages" ] || [ -d "src/routes" ] || [ -d "app/routes" ] && domain="${domain:-Web应用}"
    [ -d "public" ] && [ -f "public/index.html" ] && domain="${domain:-Web前端}"

    # API/服务检测
    for f in main.py app.py server.py index.js index.ts main.go main.rs; do
        if [ -f "$f" ] || [ -f "src/$f" ]; then
            if grep -q 'FastAPI\|Flask\|Express\|Gin\|actix\|axum' "$f" "src/$f" 2>/dev/null; then
                domain="${domain:-API服务}"
                break
            fi
        fi
    done

    [ -n "$domain" ] && echo "[domain] $domain"

    # 资源统计
    for dir in assets resources res public static; do
        if [ -d "$dir" ]; then
            local count
            count="$(find "$dir" -type f 2>/dev/null | wc -l | tr -d ' ')"
            [ "$count" -gt 0 ] && echo "[assets] $count 个文件 ($dir/)"
            break
        fi
    done
}

# Git 状态
collect_git_status() {
    git rev-parse --is-inside-work-tree &>/dev/null || return

    local branch staged unstaged untracked ahead
    branch="$(git branch --show-current 2>/dev/null || echo 'detached')"
    staged="$(git diff --cached --numstat 2>/dev/null | wc -l | tr -d ' ')"
    unstaged="$(git diff --numstat 2>/dev/null | wc -l | tr -d ' ')"
    untracked="$(git ls-files --others --exclude-standard 2>/dev/null | wc -l | tr -d ' ')"
    ahead="$(git rev-list --count @{u}..HEAD 2>/dev/null || echo 0)"

    local parts=()
    [ "$staged" -gt 0 ] && parts+=("staged=$staged")
    [ "$unstaged" -gt 0 ] && parts+=("modified=$unstaged")
    [ "$untracked" -gt 0 ] && parts+=("new=$untracked")
    [ "$ahead" -gt 0 ] && parts+=("unpushed=$ahead")

    if [ ${#parts[@]} -gt 0 ]; then
        local IFS=', '
        echo "[git] ${parts[*]} (branch=$branch)"
    else
        echo "[git] 干净 (branch=$branch)"
    fi

    local last
    last="$(git log -1 --format='%s' 2>/dev/null | head -c 50)"
    [ -n "$last" ] && echo "[last-commit] $last"
}

# TODO 统计
collect_todos() {
    command -v rg &>/dev/null || return

    local count
    count="$(rg -c 'TODO|FIXME|HACK|XXX' --type-not md --type-not txt 2>/dev/null | awk -F: '{sum+=$2} END {print sum+0}')"

    if [ "$count" -gt 0 ]; then
        echo "[todos] $count 个待处理"
        rg -n 'TODO|FIXME' --type-not md --type-not txt 2>/dev/null | head -2 | while read -r line; do
            echo "  ${line:0:80}..."
        done
    fi
}

# 构建状态
collect_build_status() {
    for dir in build build-tests target dist out .next; do
        if [ -d "$dir" ]; then
            local age_min
            age_min="$(find "$dir" -maxdepth 1 -type f -mmin -60 2>/dev/null | wc -l | tr -d ' ')"
            if [ "$age_min" -gt 0 ]; then
                echo "[build] $dir/ (最近更新)"
            else
                echo "[build] $dir/ 存在"
            fi
            return
        fi
    done
}

# 最近活动
collect_recent() {
    local files
    files="$(find . -maxdepth 4 -type f \( -name '*.cpp' -o -name '*.hpp' -o -name '*.py' -o -name '*.ts' -o -name '*.tsx' -o -name '*.js' -o -name '*.go' -o -name '*.rs' \) -mmin -30 2>/dev/null | head -3)"

    if [ -n "$files" ]; then
        echo "[recent] 30分钟内:"
        echo "$files" | while read -r f; do
            echo "  ${f#./}"
        done
    fi
}

# ============================================
# 主入口
# ============================================

main() {
    local work_dir="${1:-.}"
    cd "$work_dir" 2>/dev/null || exit 1

    echo "=== 项目上下文 ==="

    # 优先从 CLAUDE.md / AGENTS.md 读取
    if collect_from_project_docs; then
        # 有项目文档，补充动态信息
        collect_build_status
        collect_git_status
        collect_recent
    else
        # 没有项目文档，降级到自动检测
        echo "[hint] 无 CLAUDE.md/AGENTS.md，建议运行 /init-project"
        collect_tech_stack
        collect_domain
        collect_build_status
        collect_git_status
        collect_todos
        collect_recent
    fi

    echo "=================="
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
