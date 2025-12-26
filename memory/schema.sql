-- Claude Monitor Memory System Schema
-- Version: 1.0.0
-- 任务记忆数据库结构

-- 会话记录表
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    target TEXT NOT NULL,                    -- tmux target (e.g., "5:node.0")
    project_path TEXT,                       -- 项目路径
    start_time INTEGER NOT NULL,             -- Unix timestamp
    end_time INTEGER,                        -- Unix timestamp (NULL if active)
    status TEXT DEFAULT 'active',            -- active/paused/completed/failed
    summary TEXT,                            -- 会话总结
    total_commands INTEGER DEFAULT 0,        -- 发送的命令总数
    total_waits INTEGER DEFAULT 0,           -- WAIT 次数
    last_stage TEXT,                         -- 最后阶段
    last_role TEXT,                          -- 最后角色
    created_at INTEGER DEFAULT (strftime('%s', 'now'))
);

-- 决策记录表
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp INTEGER NOT NULL,              -- Unix timestamp
    stage TEXT,                              -- 当前阶段
    role TEXT,                               -- 使用的角色
    input_hash TEXT,                         -- 输入内容的哈希（用于去重分析）
    input_preview TEXT,                      -- 输入预览（前200字符）
    output TEXT NOT NULL,                    -- LLM 输出
    outcome TEXT,                            -- success/wait/error/ignored/blocked
    latency_ms INTEGER,                      -- 响应延迟（毫秒）
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

-- 阶段时间线表
CREATE TABLE IF NOT EXISTS stage_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    entered_at INTEGER NOT NULL,             -- 进入阶段时间
    exited_at INTEGER,                       -- 退出阶段时间 (NULL if current)
    duration_s INTEGER,                      -- 持续时间（秒）
    commands_sent INTEGER DEFAULT 0,         -- 该阶段发送的命令数
    waits INTEGER DEFAULT 0,                 -- 该阶段 WAIT 次数
    role_used TEXT,                          -- 该阶段使用的角色
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

-- 错误模式学习表
CREATE TABLE IF NOT EXISTS error_patterns (
    pattern_hash TEXT PRIMARY KEY,
    error_signature TEXT NOT NULL,           -- 错误特征（如错误类型+关键词）
    error_preview TEXT,                      -- 错误预览
    occurrences INTEGER DEFAULT 1,           -- 出现次数
    first_seen INTEGER,                      -- 首次出现时间
    last_seen INTEGER,                       -- 最后出现时间
    successful_fixes TEXT,                   -- JSON: [{"command": "...", "success_count": N}]
    failed_fixes TEXT,                       -- JSON: 失败的修复尝试
    project_path TEXT                        -- 关联项目
);

-- 项目进度表（跨会话）
CREATE TABLE IF NOT EXISTS project_progress (
    project_path TEXT PRIMARY KEY,
    last_session_id TEXT,
    total_sessions INTEGER DEFAULT 0,
    total_commands INTEGER DEFAULT 0,
    total_time_s INTEGER DEFAULT 0,          -- 总监控时间
    stage_distribution TEXT,                 -- JSON: {"coding": 3600, "testing": 1200, ...}
    milestones TEXT,                         -- JSON: [{"name": "...", "achieved_at": ..., "session_id": ...}]
    learned_patterns TEXT,                   -- JSON: 学习到的模式
    last_updated INTEGER,
    FOREIGN KEY (last_session_id) REFERENCES sessions(session_id)
);

-- 索引优化
CREATE INDEX IF NOT EXISTS idx_decisions_session ON decisions(session_id);
CREATE INDEX IF NOT EXISTS idx_decisions_timestamp ON decisions(timestamp);
CREATE INDEX IF NOT EXISTS idx_decisions_stage ON decisions(stage);
CREATE INDEX IF NOT EXISTS idx_stage_timeline_session ON stage_timeline(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_path);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_error_patterns_project ON error_patterns(project_path);

-- 视图：活跃会话
CREATE VIEW IF NOT EXISTS v_active_sessions AS
SELECT
    s.session_id,
    s.target,
    s.project_path,
    s.start_time,
    (strftime('%s', 'now') - s.start_time) as uptime_s,
    s.total_commands,
    s.total_waits,
    s.last_stage,
    s.last_role,
    (SELECT COUNT(*) FROM decisions d WHERE d.session_id = s.session_id) as decision_count
FROM sessions s
WHERE s.status = 'active';

-- 视图：会话统计
CREATE VIEW IF NOT EXISTS v_session_stats AS
SELECT
    s.session_id,
    s.target,
    s.project_path,
    s.start_time,
    s.end_time,
    COALESCE(s.end_time - s.start_time, strftime('%s', 'now') - s.start_time) as duration_s,
    s.status,
    s.total_commands,
    s.total_waits,
    CASE WHEN s.total_commands + s.total_waits > 0
         THEN ROUND(100.0 * s.total_commands / (s.total_commands + s.total_waits), 1)
         ELSE 0 END as action_rate_pct,
    (SELECT COUNT(DISTINCT stage) FROM stage_timeline st WHERE st.session_id = s.session_id) as stages_visited
FROM sessions s;
