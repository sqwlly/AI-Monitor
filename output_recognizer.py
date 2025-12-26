#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude Monitor Output Recognizer
输出模式识别器 - 识别和解析终端输出的各类模式

功能：
1. 结构化输出解析（JSON/YAML/表格/列表）
2. 进度指示器识别（进度条/Spinner/计数器/时间估计）
3. 状态指示器识别（成功/失败/警告/信息）
4. 交互提示识别（确认/选择/输入/等待）

Usage:
    python3 output_recognizer.py parse <text>
    python3 output_recognizer.py status <text>
    python3 output_recognizer.py progress <text>
    python3 output_recognizer.py interactive <text>
"""

import argparse
import json
import re
import sys
from compat_dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class OutputType(Enum):
    """输出类型"""
    JSON = "json"
    YAML = "yaml"
    TABLE = "table"
    LIST = "list"
    PLAIN = "plain"


class StatusType(Enum):
    """状态类型"""
    SUCCESS = "success"
    FAILURE = "failure"
    WARNING = "warning"
    INFO = "info"
    UNKNOWN = "unknown"


class InteractiveType(Enum):
    """交互类型"""
    CONFIRM = "confirm"          # [y/n] 确认
    SELECT = "select"            # 1) 2) 选择
    INPUT = "input"              # Enter xxx: 输入
    PASSWORD = "password"        # Password: 密码输入
    WAIT = "wait"               # Press any key...
    NONE = "none"


@dataclass
class ProgressInfo:
    """进度信息"""
    percentage: Optional[float] = None      # 百分比 0-100
    current: Optional[int] = None           # 当前值
    total: Optional[int] = None             # 总值
    eta: Optional[str] = None               # 预计剩余时间
    speed: Optional[str] = None             # 速度
    is_spinning: bool = False               # 是否在旋转动画中
    bar_visual: Optional[str] = None        # 进度条可视化

    def to_dict(self) -> Dict:
        return {
            "percentage": self.percentage,
            "current": self.current,
            "total": self.total,
            "eta": self.eta,
            "speed": self.speed,
            "is_spinning": self.is_spinning,
            "bar_visual": self.bar_visual,
        }


@dataclass
class StatusInfo:
    """状态信息"""
    status_type: StatusType = StatusType.UNKNOWN
    count: int = 0                          # 该类型出现次数
    messages: List[str] = field(default_factory=list)  # 相关消息

    def to_dict(self) -> Dict:
        return {
            "status_type": self.status_type.value,
            "count": self.count,
            "messages": self.messages[:5],  # 最多5条
        }


@dataclass
class InteractiveInfo:
    """交互信息"""
    interactive_type: InteractiveType = InteractiveType.NONE
    prompt: str = ""                        # 提示文本
    options: List[str] = field(default_factory=list)  # 可选项
    default_value: Optional[str] = None     # 默认值

    def to_dict(self) -> Dict:
        return {
            "interactive_type": self.interactive_type.value,
            "prompt": self.prompt,
            "options": self.options,
            "default_value": self.default_value,
        }


@dataclass
class ParseResult:
    """解析结果"""
    output_type: OutputType = OutputType.PLAIN
    structured_data: Optional[Any] = None   # 结构化数据
    progress: Optional[ProgressInfo] = None
    status: Optional[StatusInfo] = None
    interactive: Optional[InteractiveInfo] = None
    raw_text: str = ""

    def to_dict(self) -> Dict:
        return {
            "output_type": self.output_type.value,
            "structured_data": self.structured_data,
            "progress": self.progress.to_dict() if self.progress else None,
            "status": self.status.to_dict() if self.status else None,
            "interactive": self.interactive.to_dict() if self.interactive else None,
        }

    def to_context_string(self) -> str:
        """生成用于 LLM 上下文的字符串"""
        parts = []

        if self.progress and (self.progress.percentage or self.progress.current):
            if self.progress.percentage:
                parts.append(f"进度: {self.progress.percentage:.0f}%")
            elif self.progress.current and self.progress.total:
                parts.append(f"进度: {self.progress.current}/{self.progress.total}")
            if self.progress.eta:
                parts.append(f"ETA: {self.progress.eta}")

        if self.status and self.status.status_type != StatusType.UNKNOWN:
            status_icons = {
                StatusType.SUCCESS: "✅",
                StatusType.FAILURE: "❌",
                StatusType.WARNING: "⚠️",
                StatusType.INFO: "ℹ️",
            }
            icon = status_icons.get(self.status.status_type, "")
            parts.append(f"{icon} {self.status.status_type.value}×{self.status.count}")

        if self.interactive and self.interactive.interactive_type != InteractiveType.NONE:
            interactive_icons = {
                InteractiveType.CONFIRM: "❓",
                InteractiveType.SELECT: "📋",
                InteractiveType.INPUT: "✏️",
                InteractiveType.PASSWORD: "🔐",
                InteractiveType.WAIT: "⏳",
            }
            icon = interactive_icons.get(self.interactive.interactive_type, "")
            parts.append(f"{icon} 等待{self.interactive.interactive_type.value}")

        if parts:
            return "[output] " + " | ".join(parts)
        return ""


class OutputRecognizer:
    """输出模式识别器"""

    # 进度条模式
    PROGRESS_PATTERNS = [
        # 百分比: 50%, 50.5%
        (r'(\d+(?:\.\d+)?)\s*%', 'percentage'),
        # 分数: 5/10, 50 of 100
        (r'(\d+)\s*[/of]\s*(\d+)', 'fraction'),
        # 进度条: [=====>    ], [####....], [▓▓▓▓░░░░]
        (r'\[([=\->#▓▒░\s]+)\]', 'bar'),
        # ETA: ETA: 2m, eta 10s, 剩余 5分钟
        (r'(?:ETA|eta|剩余|remaining)[:\s]*(\d+[smh分秒时]?\d*[smh分秒时]?)', 'eta'),
        # 速度: 10MB/s, 100 items/s
        (r'(\d+(?:\.\d+)?\s*(?:MB|KB|GB|items?|行|条)[/每]s?)', 'speed'),
    ]

    # Spinner 模式
    SPINNER_CHARS = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏',
                     '◐', '◓', '◑', '◒', '|', '/', '-', '\\',
                     '⣾', '⣽', '⣻', '⢿', '⡿', '⣟', '⣯', '⣷']

    # 状态指示器模式
    STATUS_PATTERNS = {
        StatusType.SUCCESS: [
            r'\b(?:PASS(?:ED)?|SUCCESS(?:FUL)?|OK|DONE|COMPLETED?|✓|✔|√)\b',
            r'(?:测试通过|成功|完成|已完成)',
            r'\[(?:PASS|OK|SUCCESS|DONE)\]',
        ],
        StatusType.FAILURE: [
            r'\b(?:FAIL(?:ED|URE)?|ERROR|CRASHED?|✗|✘|×)\b',
            r'(?:失败|错误|崩溃)',
            r'\[(?:FAIL|ERROR|FAILED)\]',
        ],
        StatusType.WARNING: [
            r'\b(?:WARN(?:ING)?|DEPRECATED|CAUTION|⚠)\b',
            r'(?:警告|注意|过时)',
            r'\[(?:WARN|WARNING)\]',
        ],
        StatusType.INFO: [
            r'\b(?:INFO|NOTE|NOTICE|HINT|TIP|ℹ)\b',
            r'(?:信息|提示|注意)',
            r'\[(?:INFO|NOTE)\]',
        ],
    }

    # 交互提示模式
    INTERACTIVE_PATTERNS = {
        InteractiveType.CONFIRM: [
            r'\[([yYnN])/([yYnN])\]',
            r'\(([yY]es)/([nN]o)\)',
            r'(?:确认|是否|继续)\s*[?？]',
            r'(?:proceed|continue|confirm)\s*[?？]',
        ],
        InteractiveType.SELECT: [
            r'^\s*(\d+)\)\s+',                # 1) option
            r'^\s*\[(\d+)\]\s+',              # [1] option
            r'(?:选择|请选择|choose|select)',
        ],
        InteractiveType.INPUT: [
            r'(?:Enter|Input|输入|请输入)\s+.+[:\：]',
            r'.+[:\：]\s*$',
        ],
        InteractiveType.PASSWORD: [
            r'(?:Password|密码|口令)[:\：]',
            r'(?:Enter|输入)\s*(?:password|密码)',
        ],
        InteractiveType.WAIT: [
            r'(?:Press any key|按任意键)',
            r'(?:等待|waiting)',
            r'(?:Hit Enter|按回车)',
        ],
    }

    def __init__(self):
        pass

    def parse(self, text: str) -> ParseResult:
        """完整解析输出"""
        if not text or not text.strip():
            return ParseResult(raw_text=text)

        result = ParseResult(raw_text=text)

        # 尝试解析结构化数据
        result.output_type, result.structured_data = self._parse_structured(text)

        # 解析进度信息
        result.progress = self._parse_progress(text)

        # 解析状态信息
        result.status = self._parse_status(text)

        # 解析交互信息
        result.interactive = self._parse_interactive(text)

        return result

    def _parse_structured(self, text: str) -> Tuple[OutputType, Optional[Any]]:
        """解析结构化输出"""
        text = text.strip()

        # 尝试解析 JSON
        if text.startswith('{') or text.startswith('['):
            try:
                data = json.loads(text)
                return OutputType.JSON, data
            except json.JSONDecodeError:
                # 尝试提取 JSON 部分
                json_match = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', text)
                if json_match:
                    try:
                        data = json.loads(json_match.group(1))
                        return OutputType.JSON, data
                    except json.JSONDecodeError:
                        pass

        # 尝试解析 YAML (简单检测)
        if re.search(r'^[\w-]+:\s+', text, re.MULTILINE):
            try:
                import yaml
                data = yaml.safe_load(text)
                if isinstance(data, dict):
                    return OutputType.YAML, data
            except (ImportError, Exception):
                pass

        # 检测表格输出
        if self._is_table(text):
            table_data = self._parse_table(text)
            if table_data:
                return OutputType.TABLE, table_data

        # 检测列表输出
        list_data = self._parse_list(text)
        if list_data:
            return OutputType.LIST, list_data

        return OutputType.PLAIN, None

    def _is_table(self, text: str) -> bool:
        """检测是否为表格输出"""
        lines = text.strip().split('\n')
        if len(lines) < 2:
            return False

        # 检测分隔符行: ---|---|---
        for line in lines[:5]:
            if re.match(r'^[\s|+-]+$', line) and ('|' in line or '-' in line):
                return True

        # 检测制表符或多空格对齐
        tab_count = sum(1 for line in lines if '\t' in line or '  ' in line)
        return tab_count >= len(lines) * 0.5

    def _parse_table(self, text: str) -> Optional[List[Dict]]:
        """解析表格输出"""
        lines = text.strip().split('\n')
        if len(lines) < 2:
            return None

        # 尝试解析 markdown 表格
        if '|' in text:
            return self._parse_markdown_table(lines)

        # 尝试解析空格/制表符分隔的表格
        return self._parse_space_table(lines)

    def _parse_markdown_table(self, lines: List[str]) -> Optional[List[Dict]]:
        """解析 Markdown 表格"""
        # 找到表头
        header_line = None
        data_start = 0

        for i, line in enumerate(lines):
            if '|' in line and not re.match(r'^[\s|:-]+$', line):
                if header_line is None:
                    header_line = line
                    data_start = i + 1
                    # 跳过分隔符行
                    if data_start < len(lines) and re.match(r'^[\s|:-]+$', lines[data_start]):
                        data_start += 1
                    break

        if not header_line:
            return None

        # 解析表头
        headers = [h.strip() for h in header_line.split('|') if h.strip()]

        # 解析数据行
        result = []
        for line in lines[data_start:]:
            if '|' in line and not re.match(r'^[\s|:-]+$', line):
                cells = [c.strip() for c in line.split('|') if c.strip()]
                if len(cells) == len(headers):
                    result.append(dict(zip(headers, cells)))

        return result if result else None

    def _parse_space_table(self, lines: List[str]) -> Optional[List[Dict]]:
        """解析空格分隔的表格"""
        # 简化实现：假设第一行是表头
        if not lines:
            return None

        # 使用多空格或制表符分隔
        header_parts = re.split(r'\s{2,}|\t', lines[0].strip())
        if len(header_parts) < 2:
            return None

        result = []
        for line in lines[1:]:
            parts = re.split(r'\s{2,}|\t', line.strip())
            if len(parts) == len(header_parts):
                result.append(dict(zip(header_parts, parts)))

        return result if result else None

    def _parse_list(self, text: str) -> Optional[List[str]]:
        """解析列表输出"""
        lines = text.strip().split('\n')
        list_items = []

        # 匹配列表项模式
        list_patterns = [
            r'^\s*[-*•]\s+(.+)$',           # - item, * item, • item
            r'^\s*\d+[.)]\s+(.+)$',          # 1. item, 1) item
            r'^\s*\[.\]\s+(.+)$',            # [x] item, [ ] item
        ]

        for line in lines:
            for pattern in list_patterns:
                match = re.match(pattern, line)
                if match:
                    list_items.append(match.group(1))
                    break

        return list_items if len(list_items) >= 2 else None

    def _parse_progress(self, text: str) -> Optional[ProgressInfo]:
        """解析进度信息"""
        progress = ProgressInfo()
        has_progress = False

        # 检测 Spinner
        for char in self.SPINNER_CHARS:
            if char in text:
                progress.is_spinning = True
                has_progress = True
                break

        for pattern, ptype in self.PROGRESS_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                has_progress = True

                if ptype == 'percentage':
                    # 取最后一个百分比
                    progress.percentage = float(matches[-1])

                elif ptype == 'fraction':
                    # 取最后一个分数
                    current, total = matches[-1]
                    progress.current = int(current)
                    progress.total = int(total)
                    if progress.total > 0:
                        progress.percentage = (progress.current / progress.total) * 100

                elif ptype == 'bar':
                    progress.bar_visual = matches[-1]
                    # 从进度条估算百分比
                    bar = matches[-1]
                    filled = len(re.findall(r'[=#▓]', bar))
                    total = len(bar.replace(' ', ''))
                    if total > 0:
                        progress.percentage = progress.percentage or (filled / total) * 100

                elif ptype == 'eta':
                    progress.eta = matches[-1]

                elif ptype == 'speed':
                    progress.speed = matches[-1]

        return progress if has_progress else None

    def _parse_status(self, text: str) -> Optional[StatusInfo]:
        """解析状态信息"""
        status_counts = {}
        status_messages = {}

        for status_type, patterns in self.STATUS_PATTERNS.items():
            count = 0
            messages = []

            for pattern in patterns:
                matches = re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE)
                for match in matches:
                    count += 1
                    # 获取匹配行作为消息
                    start = text.rfind('\n', 0, match.start()) + 1
                    end = text.find('\n', match.end())
                    if end == -1:
                        end = len(text)
                    line = text[start:end].strip()
                    if line and line not in messages:
                        messages.append(line[:100])

            if count > 0:
                status_counts[status_type] = count
                status_messages[status_type] = messages

        if not status_counts:
            return None

        # 确定主要状态（按优先级：failure > warning > success > info）
        priority = [StatusType.FAILURE, StatusType.WARNING, StatusType.SUCCESS, StatusType.INFO]
        for st in priority:
            if st in status_counts:
                return StatusInfo(
                    status_type=st,
                    count=status_counts[st],
                    messages=status_messages[st][:5]
                )

        return None

    def _parse_interactive(self, text: str) -> Optional[InteractiveInfo]:
        """解析交互信息"""
        # 只检查最后几行（交互提示通常在末尾）
        lines = text.strip().split('\n')
        check_text = '\n'.join(lines[-5:]) if len(lines) > 5 else text

        for interactive_type, patterns in self.INTERACTIVE_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, check_text, re.IGNORECASE | re.MULTILINE)
                if match:
                    info = InteractiveInfo(interactive_type=interactive_type)

                    # 提取提示文本
                    line_start = check_text.rfind('\n', 0, match.start()) + 1
                    info.prompt = check_text[line_start:].split('\n')[0].strip()

                    # 提取选项（针对选择类型）
                    if interactive_type == InteractiveType.SELECT:
                        options = re.findall(r'^\s*(?:\d+[.)]\s*|\[\d+\]\s*)(.+)$',
                                           check_text, re.MULTILINE)
                        info.options = options[:10]

                    # 提取默认值
                    default_match = re.search(r'\[([^\]]+)\]|\(default[:\s]+([^)]+)\)',
                                            info.prompt, re.IGNORECASE)
                    if default_match:
                        info.default_value = default_match.group(1) or default_match.group(2)

                    return info

        return None

    def get_status_summary(self, text: str) -> str:
        """获取状态摘要（用于快速判断）"""
        result = self.parse(text)
        return result.to_context_string()

    def is_waiting_input(self, text: str) -> bool:
        """判断是否在等待用户输入"""
        result = self.parse(text)
        return (result.interactive is not None and
                result.interactive.interactive_type != InteractiveType.NONE)

    def get_progress_percentage(self, text: str) -> Optional[float]:
        """获取进度百分比"""
        result = self.parse(text)
        if result.progress:
            return result.progress.percentage
        return None


# ==================== CLI 入口 ====================

def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Claude Monitor Output Recognizer',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # parse
    p_parse = subparsers.add_parser('parse', help='Parse output completely')
    p_parse.add_argument('text', nargs='?', help='Text to parse (or read from stdin)')

    # status
    p_status = subparsers.add_parser('status', help='Parse status indicators')
    p_status.add_argument('text', nargs='?', help='Text to parse')

    # progress
    p_progress = subparsers.add_parser('progress', help='Parse progress indicators')
    p_progress.add_argument('text', nargs='?', help='Text to parse')

    # interactive
    p_interactive = subparsers.add_parser('interactive', help='Parse interactive prompts')
    p_interactive.add_argument('text', nargs='?', help='Text to parse')

    # summary
    p_summary = subparsers.add_parser('summary', help='Get output summary for LLM context')
    p_summary.add_argument('text', nargs='?', help='Text to parse')

    args = parser.parse_args(argv)
    recognizer = OutputRecognizer()

    try:
        # 获取输入文本
        text = None
        if hasattr(args, 'text') and args.text:
            text = args.text
        elif args.command:
            text = sys.stdin.read()

        if args.command == 'parse':
            result = recognizer.parse(text or "")
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

        elif args.command == 'status':
            result = recognizer.parse(text or "")
            if result.status:
                print(json.dumps(result.status.to_dict(), indent=2, ensure_ascii=False))
            else:
                print("{}")

        elif args.command == 'progress':
            result = recognizer.parse(text or "")
            if result.progress:
                print(json.dumps(result.progress.to_dict(), indent=2, ensure_ascii=False))
            else:
                print("{}")

        elif args.command == 'interactive':
            result = recognizer.parse(text or "")
            if result.interactive:
                print(json.dumps(result.interactive.to_dict(), indent=2, ensure_ascii=False))
            else:
                print("{}")

        elif args.command == 'summary':
            summary = recognizer.get_status_summary(text or "")
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
