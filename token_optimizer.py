#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Token优化模块 - 减少多Agent系统的token消耗

包含:
1. 分层Agent策略 (TieredExecutor)
2. 终端输出智能过滤 (OutputFilter)
3. 响应缓存 (ResponseCache)
"""

import hashlib
import re
import time
from typing import Optional, List, Dict, Tuple

# ==================== 1. 分层Agent策略 ====================

class QuickClassifier:
    """快速分类器 - 用规则判断是否需要完整Agent分析"""

    # 明确需要WAIT的模式
    WAIT_PATTERNS = [
        r'Compiling\s+\d+',           # 编译中
        r'Building\s+\[',              # 构建中
        r'Installing\s+',              # 安装中
        r'Downloading\s+',             # 下载中
        r'^\s*\.\.\.\s*$',            # 省略号（进行中）
        r'Running\s+\d+\s+tests?',    # 测试运行中
        r'waiting\s+for',              # 等待中
        r'\d+%\s*\|',                  # 进度条
    ]

    # 明确需要干预的模式
    ACTION_PATTERNS = [
        (r'error\s*:', 'error'),
        (r'Error:', 'error'),
        (r'failed', 'failure'),
        (r'FAILED', 'failure'),
        (r'panic:', 'panic'),
        (r'exception', 'exception'),
        (r'\?\s*\[y/n\]', 'prompt'),
        (r'Press\s+.*\s+to\s+continue', 'prompt'),
        (r'Enter\s+.*:', 'prompt'),
    ]

    # 空闲/完成模式
    IDLE_PATTERNS = [
        r'^\s*\$\s*$',                # 空命令行
        r'Successfully',               # 成功完成
        r'Done\s*[.!]?$',             # 完成
        r'Finished',                   # 完成
        r'All\s+\d+\s+tests?\s+passed', # 测试通过
    ]

    def classify(self, output: str) -> Tuple[str, float]:
        """
        快速分类输出
        返回: (action, confidence)
        - action: 'wait' | 'action_needed' | 'idle' | 'uncertain'
        - confidence: 0.0-1.0
        """
        if not output or not output.strip():
            return ('idle', 0.9)

        last_lines = '\n'.join(output.strip().split('\n')[-20:])

        # 检查WAIT模式
        for pattern in self.WAIT_PATTERNS:
            if re.search(pattern, last_lines, re.IGNORECASE):
                return ('wait', 0.85)

        # 检查需要干预的模式
        for pattern, action_type in self.ACTION_PATTERNS:
            if re.search(pattern, last_lines, re.IGNORECASE):
                return ('action_needed', 0.75)

        # 检查空闲模式
        for pattern in self.IDLE_PATTERNS:
            if re.search(pattern, last_lines, re.IGNORECASE):
                return ('idle', 0.80)

        return ('uncertain', 0.3)


class TieredExecutor:
    """分层执行器 - 先快速判断，再决定是否调用完整Agent"""

    def __init__(self, confidence_threshold: float = 0.75):
        self.classifier = QuickClassifier()
        self.threshold = confidence_threshold

    def should_invoke_full_agent(self, context: str) -> Tuple[bool, Optional[str]]:
        """
        判断是否需要调用完整Agent
        返回: (need_full_agent, quick_response)
        """
        action, confidence = self.classifier.classify(context)

        if confidence >= self.threshold:
            if action == 'wait':
                return (False, 'WAIT')
            elif action == 'idle':
                # 空闲时可能需要推进，但置信度要更高
                if confidence >= 0.85:
                    return (False, 'WAIT')

        # 不确定或需要干预，调用完整Agent
        return (True, None)


# ==================== 2. 终端输出智能过滤 ====================

class OutputFilter:
    """智能过滤终端输出，减少token消耗"""

    # 低价值模式（可删除或折叠）
    LOW_VALUE_PATTERNS = [
        (r'^\s*$', 'empty'),                           # 空行
        (r'^(DEBUG|TRACE|VERBOSE):', 'debug'),         # 调试日志
        (r'^\s*\d+%.*\|.*\|', 'progress'),             # 进度条
        (r'^npm (WARN|notice)', 'npm_warn'),           # npm警告
        (r'^\s*at\s+.*\(.*:\d+:\d+\)', 'stacktrace'),  # 堆栈中间行
        (r'^info:', 'info'),                           # info日志
        (r'^\s*\^+\s*$', 'caret'),                     # 错误指示符
    ]

    # 高价值模式（必须保留）
    HIGH_VALUE_PATTERNS = [
        r'(error|Error|ERROR)',
        r'(fail|Fail|FAIL)',
        r'(success|Success|SUCCESS)',
        r'^(STAGE|CMD)=',
        r'(warning|Warning|WARNING)',
        r'(panic|Panic|PANIC)',
        r'\?\s*\[',                    # 交互提示
        r'(test|Test).*passed',
        r'(test|Test).*failed',
    ]

    def __init__(self, max_lines: int = 60, keep_recent: int = 15):
        self.max_lines = max_lines
        self.keep_recent = keep_recent

    def filter(self, output: str) -> str:
        """过滤输出，保留关键信息"""
        if not output:
            return ""

        lines = output.split('\n')
        if len(lines) <= self.max_lines:
            return output

        scored_lines = []
        for i, line in enumerate(lines):
            score = self._score_line(line, i, len(lines))
            scored_lines.append((score, i, line))

        # 按分数排序，保留高分行
        scored_lines.sort(key=lambda x: (-x[0], x[1]))

        # 选择要保留的行
        selected = set()

        # 1. 保留最近N行
        for i in range(max(0, len(lines) - self.keep_recent), len(lines)):
            selected.add(i)

        # 2. 保留高分行直到达到限制
        for score, idx, _ in scored_lines:
            if len(selected) >= self.max_lines:
                break
            if score > 0:
                selected.add(idx)

        # 按原始顺序输出
        result_lines = []
        prev_idx = -1
        for idx in sorted(selected):
            if prev_idx >= 0 and idx - prev_idx > 1:
                result_lines.append(f"  ... ({idx - prev_idx - 1} lines omitted)")
            result_lines.append(lines[idx])
            prev_idx = idx

        return '\n'.join(result_lines)

    def _score_line(self, line: str, index: int, total: int) -> int:
        """评分单行，高分=重要"""
        score = 0

        # 高价值模式 +10
        for pattern in self.HIGH_VALUE_PATTERNS:
            if re.search(pattern, line):
                score += 10
                break

        # 低价值模式 -5
        for pattern, _ in self.LOW_VALUE_PATTERNS:
            if re.match(pattern, line):
                score -= 5
                break

        # 位置加分（越靠后越重要）
        position_score = (index / total) * 3
        score += int(position_score)

        return score

    def fold_repetitive(self, output: str) -> str:
        """折叠重复的日志行"""
        lines = output.split('\n')
        if len(lines) < 5:
            return output

        result = []
        prev_pattern = None
        repeat_count = 0

        for line in lines:
            # 提取模式（去除数字和时间戳）
            pattern = re.sub(r'\d+', 'N', line[:60])
            pattern = re.sub(r'\d{2}:\d{2}:\d{2}', 'TIME', pattern)

            if pattern == prev_pattern and len(pattern) > 10:
                repeat_count += 1
            else:
                if repeat_count > 2:
                    result.append(f"  ... ({repeat_count} similar lines)")
                if repeat_count <= 2:
                    # 补回被跳过的行
                    pass
                result.append(line)
                prev_pattern = pattern
                repeat_count = 0

        if repeat_count > 2:
            result.append(f"  ... ({repeat_count} similar lines)")

        return '\n'.join(result)


# ==================== 3. 响应缓存 ====================

class ResponseCache:
    """缓存相似场景的响应，避免重复调用LLM"""

    def __init__(self, ttl_seconds: int = 180, max_size: int = 100):
        self.cache: Dict[str, Tuple[float, str, str]] = {}  # key -> (timestamp, response, stage)
        self.ttl = ttl_seconds
        self.max_size = max_size

    def _normalize(self, content: str) -> str:
        """标准化内容，去除易变部分"""
        # 去除时间戳
        normalized = re.sub(r'\d{2}:\d{2}:\d{2}', 'TIME', content)
        # 去除毫秒
        normalized = re.sub(r'\d+ms', 'Nms', normalized)
        # 去除行号
        normalized = re.sub(r':\d+:\d+', ':N:N', normalized)
        # 去除进度数字
        normalized = re.sub(r'\d+%', 'N%', normalized)
        # 只保留最后500字符
        return normalized[-500:]

    def _make_key(self, content: str, role: str) -> str:
        """生成缓存key"""
        normalized = self._normalize(content)
        return hashlib.md5(f"{role}:{normalized}".encode()).hexdigest()

    def get(self, content: str, role: str) -> Optional[Tuple[str, str]]:
        """获取缓存的响应"""
        key = self._make_key(content, role)
        if key in self.cache:
            timestamp, response, stage = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return (response, stage)
            else:
                del self.cache[key]
        return None

    def set(self, content: str, role: str, response: str, stage: str = None):
        """缓存响应"""
        # 清理过期条目
        self._cleanup()

        key = self._make_key(content, role)
        self.cache[key] = (time.time(), response, stage)

    def _cleanup(self):
        """清理过期和超量条目"""
        now = time.time()
        # 删除过期
        expired = [k for k, (t, _, _) in self.cache.items() if now - t >= self.ttl]
        for k in expired:
            del self.cache[k]

        # 如果仍超量，删除最旧的
        if len(self.cache) >= self.max_size:
            sorted_items = sorted(self.cache.items(), key=lambda x: x[1][0])
            for k, _ in sorted_items[:len(self.cache) - self.max_size + 10]:
                del self.cache[k]

    def clear(self):
        """清空缓存"""
        self.cache.clear()


# ==================== 全局实例 ====================

_tiered_executor = None
_output_filter = None
_response_cache = None


def get_tiered_executor() -> TieredExecutor:
    global _tiered_executor
    if _tiered_executor is None:
        _tiered_executor = TieredExecutor()
    return _tiered_executor


def get_output_filter() -> OutputFilter:
    global _output_filter
    if _output_filter is None:
        _output_filter = OutputFilter()
    return _output_filter


def get_response_cache() -> ResponseCache:
    global _response_cache
    if _response_cache is None:
        _response_cache = ResponseCache()
    return _response_cache
