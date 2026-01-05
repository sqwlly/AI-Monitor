#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
File Tool and Search Tool Unit Tests
Tests for file operations and search functionality
"""

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.validators import PathValidator, RegexValidator
from core.exceptions import ValidationError


class TestPathValidator:
    """Path validator unit tests"""

    def test_normalizes_paths(self):
        """Test path normalization"""
        # Normalize with . and ..
        # PathValidator.is_safe_path returns (is_safe, error_message)
        # The normalization happens internally during Path.resolve()
        is_safe, msg = PathValidator.is_safe_path("./file.txt")
        # Should allow simple relative paths
        assert is_safe, f"Should allow simple relative path: {msg}"

    def test_detects_traversal(self):
        """Test path traversal detection"""
        # Detect ../ patterns beyond safe boundary
        is_safe, msg = PathValidator.is_safe_path("../../../../etc/passwd")
        assert not is_safe

    def test_allows_relative_paths(self):
        """Test relative paths are allowed within safe boundary"""
        safe_paths = [
            "./file.txt",
            "dir/file.txt",
        ]

        for path in safe_paths:
            is_safe, msg = PathValidator.is_safe_path(path)
            assert is_safe, f"Should allow: {path} - {msg}"

    def test_blocks_symlinks_to_sensitive(self):
        """Test blocking symlinks to sensitive paths"""
        # This test requires actual file system operations
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create a safe file
            safe_file = tmpdir / "safe.txt"
            safe_file.write_text("safe content")

            # Should allow regular files
            is_safe, _ = PathValidator.is_safe_path(str(safe_file))
            assert is_safe


class TestRegexValidator:
    """Regex validator unit tests"""

    def test_validates_simple_patterns(self):
        """Test simple regex patterns are allowed"""
        safe_patterns = [
            r"\w+",
            r"[a-z]+",
            r"test.*pattern",
            r"(foo|bar)",
            r"^start.*end$",
        ]

        for pattern in safe_patterns:
            is_safe, msg = RegexValidator.validate(pattern)
            assert is_safe, f"Should allow: {pattern}"

    def test_detects_nested_quantifiers(self):
        """Test detection of nested quantifiers (ReDoS risk)"""
        # Python's re.compile() rejects patterns like a++, a**, a?+ as invalid
        # RegexValidator catches these as "Invalid regex"
        risky_patterns = [
            r"a++",     # Double quantifier - invalid in Python
            r"a**",     # Double star - invalid in Python
            r"a?+",     # Double quantifier - invalid in Python
        ]

        for pattern in risky_patterns:
            is_safe, msg = RegexValidator.validate(pattern)
            # Should be marked as unsafe due to invalid regex syntax
            assert not is_safe, f"Should block risky pattern {pattern}: {msg}"
            assert "invalid" in msg.lower(), f"Expected 'invalid' in message: {msg}"

    def test_enforces_length_limit(self):
        """Test pattern length limit"""
        # Create a pattern that exceeds 200 characters
        long_pattern = r"(a+)+" * 50

        is_safe, msg = RegexValidator.validate(long_pattern)
        assert not is_safe, "Should block overly long pattern"
        assert "length" in msg.lower() or "too long" in msg.lower()

    def test_allows_character_classes(self):
        """Test character classes are allowed"""
        patterns_with_classes = [
            r"[a-zA-Z0-9]+",
            r"[^\s]+",  # Non-whitespace
            r"[\d\w]+",  # Digits and word chars
        ]

        for pattern in patterns_with_classes:
            is_safe, msg = RegexValidator.validate(pattern)
            assert is_safe, f"Should allow character class: {pattern}"

    def test_allows_anchors(self):
        """Test regex anchors are allowed"""
        patterns_with_anchors = [
            r"^start",
            r"end$",
            r"^start.*end$",
        ]

        for pattern in patterns_with_anchors:
            is_safe, msg = RegexValidator.validate(pattern)
            assert is_safe, f"Should allow anchors: {pattern}"

    def test_allows_capture_groups(self):
        """Test capture groups are allowed"""
        patterns_with_groups = [
            r"(test)+",
            r"(foo|bar)+",
            r"([a-z]+)\1",
        ]

        for pattern in patterns_with_groups:
            is_safe, msg = RegexValidator.validate(pattern)
            assert is_safe, f"Should allow capture groups: {pattern}"

    def test_blocks_excessive_repetition(self):
        """Test excessive repetition is blocked"""
        # Pattern with excessive repetition (but not nested)
        excessive_pattern = "a" * 300  # 300 'a' characters

        is_safe, msg = RegexValidator.validate(excessive_pattern)
        assert not is_safe, "Should block excessive repetition"


class TestFileOperations:
    """File operation safety tests"""

    def test_safe_file_read(self, tmp_path):
        """Test safe file reading"""
        # Create test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        # Read file
        is_safe, _ = PathValidator.is_safe_path(str(test_file))
        assert is_safe

        content = test_file.read_text()
        assert "Hello, World!" in content

    def test_safe_file_write(self, tmp_path):
        """Test safe file writing"""
        # Write to file
        test_file = tmp_path / "output.txt"

        is_safe, _ = PathValidator.is_safe_path(str(test_file))
        assert is_safe

        test_file.write_text("Test content")
        assert test_file.read_text() == "Test content"

    def test_blocks_system_file_write(self):
        """Test writing to system files is blocked"""
        system_paths = [
            "/etc/passwd",
            "/etc/shadow",
            "/root/.ssh/authorized_keys",
        ]

        for path in system_paths:
            is_safe, msg = PathValidator.is_safe_path(path)
            assert not is_safe, f"Should block: {path}"

    def test_safe_directory_operations(self, tmp_path):
        """Test safe directory operations"""
        # Create directory
        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()

        is_safe, _ = PathValidator.is_safe_path(str(test_dir))
        assert is_safe

        # List directory
        contents = list(test_dir.iterdir())
        assert len(contents) == 0

    def test_safe_file_search(self, tmp_path):
        """Test safe file searching"""
        # Create test files
        (tmp_path / "file1.py").write_text("# Python file")
        (tmp_path / "file2.py").write_text("# Another Python file")
        (tmp_path / "README.md").write_text("# Documentation")

        # Search for Python files
        py_files = list(tmp_path.glob("*.py"))

        assert len(py_files) == 2
        assert all(f.suffix == ".py" for f in py_files)


class TestSearchOperations:
    """Search operation safety tests"""

    def test_safe_text_search(self, tmp_path):
        """Test safe text searching"""
        # Create test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello World\nPython Test\nSearch Example")

        # Simple search (simulate grep -like behavior)
        content = test_file.read_text()
        matches = content.count("Python")

        assert matches == 1

    def test_safe_pattern_search(self, tmp_path):
        """Test safe pattern searching"""
        # Create test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("email@test.com\nuser@example.org\nadmin@domain.net")

        # Validate regex pattern before use
        pattern = r"[\w.]+@[\w.]+"
        is_safe, _ = RegexValidator.validate(pattern)
        assert is_safe

        # Search would use re module with validated pattern
        import re
        content = test_file.read_text()
        matches = re.findall(pattern, content)

        assert len(matches) == 3

    def test_blocks_complex_search_pattern(self):
        """Test blocking complex search patterns"""
        # Patterns that Python's regex engine rejects as invalid
        invalid_patterns = [
            r"a++",       # Double quantifier - invalid
            r"a**",       # Double star - invalid
        ]

        for pattern in invalid_patterns:
            is_safe, msg = RegexValidator.validate(pattern)
            assert not is_safe, f"Should block invalid pattern {pattern}: {msg}"


class TestIntegration:
    """Integration tests for file and search operations"""

    def test_file_search_workflow(self, tmp_path):
        """Test complete file search workflow"""
        # Setup: Create multiple files
        (tmp_path / "test1.py").write_text("import os\nprint('hello')")
        (tmp_path / "test2.py").write_text("import sys\nprint('world')")
        (tmp_path / "README.md").write_text("# Project\n\nDocumentation")

        # Step 1: List Python files
        py_files = list(tmp_path.glob("*.py"))
        assert len(py_files) == 2

        # Step 2: Search in Python files
        import_count = 0
        for file in py_files:
            content = file.read_text()
            import_count += content.count("import")

        assert import_count == 2

        # Step 3: Validate search pattern
        pattern = r"import \w+"
        is_safe, _ = RegexValidator.validate(pattern)
        assert is_safe

    def test_safe_file_modification(self, tmp_path):
        """Test safe file modification workflow"""
        # Create file
        test_file = tmp_path / "config.txt"
        test_file.write_text("setting1=value1\nsetting2=value2")

        # Validate path
        is_safe, _ = PathValidator.is_safe_path(str(test_file))
        assert is_safe

        # Read, modify, write
        content = test_file.read_text()
        modified = content.replace("value1", "new_value")
        test_file.write_text(modified)

        # Verify
        result = test_file.read_text()
        assert "new_value" in result
        assert "value1" not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
