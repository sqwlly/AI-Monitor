#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Input Validation Module
Provides path validation, regex validation, and other input checks
"""

import os
import re
from pathlib import Path
from typing import List, Optional, Tuple


class PathValidator:
    """
    Path validator - Prevent path traversal and unauthorized access

    Features:
    1. Path normalization
    2. Path traversal detection
    3. Protected path checking
    4. Symlink blocking
    """

    # Protected system paths
    PROTECTED_PATH = [
        "/etc/passwd",
        "/etc/shadow",
        "/etc/sudoers",
        "/root/.ssh",
        "/home/*/.ssh",
        "/var/log",
        "/boot",
        "/sys",
        "/proc",
    ]

    # Maximum path length
    MAX_PATH_LENGTH = 4096

    @classmethod
    def is_safe_path(cls, path_str: str, base_dir: Optional[str] = None) -> Tuple[bool, str]:
        """
        Validate if a path is safe to access

        Args:
            path_str: Path string to validate
            base_dir: Optional base directory to restrict access to

        Returns:
            (is_safe, error_message)
        """
        # 1. Check path length
        if len(path_str) > cls.MAX_PATH_LENGTH:
            return False, f"Path too long (max {cls.MAX_PATH_LENGTH})"

        # 2. Normalize the path
        try:
            path = Path(path_str).expanduser().resolve()
        except (OSError, ValueError) as e:
            return False, f"Invalid path: {e}"

        # 3. Check for path traversal components
        if ".." in path_str or "~" in path_str:
            # After expansion, check if still outside base
            if base_dir:
                try:
                    base = Path(base_dir).resolve()
                    if not str(path).startswith(str(base)):
                        return False, "Path outside allowed directory"
                except (OSError, ValueError):
                    return False, "Invalid base directory"

        # 4. Check against protected paths
        path_lower = str(path).lower()
        for protected in cls.PROTECTED_PATH:
            # Convert wildcard patterns
            protected_pattern = protected.replace("*", ".*").lower()
            if re.search(protected_pattern, path_lower):
                return False, f"Access to protected path: {protected}"

        # 5. Block symlinks to sensitive locations
        if path.is_symlink():
            try:
                target = path.readlink()
                target_lower = str(target).lower()
                for protected in cls.PROTECTED_PATH:
                    protected_pattern = protected.replace("*", ".*").lower()
                    if re.search(protected_pattern, target_lower):
                        return False, f"Symlink to protected path: {protected}"
            except (OSError, ValueError):
                return False, "Cannot read symlink"

        # 6. If base_dir specified, ensure path is within it
        if base_dir:
            try:
                base = Path(base_dir).resolve()
                try:
                    path.relative_to(base)
                except ValueError:
                    return False, "Path outside base directory"
            except (OSError, ValueError):
                return False, "Invalid base directory"

        return True, ""

    @classmethod
    def validate_file_access(cls, file_path: str, max_size_mb: int = 5) -> Tuple[bool, str]:
        """
        Validate file access is safe

        Args:
            file_path: Path to file
            max_size_mb: Maximum file size in MB

        Returns:
            (is_safe, error_message)
        """
        # First check path safety
        is_safe, error = cls.is_safe_path(file_path)
        if not is_safe:
            return False, error

        try:
            path = Path(file_path).expanduser().resolve()

            # Check if file exists
            if not path.exists():
                return False, "File does not exist"

            # Check if it's a file (not directory)
            if not path.is_file():
                return False, "Not a file"

            # Check file size
            size_mb = path.stat().st_size / (1024 * 1024)
            if size_mb > max_size_mb:
                return False, f"File too large ({size_mb:.1f}MB > {max_size_mb}MB)"

            return True, ""

        except (OSError, ValueError) as e:
            return False, f"Cannot access file: {e}"


class RegexValidator:
    """
    Regular expression validator - Prevent ReDoS attacks

    Features:
    1. Pattern length limits
    2. Complexity detection
    3. Nested quantifier detection
    """

    # Maximum pattern length
    MAX_PATTERN_LENGTH = 200

    # Maximum nested quantifiers
    MAX_NESTED_QUANTIFIERS = 3

    @classmethod
    def validate(cls, pattern: str) -> Tuple[bool, str]:
        """
        Validate regex pattern is safe

        Args:
            pattern: Regex pattern to validate

        Returns:
            (is_safe, error_message)
        """
        # 1. Check pattern length
        if len(pattern) > cls.MAX_PATTERN_LENGTH:
            return False, f"Pattern too long (max {cls.MAX_PATTERN_LENGTH})"

        # 2. Check for nested quantifiers (more precise pattern)
        # Match things like: *+, ++, {1,}+, but not \w+ or [a-z]+
        # We need to exclude escaped characters and character classes
        # Pattern: quantifier followed immediately by another quantifier
        # But NOT when preceded by a backslash or inside brackets

        # First, remove character classes [...] to avoid false positives
        temp_pattern = re.sub(r'\[[^\]]*\]', '', pattern)

        # Check for nested quantifiers in the remaining pattern
        # Match: quantifier (*, +, ?, {n,m}) followed by another quantifier
        # But not: \w+, \d+, [a-z]+, etc. (these are valid)
        nested_count = len(re.findall(r'([^\\])([*+?]{1})([*+]{1})', temp_pattern))
        if nested_count > cls.MAX_NESTED_QUANTIFIERS:
            return False, f"Too many nested quantifiers ({nested_count})"

        # 3. Check for catastrophic backtracking patterns
        # Pattern: (a+)+, (.*)*, etc.
        if re.search(r'\([.\\*]\+[)]*[\+\*]', pattern):
            return False, "Contains catastrophic backtracking pattern"

        # 4. Try to compile (catch syntax errors)
        try:
            re.compile(pattern)
        except re.error as e:
            return False, f"Invalid regex: {e}"

        return True, ""

    @classmethod
    def is_safe_pattern(cls, pattern: str) -> bool:
        """Quick check if pattern is safe"""
        is_safe, _ = cls.validate(pattern)
        return is_safe
