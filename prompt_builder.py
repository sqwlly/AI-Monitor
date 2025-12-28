#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
动态Prompt组装器 - 按需组装prompt，减少token消耗

根据上下文动态选择需要的prompt模块，避免发送无关内容。
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

PROMPTS_DIR = Path(__file__).parent / "prompts"

# ==================== 核心模块（必须包含） ====================

CORE_RULES = """你是 AI 监工/督导。核心任务是**主动推进开发**，不等待、不犹豫。

## 行为准则
- 看到任务完成 → 立即指出下一个任务
- 看到空闲/等待输入 → 主动给出下一步指令
- 看到错误 → 立即给出诊断命令
- 只有终端正在执行或危险操作时才 WAIT

## 输出规范（必须遵守）
格式：`STAGE=<阶段>; CMD=<命令或WAIT>`
阶段：planning|coding|testing|fixing|refining|reviewing|documenting|release|done|blocked|waiting|unknown
"""

SAFETY_RULES = """## 安全边界
必须 WAIT 的操作：
- git reset --hard / git push --force / rm -rf
- DROP / TRUNCATE 生产数据
- 任何 --force / --hard 且涉及删除的命令
"""

OUTPUT_FORMAT = """## 输出要求
1. 单行纯文本，禁止 Markdown/代码块
2. 格式：STAGE=<阶段>; CMD=<命令或WAIT>
3. 信息不足时输出 WAIT
"""

# ==================== 可选模块 ====================

OPTIONAL_MODULES = {
    # Git相关操作指导
    'git_ops': """## Git 操作指导
- 有未暂存文件 → 建议 git add 并提交
- 有暂存文件 → 建议 git commit
- 代码改动后 → 建议运行测试再提交
- 风险操作 → 新建分支或 git stash 备份
""",

    # 测试阶段指导
    'testing': """## 测试指导
- 测试通过 → 继续下一个功能或提交
- 测试失败 → 分析错误并修复
- 代码改动后 → 主动建议运行测试
""",

    # 错误处理指导
    'error_handling': """## 错误处理
- 同一错误连续3次 → 收集环境信息后暂停
- 死循环（相同输出反复） → WAIT 并标注需人工介入
- 编译错误 → 定位具体文件和行号
""",

    # 反重复机制
    'anti_repeat': """## 反重复机制
- same_response_count >= 2 → 必须尝试不同方法或 WAIT
- same_response_count >= 3 → 强制 WAIT
- no_output_change_since_last_command = 1 → 禁止重复上次命令
""",

    # 项目状态感知
    'project_awareness': """## 项目状态感知
利用 [git]/[todos]/[test] 等信息主动决策：
- 有未提交改动 → 建议提交
- 有 TODO → 建议处理
- 有失败测试 → 建议修复
""",
}

# ==================== 上下文检测器 ====================

class ContextDetector:
    """检测上下文特征，决定需要哪些prompt模块"""

    def __init__(self):
        self.patterns = {
            'git_ops': [
                r'\[git\]',
                r'git\s+(add|commit|push|pull|merge|rebase)',
                r'unstaged=\d+',
                r'staged=\d+',
            ],
            'testing': [
                r'\[test\]',
                r'pytest|jest|npm\s+test|cargo\s+test',
                r'test.*passed|test.*failed',
                r'PASS|FAIL',
            ],
            'error_handling': [
                r'error|Error|ERROR',
                r'exception|Exception',
                r'failed|Failed|FAILED',
                r'panic|Panic',
            ],
            'anti_repeat': [
                r'same_response_count\s*[=:]\s*[1-9]',
                r'no_output_change_since_last_command\s*[=:]\s*1',
                r'consecutive_wait_count\s*[=:]\s*[2-9]',
            ],
            'project_awareness': [
                r'\[project\]',
                r'\[todos\]',
                r'TODO:',
            ],
        }

    def detect(self, context: str) -> Set[str]:
        """检测上下文中需要的模块"""
        needed = set()
        for module, patterns in self.patterns.items():
            for pattern in patterns:
                if re.search(pattern, context, re.IGNORECASE):
                    needed.add(module)
                    break
        return needed


class DynamicPromptBuilder:
    """动态Prompt组装器"""

    def __init__(self):
        self.detector = ContextDetector()
        self._role_cache: Dict[str, str] = {}

    def build(self, role: str, context: str = "", force_modules: List[str] = None) -> str:
        """
        构建动态prompt

        Args:
            role: 角色名称
            context: 当前上下文（用于检测需要的模块）
            force_modules: 强制包含的模块列表

        Returns:
            组装后的prompt
        """
        parts = []

        # 1. 核心规则（必须）
        parts.append(CORE_RULES)
        parts.append(SAFETY_RULES)
        parts.append(OUTPUT_FORMAT)

        # 2. 检测需要的可选模块
        needed_modules = self.detector.detect(context)
        if force_modules:
            needed_modules.update(force_modules)

        # 3. 添加可选模块
        for module in needed_modules:
            if module in OPTIONAL_MODULES:
                parts.append(OPTIONAL_MODULES[module])

        # 4. 添加角色特定内容（如果有）
        role_specific = self._get_role_specific(role)
        if role_specific:
            parts.append(role_specific)

        return '\n\n'.join(parts)

    def _get_role_specific(self, role: str) -> Optional[str]:
        """获取角色特定的prompt片段"""
        if role in self._role_cache:
            return self._role_cache[role]

        role_file = PROMPTS_DIR / f"{role}.txt"
        if not role_file.exists():
            return None

        try:
            content = role_file.read_text(encoding='utf-8')
            # 提取角色特定部分（跳过通用规则）
            # 只保留角色独特的内容
            specific = self._extract_role_specific(content, role)
            self._role_cache[role] = specific
            return specific
        except Exception:
            return None

    def _extract_role_specific(self, content: str, role: str) -> str:
        """提取角色特定内容，去除与核心规则重复的部分"""
        lines = content.split('\n')
        result = []
        skip_section = False

        # 要跳过的通用段落标题
        skip_headers = [
            '## 行为准则', '## 输出规范', '## 安全边界',
            '## 强制 WAIT', '## 反重复机制', '## 失败升级',
        ]

        for line in lines:
            # 检查是否进入要跳过的段落
            if any(h in line for h in skip_headers):
                skip_section = True
                continue

            # 检查是否进入新段落（退出跳过模式）
            if line.startswith('## ') and not any(h in line for h in skip_headers):
                skip_section = False

            if not skip_section:
                result.append(line)

        # 清理结果
        text = '\n'.join(result).strip()
        # 去除版本号和角色声明行
        text = re.sub(r'^#\s*Version:.*\n', '', text)
        text = re.sub(r'^#\s*Role:.*\n', '', text)

        return text if len(text) > 50 else None

    def estimate_tokens(self, prompt: str) -> int:
        """估算token数量"""
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', prompt))
        other_chars = len(prompt) - chinese_chars
        return int(chinese_chars / 1.5 + other_chars / 4)


# ==================== 全局实例 ====================

_prompt_builder = None


def get_prompt_builder() -> DynamicPromptBuilder:
    global _prompt_builder
    if _prompt_builder is None:
        _prompt_builder = DynamicPromptBuilder()
    return _prompt_builder


def build_dynamic_prompt(role: str, context: str = "", force_modules: List[str] = None) -> str:
    """便捷函数：构建动态prompt"""
    return get_prompt_builder().build(role, context, force_modules)


# ==================== CLI ====================

def main():
    import sys

    if len(sys.argv) < 2:
        print("Usage: python prompt_builder.py <role> [context]")
        print("Example: python prompt_builder.py monitor '[git] unstaged=3'")
        sys.exit(1)

    role = sys.argv[1]
    context = sys.argv[2] if len(sys.argv) > 2 else ""

    builder = get_prompt_builder()
    prompt = builder.build(role, context)

    print(f"=== Dynamic Prompt for '{role}' ===")
    print(f"Detected modules: {builder.detector.detect(context)}")
    print(f"Estimated tokens: {builder.estimate_tokens(prompt)}")
    print("=" * 40)
    print(prompt)


if __name__ == '__main__':
    main()
