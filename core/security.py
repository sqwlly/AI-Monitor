#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Security Validation Module
Provides command validation and input filtering
"""

import shlex
from typing import List, Optional, Tuple


class CommandValidator:
    """
    Command Validator - Prevent command injection attacks

    Features:
    1. Command whitelist validation
    2. Dangerous pattern detection
    3. Parameter safety validation
    """

    # Allowed command whitelist
    ALLOWED_COMMANDS = {
        "ls", "cat", "grep", "find", "echo", "pwd", "cd",
        "head", "tail", "wc", "sort", "uniq", "cut",
        "date", "whoami", "id", "uname", "df", "du",
        "ps", "top", "htop", "kill", "pgrep",
        "git", "npm", "pip", "python", "python3", "node",
        "mkdir", "touch", "cp", "mv", "rm",
        "file", "stat", "which", "whereis", "type",
        "basename", "dirname", "realpath", "readlink",
        "sed", "awk", "tr", "xargs",
    }

    # Dangerous pattern blacklist
    BLOCKED_PATTERNS = [
        "rm -rf",
        "rm -r /",
        "dd if=",
        "> /dev/",
        "mkfs",
        "fdisk",
        "format",
        ":(){",
        "fork bomb",
        "chmod 000",
        "chown root",
        "wget",
        "curl",
        "nc -l",
        "netcat",
        "/etc/passwd",
        "/etc/shadow",
        "../../",
        "..\\..\\",
    ]

    # Dangerous operators
    DANGEROUS_OPERATORS = ["&&", "||", ";", "|", "&", ">"]

    @classmethod
    def validate(cls, command: str) -> Tuple[bool, str]:
        """
        Validate command safety

        Args:
            command: Command string to validate

        Returns:
            (is_safe, error_message)
        """
        # 1. Check command length
        if len(command) > 1000:
            return False, "Command too long (max 1000 chars)"

        # 2. Check dangerous patterns
        cmd_lower = command.lower()
        for pattern in cls.BLOCKED_PATTERNS:
            if pattern.lower() in cmd_lower:
                return False, f"Contains dangerous pattern: {pattern}"

        # 3. Try to parse command, check syntax
        try:
            # Use shlex for safe parsing
            parts = shlex.split(command)
            if not parts:
                return False, "Empty command"

            # Check if command is in whitelist
            cmd_name = parts[0]
            if cmd_name not in cls.ALLOWED_COMMANDS:
                return False, f"Command '{cmd_name}' not in allowed list"

            # 4. Check for pipes or redirects (limited pipes allowed)
            if "|" in command:
                # Check commands after pipe
                pipe_parts = command.split("|")
                if len(pipe_parts) > 3:  # Max 2 pipes
                    return False, "Too many pipe levels"

            # 5. Check parameter safety
            for i, part in enumerate(parts[1:], 1):
                # Check if parameter contains dangerous chars
                if cls._has_dangerous_chars(part):
                    return False, f"Parameter {i} contains dangerous chars: {part}"

        except ValueError as e:
            return False, f"Command syntax error: {e}"

        return True, ""

    @classmethod
    def _has_dangerous_chars(cls, arg: str) -> bool:
        """Check if argument contains dangerous characters"""
        dangerous_chars = ["$", "`", "\\", "\n", "\r", "\x00"]
        return any(char in arg for char in dangerous_chars)

    @classmethod
    def parse_command(cls, command: str) -> Optional[List[str]]:
        """
        Safely parse command into argument list

        Args:
            command: Command string

        Returns:
            Argument list, or None if unsafe
        """
        is_safe, error = cls.validate(command)
        if not is_safe:
            return None

        try:
            return shlex.split(command)
        except ValueError:
            return None

    @classmethod
    def is_safe_command(cls, command: str) -> bool:
        """Quick check if command is safe"""
        is_safe, _ = cls.validate(command)
        return is_safe
