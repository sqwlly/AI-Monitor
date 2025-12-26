#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Error Analyzer
错误语义分析器 - 理解错误的本质和根因

功能：
1. 错误分类（语法/运行时/依赖/配置/网络/逻辑）
2. 根因推断（堆栈解析、上下文关联）
3. 修复建议生成
4. 历史相似错误匹配

Usage:
    python3 error_analyzer.py analyze <session_id> <text>
    python3 error_analyzer.py history <session_id>
    python3 error_analyzer.py suggest <error_id>
    python3 error_analyzer.py resolve <error_id> <fix_result>
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

# 数据库路径
DEFAULT_DB_DIR = Path.home() / ".tmux-monitor" / "memory"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "monitor.db"
DB_PATH = Path(os.environ.get("AI_MONITOR_MEMORY_DB", str(DEFAULT_DB_PATH)))

# 错误类型定义
ERROR_TYPES = {
    "syntax": {
        "name": "语法错误",
        "patterns": [
            r"SyntaxError",
            r"ParseError",
            r"IndentationError",
            r"unexpected token",
            r"invalid syntax",
            r"parsing error",
        ],
        "suggestions": [
            "检查语法格式",
            "检查括号/引号是否匹配",
            "检查缩进是否正确",
        ],
    },
    "runtime": {
        "name": "运行时错误",
        "patterns": [
            r"RuntimeError",
            r"TypeError",
            r"ValueError",
            r"AttributeError",
            r"KeyError",
            r"IndexError",
            r"ZeroDivisionError",
            r"NullPointerException",
            r"undefined is not",
            r"Cannot read property",
        ],
        "suggestions": [
            "检查变量类型是否正确",
            "检查空值处理",
            "添加边界条件检查",
        ],
    },
    "dependency": {
        "name": "依赖错误",
        "patterns": [
            r"ModuleNotFoundError",
            r"ImportError",
            r"No module named",
            r"Cannot find module",
            r"Package .+ not found",
            r"require\(\) of ES Module",
            r"Module not found",
        ],
        "suggestions": [
            "运行 pip install / npm install",
            "检查 requirements.txt / package.json",
            "检查模块路径是否正确",
        ],
    },
    "config": {
        "name": "配置错误",
        "patterns": [
            r"FileNotFoundError",
            r"ENOENT",
            r"PermissionError",
            r"EACCES",
            r"ConfigurationError",
            r"Environment variable .+ not set",
            r"Missing required",
            r"Invalid configuration",
        ],
        "suggestions": [
            "检查文件路径是否正确",
            "检查文件权限",
            "检查环境变量配置",
            "检查 .env 文件",
        ],
    },
    "network": {
        "name": "网络错误",
        "patterns": [
            r"ConnectionError",
            r"TimeoutError",
            r"ETIMEDOUT",
            r"ECONNREFUSED",
            r"Connection refused",
            r"Network is unreachable",
            r"getaddrinfo",
            r"socket hang up",
            r"CORS",
        ],
        "suggestions": [
            "检查网络连接",
            "检查服务是否启动",
            "检查端口是否被占用",
            "检查防火墙设置",
        ],
    },
    "database": {
        "name": "数据库错误",
        "patterns": [
            r"OperationalError",
            r"IntegrityError",
            r"DatabaseError",
            r"Connection to .+ failed",
            r"duplicate key",
            r"foreign key constraint",
            r"table .+ doesn't exist",
        ],
        "suggestions": [
            "检查数据库连接配置",
            "检查表结构是否正确",
            "检查数据约束",
            "运行数据库迁移",
        ],
    },
    "assertion": {
        "name": "断言/测试错误",
        "patterns": [
            r"AssertionError",
            r"FAILED",
            r"Expected .+ but got",
            r"does not match",
            r"should be",
            r"to equal",
            r"toBe",
            r"assert",
        ],
        "suggestions": [
            "检查预期值与实际值",
            "检查测试数据",
            "检查业务逻辑实现",
        ],
    },
    "memory": {
        "name": "内存错误",
        "patterns": [
            r"MemoryError",
            r"OutOfMemoryError",
            r"heap out of memory",
            r"JavaScript heap",
            r"allocation failed",
        ],
        "suggestions": [
            "减少数据处理量",
            "增加内存限制",
            "优化内存使用",
            "使用流式处理",
        ],
    },
    "auth": {
        "name": "认证/授权错误",
        "patterns": [
            r"401",
            r"403",
            r"Unauthorized",
            r"Forbidden",
            r"Invalid token",
            r"Authentication failed",
            r"Permission denied",
        ],
        "suggestions": [
            "检查认证凭据",
            "检查 token 是否过期",
            "检查权限配置",
        ],
    },
}

# 严重程度判断
SEVERITY_PATTERNS = {
    "critical": [
        r"FATAL",
        r"CRITICAL",
        r"panic",
        r"segmentation fault",
        r"core dumped",
        r"system error",
    ],
    "high": [
        r"ERROR",
        r"Exception",
        r"FAILED",
        r"crash",
    ],
    "medium": [
        r"WARNING",
        r"WARN",
        r"deprecated",
    ],
    "low": [
        r"INFO",
        r"NOTE",
        r"hint",
    ],
}


class ErrorAnalysis:
    """错误分析结果"""

    def __init__(self, error_id=None, session_id=None, raw_error="",
                 error_type="unknown", error_signature="", severity="medium",
                 root_cause="", related_files=None, suggested_fixes=None,
                 applied_fix=None, fix_result=None, created_at=None,
                 resolved_at=None, **kwargs):
        self.error_id = error_id or str(uuid.uuid4())[:8]
        self.session_id = session_id
        self.raw_error = raw_error
        self.error_type = error_type
        self.error_signature = error_signature
        self.severity = severity
        self.root_cause = root_cause
        self.related_files = related_files or []
        self.suggested_fixes = suggested_fixes or []
        self.applied_fix = applied_fix
        self.fix_result = fix_result
        self.created_at = created_at or int(time.time())
        self.resolved_at = resolved_at

    def to_dict(self):
        return {
            "error_id": self.error_id,
            "session_id": self.session_id,
            "raw_error": self.raw_error[:500],
            "error_type": self.error_type,
            "error_signature": self.error_signature,
            "severity": self.severity,
            "root_cause": self.root_cause,
            "related_files": self.related_files,
            "suggested_fixes": self.suggested_fixes,
            "applied_fix": self.applied_fix,
            "fix_result": self.fix_result,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }

    def to_context_string(self):
        """生成用于 LLM 上下文的字符串"""
        type_name = ERROR_TYPES.get(self.error_type, {}).get("name", self.error_type)
        severity_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(self.severity, "⚪")

        result = f"[error] {severity_icon} {type_name}: {self.root_cause[:100]}"

        if self.related_files:
            result += f" | 相关文件: {', '.join(self.related_files[:3])}"

        if self.suggested_fixes:
            result += f" | 建议: {self.suggested_fixes[0]}"

        return result


class ErrorAnalyzer:
    """错误语义分析器"""

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_db(self):
        """确保数据库和表存在"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS error_analysis (
                    error_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    raw_error TEXT,
                    error_type TEXT,
                    error_signature TEXT,
                    severity TEXT,
                    root_cause TEXT,
                    related_files TEXT,
                    suggested_fixes TEXT,
                    applied_fix TEXT,
                    fix_result TEXT,
                    created_at INTEGER,
                    resolved_at INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_error_session
                ON error_analysis(session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_error_signature
                ON error_analysis(error_signature)
            """)

    def analyze(self, session_id, text):
        """分析文本中的错误"""
        if not text or not text.strip():
            return None

        # 检测是否包含错误
        if not self._contains_error(text):
            return None

        # 提取错误信息
        error_text = self._extract_error_text(text)

        # 分类错误
        error_type = self._classify_error(error_text)

        # 判断严重程度
        severity = self._assess_severity(error_text)

        # 生成错误签名（用于匹配相似错误）
        signature = self._generate_signature(error_text, error_type)

        # 提取相关文件
        related_files = self._extract_related_files(text)

        # 推断根因
        root_cause = self._infer_root_cause(error_text, error_type, related_files)

        # 生成修复建议
        suggestions = self._generate_suggestions(error_type, error_text, root_cause)

        # 查找历史相似错误
        similar_errors = self._find_similar_errors(signature)
        if similar_errors:
            # 从历史中提取成功的修复方案
            for err in similar_errors:
                if err.fix_result == "success" and err.applied_fix:
                    suggestions.insert(0, f"[历史成功方案] {err.applied_fix}")
                    break

        analysis = ErrorAnalysis(
            session_id=session_id,
            raw_error=error_text,
            error_type=error_type,
            error_signature=signature,
            severity=severity,
            root_cause=root_cause,
            related_files=related_files,
            suggested_fixes=suggestions[:5],  # 最多5条建议
        )

        # 保存分析结果
        self._save_analysis(analysis)

        return analysis

    def _contains_error(self, text):
        """检测文本是否包含错误"""
        error_indicators = [
            r'\bError\b', r'\bException\b', r'\bFAILED\b', r'\bFailed\b',
            r'\bTraceback\b', r'\bpanic\b', r'\bfatal\b', r'\bcrash\b',
            r'错误', r'失败', r'异常',
        ]
        text_lower = text.lower()
        for pattern in error_indicators:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _extract_error_text(self, text):
        """提取核心错误信息"""
        lines = text.strip().split('\n')

        # 查找 Traceback
        traceback_start = -1
        for i, line in enumerate(lines):
            if 'Traceback' in line or 'Error:' in line or 'Exception:' in line:
                traceback_start = i
                break

        if traceback_start >= 0:
            # 提取 traceback 及其后的内容
            error_lines = lines[traceback_start:]
            return '\n'.join(error_lines[:30])  # 最多30行

        # 查找包含 error/exception 的行及其上下文
        for i, line in enumerate(lines):
            if re.search(r'(error|exception|failed)', line, re.IGNORECASE):
                start = max(0, i - 3)
                end = min(len(lines), i + 5)
                return '\n'.join(lines[start:end])

        # 返回最后20行
        return '\n'.join(lines[-20:])

    def _classify_error(self, error_text):
        """分类错误类型"""
        for error_type, config in ERROR_TYPES.items():
            for pattern in config["patterns"]:
                if re.search(pattern, error_text, re.IGNORECASE):
                    return error_type
        return "unknown"

    def _assess_severity(self, error_text):
        """评估错误严重程度"""
        for severity, patterns in SEVERITY_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, error_text, re.IGNORECASE):
                    return severity
        return "medium"

    def _generate_signature(self, error_text, error_type):
        """生成错误签名"""
        # 提取关键错误信息
        key_parts = []

        # 错误类型
        key_parts.append(error_type)

        # 提取错误类名（如 TypeError, ValueError）
        error_class_match = re.search(r'\b(\w+Error|\w+Exception)\b', error_text)
        if error_class_match:
            key_parts.append(error_class_match.group(1))

        # 提取错误消息的关键词
        error_msg_match = re.search(r'(?:Error|Exception)[:\s]+(.+?)(?:\n|$)', error_text)
        if error_msg_match:
            msg = error_msg_match.group(1)
            # 移除变量值，保留结构
            msg = re.sub(r'["\'][^"\']+["\']', '""', msg)
            msg = re.sub(r'\d+', 'N', msg)
            key_parts.append(msg[:100])

        signature_str = '|'.join(key_parts)
        return hashlib.sha256(signature_str.encode()).hexdigest()[:16]

    def _extract_related_files(self, text):
        """提取相关文件"""
        files = set()

        # 匹配文件路径
        file_patterns = [
            r'File ["\']([^"\']+)["\']',
            r'at ([^\s:]+):(\d+)',
            r'in ([^\s:]+):(\d+)',
            r'([/\\][\w/\\.-]+\.(py|js|ts|go|rs|java|rb|php|c|cpp|h))',
        ]

        for pattern in file_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if isinstance(match, tuple):
                    file_path = match[0]
                else:
                    file_path = match
                # 清理路径
                file_path = file_path.strip()
                if file_path and len(file_path) < 200:
                    # 只保留文件名和最近的目录
                    parts = file_path.replace('\\', '/').split('/')
                    if len(parts) > 2:
                        file_path = '/'.join(parts[-2:])
                    files.add(file_path)

        return list(files)[:5]  # 最多5个文件

    def _infer_root_cause(self, error_text, error_type, related_files):
        """推断根因"""
        # 提取错误消息
        error_msg = ""
        msg_match = re.search(r'(?:Error|Exception)[:\s]+(.+?)(?:\n|$)', error_text, re.IGNORECASE)
        if msg_match:
            error_msg = msg_match.group(1).strip()

        # 根据错误类型和消息推断根因
        if error_type == "dependency":
            module_match = re.search(r"No module named ['\"]?(\w+)", error_text)
            if module_match:
                return f"缺少模块 {module_match.group(1)}，需要安装"

        elif error_type == "config":
            file_match = re.search(r"(?:No such file|FileNotFoundError)[:\s]*['\"]?([^'\"]+)", error_text)
            if file_match:
                return f"文件不存在: {file_match.group(1)}"

        elif error_type == "syntax":
            line_match = re.search(r"line (\d+)", error_text)
            if line_match and related_files:
                return f"{related_files[0]} 第 {line_match.group(1)} 行语法错误"

        elif error_type == "assertion":
            expected_match = re.search(r"[Ee]xpected[:\s]+(.+?)\s+(?:but got|to equal|received)[:\s]+(.+?)(?:\n|$)", error_text)
            if expected_match:
                return f"预期 {expected_match.group(1).strip()[:30]}，实际 {expected_match.group(2).strip()[:30]}"

        elif error_type == "runtime":
            if "NoneType" in error_text or "undefined" in error_text or "null" in error_text:
                return "空值/未定义变量访问"
            if "KeyError" in error_text or "key" in error_msg.lower():
                key_match = re.search(r"KeyError[:\s]*['\"]?(\w+)", error_text)
                if key_match:
                    return f"字典缺少键: {key_match.group(1)}"

        # 默认返回错误消息的简化版
        if error_msg:
            return error_msg[:100]

        return "未能确定具体原因"

    def _generate_suggestions(self, error_type, error_text, root_cause):
        """生成修复建议"""
        suggestions = []

        # 获取错误类型的通用建议
        if error_type in ERROR_TYPES:
            suggestions.extend(ERROR_TYPES[error_type]["suggestions"])

        # 根据具体情况添加针对性建议
        if error_type == "dependency":
            module_match = re.search(r"No module named ['\"]?(\w+)", error_text)
            if module_match:
                module = module_match.group(1)
                suggestions.insert(0, f"运行: pip install {module}")

        elif error_type == "config":
            if ".env" in error_text or "environment" in error_text.lower():
                suggestions.insert(0, "检查 .env 文件是否存在且配置正确")

        elif error_type == "database":
            if "migration" in error_text.lower() or "migrate" in error_text.lower():
                suggestions.insert(0, "运行数据库迁移命令")

        elif error_type == "network":
            if "ECONNREFUSED" in error_text:
                suggestions.insert(0, "检查目标服务是否已启动")

        return suggestions

    def _find_similar_errors(self, signature):
        """查找相似错误"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM error_analysis
                WHERE error_signature = ? AND fix_result IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 5
            """, (signature,)).fetchall()

            return [self._row_to_analysis(row) for row in rows]

    def _save_analysis(self, analysis):
        """保存分析结果"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO error_analysis
                (error_id, session_id, raw_error, error_type, error_signature,
                 severity, root_cause, related_files, suggested_fixes,
                 applied_fix, fix_result, created_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                analysis.error_id,
                analysis.session_id,
                analysis.raw_error,
                analysis.error_type,
                analysis.error_signature,
                analysis.severity,
                analysis.root_cause,
                json.dumps(analysis.related_files, ensure_ascii=False),
                json.dumps(analysis.suggested_fixes, ensure_ascii=False),
                analysis.applied_fix,
                analysis.fix_result,
                analysis.created_at,
                analysis.resolved_at,
            ))

    def get_recent_errors(self, session_id, limit=5):
        """获取最近的错误"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM error_analysis
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (session_id, limit)).fetchall()

            return [self._row_to_analysis(row) for row in rows]

    def get_unresolved_errors(self, session_id):
        """获取未解决的错误"""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM error_analysis
                WHERE session_id = ? AND resolved_at IS NULL
                ORDER BY created_at DESC
            """, (session_id,)).fetchall()

            return [self._row_to_analysis(row) for row in rows]

    def resolve_error(self, error_id, applied_fix, fix_result):
        """标记错误已解决"""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE error_analysis
                SET applied_fix = ?, fix_result = ?, resolved_at = ?
                WHERE error_id = ?
            """, (applied_fix, fix_result, int(time.time()), error_id))

    def get_error_summary(self, session_id):
        """获取错误摘要（用于 LLM 上下文）"""
        errors = self.get_unresolved_errors(session_id)
        if not errors:
            return ""

        # 只返回最新的未解决错误
        latest = errors[0]
        return latest.to_context_string()

    def _row_to_analysis(self, row):
        """将数据库行转换为 ErrorAnalysis 对象"""
        return ErrorAnalysis(
            error_id=row['error_id'],
            session_id=row['session_id'],
            raw_error=row['raw_error'],
            error_type=row['error_type'],
            error_signature=row['error_signature'],
            severity=row['severity'],
            root_cause=row['root_cause'],
            related_files=json.loads(row['related_files'] or '[]'),
            suggested_fixes=json.loads(row['suggested_fixes'] or '[]'),
            applied_fix=row['applied_fix'],
            fix_result=row['fix_result'],
            created_at=row['created_at'],
            resolved_at=row['resolved_at'],
        )


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Error Analyzer',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # analyze
    p_analyze = subparsers.add_parser('analyze', help='Analyze error from text')
    p_analyze.add_argument('session_id', help='Session ID')
    p_analyze.add_argument('text', nargs='?', help='Text to analyze (or read from stdin)')

    # history
    p_history = subparsers.add_parser('history', help='Get error history')
    p_history.add_argument('session_id', help='Session ID')
    p_history.add_argument('--limit', type=int, default=10)

    # suggest
    p_suggest = subparsers.add_parser('suggest', help='Get suggestions for an error')
    p_suggest.add_argument('error_id', help='Error ID')

    # resolve
    p_resolve = subparsers.add_parser('resolve', help='Mark error as resolved')
    p_resolve.add_argument('error_id', help='Error ID')
    p_resolve.add_argument('fix_result', choices=['success', 'failed', 'partial'])
    p_resolve.add_argument('--fix', help='Applied fix description')

    # summary
    p_summary = subparsers.add_parser('summary', help='Get error summary for LLM context')
    p_summary.add_argument('session_id', help='Session ID')

    args = parser.parse_args(argv)
    ea = ErrorAnalyzer()

    try:
        if args.command == 'analyze':
            text = args.text
            if not text:
                text = sys.stdin.read()

            analysis = ea.analyze(args.session_id, text)
            if analysis:
                print(json.dumps(analysis.to_dict(), indent=2, ensure_ascii=False))
            else:
                print("{}")

        elif args.command == 'history':
            errors = ea.get_recent_errors(args.session_id, args.limit)
            for err in errors:
                status = "✅" if err.resolved_at else "❌"
                severity_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(err.severity, "⚪")
                type_name = ERROR_TYPES.get(err.error_type, {}).get("name", err.error_type)
                print(f"{status} {severity_icon} [{type_name}] {err.root_cause[:60]}")

        elif args.command == 'suggest':
            with ea._get_conn() as conn:
                row = conn.execute("SELECT * FROM error_analysis WHERE error_id = ?", (args.error_id,)).fetchone()
                if row:
                    analysis = ea._row_to_analysis(row)
                    print("修复建议:")
                    for i, sug in enumerate(analysis.suggested_fixes, 1):
                        print(f"  {i}. {sug}")
                else:
                    print("Error not found")

        elif args.command == 'resolve':
            ea.resolve_error(args.error_id, args.fix or "", args.fix_result)
            print(f"Error {args.error_id} marked as {args.fix_result}")

        elif args.command == 'summary':
            summary = ea.get_error_summary(args.session_id)
            if summary:
                print(summary)

        else:
            parser.print_help()
            return 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
